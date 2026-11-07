"""One bounded search for missing, general health evidence from official websites."""
import os
import re
from hashlib import sha256
from html.parser import HTMLParser
from urllib.parse import urlsplit
from urllib.request import Request

from public_health import SPECIAL_POPULATION, TOPICS
from public_health_providers import PROVIDER_HTTP, fetch_json, records
from query_plan import INTENTS, matching_intents, normalize_terms, requested_intents, symptom_relation

OFFICIAL_SITES = {
    'www.nhc.gov.cn': '国家卫生健康委员会',
    'www.chinacdc.cn': '中国疾病预防控制中心',
    'www.beijing.gov.cn': '北京市人民政府',
}
TOPIC_LABELS = {
    **{pattern: label for pattern, (_, label) in TOPICS.items()},
    r'新冠': '新冠',
    r'高血压': '高血压',
    r'带状疱疹': '带状疱疹',
}
PUBLIC_INTENTS = {'symptoms', 'care', 'prevention', 'diet'}
SEARCH_KEY_ENV = 'MED_AGENT_DEEPSEEK_SEARCH_KEY'


class ArticleParagraphs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self.title = ''
        self.tag = None
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ('title', 'p'):
            self.tag = tag
            self.parts = []

    def handle_data(self, data):
        if self.tag:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag != self.tag:
            return
        block = ' '.join(''.join(self.parts).split())
        if tag == 'title':
            self.title = block
        elif 18 <= len(block) <= 550:
            self.blocks.append(block)
        self.tag = None


def official_host(url):
    if not isinstance(url, str):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme == 'https' and parsed.netloc in OFFICIAL_SITES:
            return parsed.netloc
    except ValueError:
        pass
    return None


def search_links(topic, intents, key):
    sites = ' OR '.join(f'site:{host}' for host in OFFICIAL_SITES)
    labels = ' '.join(INTENTS[intent][0] for intent in intents)
    response = fetch_json('https://api.deepseek.com/v1/tools/search',
                          headers={'Authorization': f'Bearer {key}'},
                          json_body={'query': f'{topic} {labels} {sites}', 'count': 5})
    if response.get('error') or not isinstance(response.get('results'), list):
        raise ValueError('Search provider rejected request')
    return list(dict.fromkeys(item['url'] for item in records(response['results'])
                              if official_host(item.get('url'))))[:2]


def fetch_article(url):
    request = Request(url, headers={'Accept': 'text/html'})
    with PROVIDER_HTTP.open(request, timeout=2) as response:
        if response.headers.get_content_type() not in ('text/html', 'application/xhtml+xml'):
            return '', []
        raw = response.read(1_000_001)
        charset = response.headers.get_content_charset()
    if len(raw) > 1_000_000:
        return '', []
    parser = ArticleParagraphs()
    parser.feed(raw.decode(charset or 'utf-8', errors='replace'))
    return parser.title, parser.blocks


def web_search_fallback(result, question, context, route_kind, *, plan, on_progress=None):
    key = os.getenv(SEARCH_KEY_ENV, '').strip()
    if not key or route_kind != 'education' or context or result['status'] not in {
            'answered', 'guidance', 'insufficient', 'model_error', 'tool_error'}:
        return result
    if SPECIAL_POPULATION.search(question) or symptom_relation(question):
        return result
    topics = {label for pattern, label in TOPIC_LABELS.items() if plan.matches_topic(pattern)}
    if len(topics) != 1:
        return result
    topic = topics.pop()
    sections = list(result.get('answer_sections') or [])
    covered = {section.get('intent') for section in sections if section.get('has_evidence')
               and section.get('excerpts')}
    needs = [intent for intent in requested_intents(question) if intent in PUBLIC_INTENTS - covered]
    if not needs:
        return result
    if on_progress:
        on_progress('web_search')
    trace = list(result.get('trace', []))
    try:
        links = search_links(topic, needs, key)
    except (OSError, ValueError):
        trace.append({'node': 'web_search', 'status': 'unavailable'})
        return dict(result, trace=trace)

    additions = []
    sources = list(result.get('sources') or [])
    for url in links:
        if not needs:
            break
        host = official_host(url)
        if host is None:
            continue
        try:
            title, blocks = fetch_article(url)
        except (OSError, ValueError, LookupError):
            continue
        normalized_title = normalize_terms(title)
        if (topic not in normalized_title or SPECIAL_POPULATION.search(title)
                or (topic == '感冒' and '流感' in normalized_title)):
            continue
        source_id = 'web-' + sha256(url.encode()).hexdigest()[:16]
        quotes = {}
        for block in blocks:
            if topic not in normalize_terms(block) or SPECIAL_POPULATION.search(block):
                continue
            # Keep medication content out of this general-purpose source adapter.
            if re.search(r'用药|药物|抗生素|毫克|剂量|服用|处方|禁忌|抗病毒', block):
                continue
            for intent in matching_intents({'raw_quote': block, 'source': {'title': title}}, question, needs):
                quotes.setdefault(intent, block)
        for intent, block in quotes.items():
            additions.append({'title': INTENTS[intent][0], 'intent': intent, 'has_evidence': True,
                              'excerpts': [{'quote': block, 'evidence_id': source_id}]})
            needs.remove(intent)
        if quotes:
            sources.append({'id': source_id, 'title': title, 'publisher': OFFICIAL_SITES[host],
                            'source_url': url, 'raw_quote': '\n\n'.join(dict.fromkeys(quotes.values()))})

    trace.append({'node': 'web_search', 'status': 'used' if additions else 'insufficient'})
    if not additions:
        return dict(result, trace=trace)
    filled = {section['intent'] for section in additions}
    existing = [section for section in sections if section.get('intent') not in filled]
    supported = result['status'] in ('answered', 'guidance')
    prior = result.get('answer') if supported else ''
    if prior and not any(section.get('excerpts') for section in existing):
        existing.insert(0, {'title': '已有资料', 'excerpts': result.get('citations') or [{'quote': prior}]})
    excerpts = [point['quote'] for section in additions for point in section['excerpts']]
    return dict(result, status=result['status'] if supported else 'guidance', error=None,
                answer='\n\n'.join(filter(None, [prior, *excerpts])),
                answer_sections=[*existing, *additions], sources=sources, trace=trace)
