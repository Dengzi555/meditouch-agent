"""Opt-in public health adapters. Only controlled topic names leave the app."""
import json
import os
import re
from functools import lru_cache
from html.parser import HTMLParser
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from public_evidence_cache import read_snapshot, write_snapshot

NHS_BASE = 'https://api.service.nhs.uk/nhs-website-content'
NHS_LOGO = 'https://assets.nhs.uk/nhsuk-cms/images/nhs-attribution.width-510.png'
NHS_TOPICS = {
    'Common Cold': 'common-cold',
    'Flu': 'flu',
    'Migraine': 'migraine',
    'Constipation': 'constipation',
    'Eczema': 'atopic-eczema',
    'Hay Fever': 'hay-fever',
}
SHOWAPI_TOPICS = {
    'Common Cold': '普通感冒',
    'Flu': '流行性感冒',
    'Migraine': '偏头痛',
    'Constipation': '便秘',
    'Hay Fever': '过敏性鼻炎',
}
NHS_ASPECTS = {
    'OverviewHealthAspect': 'overview',
    'SymptomsHealthAspect': 'symptoms',
    'SelfCareHealthAspect': 'care',
    'PreventionHealthAspect': 'prevention',
    'MedicalHelpNonurgentHealthAspect': 'triage',
    'MedicalHelpUrgentHealthAspect': 'triage',
    'MedicalHelpEmergencyHealthAspect': 'triage',
}
SHOWAPI_TAGS = {
    '症状': 'symptoms', '临床表现': 'symptoms', '症状体征': 'symptoms',
    '预防': 'prevention', '预防措施': 'prevention', '预防预后': 'prevention',
}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = False

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style'}:
            self.hidden = True
        if tag in {'p', 'li', 'br', 'h2', 'h3', 'div'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in {'script', 'style'}:
            self.hidden = False
        if tag in {'p', 'li', 'h2', 'h3', 'div'}:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain_text(html):
    parser = PlainText()
    parser.feed(html)
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


PROVIDER_HTTP = build_opener(NoRedirect())


def fetch_json(url, *, headers=None, form=None, json_body=None):
    data = urlencode(form).encode() if form is not None else None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers = {**(headers or {}), 'Content-Type': 'application/json'}
    request = Request(url, data=data, headers={'Accept': 'application/json', **(headers or {})})
    with PROVIDER_HTTP.open(request, timeout=2) as response:
        payload = response.read(1_000_001)
    if len(payload) > 1_000_000:
        raise ValueError('provider response exceeded limit')
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError('expected provider object')
    return value


def records(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


@lru_cache(maxsize=64)
def nhs_topic(slug, key, cache_window):
    path = f'/conditions/{slug}/'
    data = fetch_json(f'{NHS_BASE}{path}?modules=true', headers={'apikey': key})
    blocks = {}
    for module in records(data.get('hasPart')):
        aspect = str(module.get('hasHealthAspect', '')).rsplit('/', 1)[-1]
        intent = NHS_ASPECTS.get(aspect)
        if not intent:
            continue
        parts = [plain_text(part['text']) for part in records(module.get('hasPart'))
                 if part.get('@type') == 'WebPageElement' and isinstance(part.get('text'), str)]
        quote = '\n'.join(filter(None, parts))
        if quote and len(quote) <= 3500:
            heading = module.get('headline')
            if isinstance(heading, str) and heading:
                quote = f'{heading}\n{quote}'
            blocks.setdefault(intent, []).append(quote)
    return {
        'id': f'public-nhs-{slug}', 'title': data.get('name') or slug,
        'publisher': 'NHS website · 英国',
        'source_url': f'https://www.nhs.uk{path}', 'attribution_logo': NHS_LOGO,
    }, blocks


def showapi_body(endpoint, key, form):
    data = fetch_json(f'https://route.showapi.com/{endpoint}?{urlencode({"appKey": key})}', form=form)
    body = data.get('showapi_res_body')
    if isinstance(body, str):
        body = json.loads(body)
    if data.get('showapi_res_code') != 0 or not isinstance(body, dict) or body.get('ret_code') != 0:
        raise ValueError('ShowAPI rejected request')
    return body


@lru_cache(maxsize=64)
def showapi_topic(name, key, detail_enabled, cache_window):
    listing = showapi_body('546-2', key, {'typeId': '', 'subTypeId': '', 'key': name, 'page': 1})
    page = listing.get('pagebean')
    matches = [item for item in records(page.get('contentlist') if isinstance(page, dict) else None)
               if item.get('name') == name]
    if len(matches) != 1:
        return {}, {}
    item = matches[0]
    identifier = item.get('id')
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', identifier):
        return {}, {}
    blocks = {}
    if detail_enabled:
        detail = showapi_body('546-3', key, {'id': identifier}).get('item')
        if isinstance(detail, dict) and detail.get('id') == identifier and detail.get('name') == name:
            item = detail
            for tag in records(item.get('tagList')):
                intent = SHOWAPI_TAGS.get(str(tag.get('name', '')).strip())
                content = tag.get('content')
                if intent and isinstance(content, str) and 0 < len(content.strip()) <= 2500:
                    blocks.setdefault(intent, []).append(plain_text(content))
    summary = item.get('summary')
    if isinstance(summary, str) and 0 < len(summary.strip()) <= 2500:
        blocks['overview'] = [plain_text(summary)]
    endpoint = '3' if detail_enabled else '2'
    return {
        'id': f'public-showapi-{identifier}', 'title': name,
        'publisher': '万维易源 · 疾病数据库',
        'source_url': f'https://www.showapi.com/apiGateway/view/546/{endpoint}',
    }, blocks


def provider_answers(title, needs, trace, cache_window):
    providers = []
    nhs_key = os.getenv('MED_AGENT_NHS_API_KEY')
    showapi_key = os.getenv('MED_AGENT_SHOWAPI_APP_KEY')
    details = os.getenv('MED_AGENT_SHOWAPI_DETAIL') == '1'
    if nhs_key and title in NHS_TOPICS:
        providers.append(('NHS', nhs_topic, (NHS_TOPICS[title], nhs_key, cache_window), set(NHS_ASPECTS.values())))
    if showapi_key and title in SHOWAPI_TOPICS:
        supported = {'overview', *SHOWAPI_TAGS.values()} if details else {'overview'}
        providers.append(('ShowAPI', showapi_topic, (SHOWAPI_TOPICS[title], showapi_key, details, cache_window), supported))
    remaining = set(needs)
    for provider, fetch, args, supported in providers:
        if not remaining.intersection(supported):
            continue
        key = (provider, args[0], details if provider == 'ShowAPI' else None)
        try:
            cached = read_snapshot(key, trace)
            if cached is None:
                source, blocks = fetch(*args)
                if source and any(blocks.values()):
                    write_snapshot(key, (source, blocks))
            else:
                source, blocks = cached
        except (OSError, ValueError):
            trace.append({'node': 'public_knowledge', 'status': 'unavailable', 'source': provider})
            continue
        selected = {intent: quotes for intent, quotes in blocks.items()
                    if intent in remaining and any(quotes)}
        trace.append({'node': 'public_knowledge', 'status': 'used' if selected else 'insufficient',
                      'source': provider, 'topic': title})
        if selected:
            remaining.difference_update(selected)
            yield source, selected
