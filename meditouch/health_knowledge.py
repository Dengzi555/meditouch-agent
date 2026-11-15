"""Bounded public health evidence lookup, followed by local RAG. No patient text leaves via public APIs."""
import os
import re
import time
from hashlib import sha256
from urllib.parse import urlencode, urlsplit
from urllib.request import Request
from xml.etree import ElementTree

from meditouch.evidence_admission import select_evidence
from meditouch.public_evidence_cache import read_snapshot, write_snapshot
from meditouch.public_health_providers import (
    PROVIDER_HTTP, fetch_json, nhs_topic, plain_text, showapi_topic, NHS_LOGO,
)

MAX_SOURCES = 5


def medline_search(term):
    """Use the documented topic XML format; accept only official MedlinePlus result URLs."""
    params = urlencode({'db': 'healthTopics', 'term': term, 'rettype': 'topic', 'retmax': 2, 'tool': 'meditouch'})
    request = Request('https://wsearch.nlm.nih.gov/ws/query?' + params, headers={'Accept': 'application/xml'})
    with PROVIDER_HTTP.open(request, timeout=3) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Unaccepted public XML')
    root = ElementTree.fromstring(raw)
    if root.tag != 'nlmSearchResult' or root.find('.//error') is not None:
        raise ValueError('Invalid public response')
    results = []
    for topic in root.findall('.//health-topic'):
        url = topic.get('url', '')
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'medlineplus.gov':
            continue
        summary = topic.find('full-summary')
        if summary is None:
            continue
        # Some providers escape HTML inside the summary, others return XML children.
        html = (summary.text or '') + ''.join(ElementTree.tostring(child, encoding='unicode') for child in summary)
        text = plain_text(html).strip()[:4000]
        if not text:
            continue
        results.append({'id': 'health-medline-' + sha256(url.encode()).hexdigest()[:12],
            'title': topic.get('title') or term, 'publisher': 'MedlinePlus.gov / 美国国家医学图书馆',
            'url': url, 'text': text, 'kind': 'public'})
    exact = [item for item in results if item['title'].casefold().strip() == term.casefold().strip()]
    return exact or results


def nhs_content(path, key):
    if not re.fullmatch(r'(?:conditions|symptoms|medicines)/[a-z0-9-]+(?:/[a-z0-9-]+){0,2}/?', path):
        return []
    data = fetch_json('https://api.service.nhs.uk/nhs-website-content/' + path.strip('/') + '/?modules=true',
                      headers={'apikey': key})
    chunks = []
    def walk(value):
        if isinstance(value, dict):
            if isinstance(value.get('text'), str):
                chunks.append(plain_text(value['text']))
            for item in value.get('hasPart', []) if isinstance(value.get('hasPart'), list) else []:
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    walk(data)
    text = '\n'.join(chunks).strip()[:6000]
    if not text:
        return []
    return [{'id': 'health-nhs-' + sha256(path.encode()).hexdigest()[:12],
             'title': data.get('name') or path, 'publisher': 'NHS website · 英国',
             'url': 'https://www.nhs.uk/' + path.strip('/') + '/', 'text': text,
             'kind': 'public', 'attribution_logo': NHS_LOGO}]


def lookup_health_evidence(state, *, deadline=None, local_service=None):
    """Public sources first. Cache successful results; check time before each bounded request."""
    sources, trace = [], []
    end = min(deadline or float('inf'), time.monotonic() + 12)
    public_enabled = os.getenv('MED_AGENT_PUBLIC_KNOWLEDGE', '1') == '1'
    for query in state.get('knowledge_queries', [])[:2] if public_enabled else []:
        if time.monotonic() >= end:
            break
        en, zh, path = query.get('en', ''), query.get('zh', ''), query.get('nhs_path', '')
        jobs = []
        if path and os.getenv('MED_AGENT_NHS_API_KEY'):
            jobs.append(('NHS', path, lambda: nhs_content(path, os.environ['MED_AGENT_NHS_API_KEY'])))
        if zh and os.getenv('MED_AGENT_SHOWAPI_APP_KEY'):
            def show():
                source, blocks = showapi_topic(zh, os.environ['MED_AGENT_SHOWAPI_APP_KEY'],
                    os.getenv('MED_AGENT_SHOWAPI_DETAIL') == '1', int(time.time()) // 43_200)
                text = '\n'.join(quote for quotes in blocks.values() for quote in quotes)
                return [{'id': source['id'], 'title': source['title'], 'publisher': source['publisher'],
                         'url': source['source_url'], 'text': text[:4000], 'kind': 'public'}] if source and text else []
            jobs.append(('ShowAPI', zh, show))
        if en and len(en) <= 100:
            jobs.append(('MedlinePlus', en, lambda: medline_search(en)))
        for provider, term, fetch in jobs:
            if time.monotonic() >= end:
                break
            cache_key = ('PharmacyHealth-' + provider, term)
            try:
                cached = read_snapshot(cache_key, trace)
                found = cached if cached is not None else fetch()
                if found and cached is None:
                    write_snapshot(cache_key, found)
                admitted, decisions = select_evidence(found, state)
                trace.extend({'node': 'evidence_admission', **decision} for decision in decisions)
                sources.extend(admitted)
                trace.append({'node': 'health_public', 'provider': provider, 'status': 'cache' if cached else 'ok' if found else 'empty'})
            except Exception:
                trace.append({'node': 'health_public', 'provider': provider, 'status': 'unavailable'})
            if sources:
                break
    # When public evidence is absent, search local text for the original intent, not only drug terms.
    if not sources and time.monotonic() < (deadline or float('inf')):
        try:
            from meditouch.evidence_service import EvidenceService
            response = (local_service or EvidenceService()).search(
                state.get('standalone_query') or state.get('user_query', ''), k=3, corpus='real')
            items, decisions = select_evidence(response.get('items', []), state)
            trace.extend({'node': 'evidence_admission', **decision} for decision in decisions)
            for item in items:
                metadata = item.get('source') or {}
                url = metadata.get('url') or item.get('source_url') or ''
                if item.get('demo') or not item.get('text'):
                    continue
                restrictions = {key: item[key] for key in ('review_status', 'generation_allowed', 'retrieval_only',
                    'demo', 'audience', 'applicability_policy') if key in item}
                sources.append({**restrictions, 'id': 'health-local-' + str(item['id']), 'title': metadata.get('title') or item.get('title') or '本地资料',
                    'publisher': metadata.get('publisher') or '本地医学资料', 'url': url,
                    'text': item['text'][:3000], 'kind': 'local'})
            trace.append({'node': 'health_local', 'status': 'ok' if sources else 'empty'})
        except Exception:
            trace.append({'node': 'health_local', 'status': 'unavailable'})
    return list({source['id']: source for source in sources}.values())[:MAX_SOURCES], trace
