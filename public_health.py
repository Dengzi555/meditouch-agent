"""MedlinePlus fallback for patient education, using only controlled topic queries."""
import os
import re
import time
from functools import lru_cache
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import urlopen
from xml.etree import ElementTree

from query_plan import INTENTS, requested_intents, symptom_relation, understand_question
from model_gateway import ModelError, ModelGateway
from public_evidence_cache import read_snapshot, write_snapshot
from public_health_providers import provider_answers


# Map user language to API topic names. No raw question or patient fields leave the app.
TOPICS = {
    r'(?<![A-Za-z])(?i:HIV)(?![A-Za-z])|人类免疫缺陷病毒': ('HIV', 'HIV'),
    '普通感冒|感冒': ('Common Cold', '感冒'),
    '流感': ('Flu', '流感'),
    '发热|高烧|高热': ('Fever', '发热'),
    '咳嗽': ('Cough', '咳嗽'),
    '咽痛|咽喉痛': ('Sore Throat', '咽痛'),
    '偏头痛': ('Migraine', '偏头痛'),
    '(?<!偏)头痛|头疼': ('Headache', '头痛'),
    '腹泻': ('Diarrhea', '腹泻'),
    '便秘': ('Constipation', '便秘'),
    '胃灼热|烧心': ('Heartburn', '烧心'),
    '湿疹': ('Eczema', '湿疹'),
    '过敏性鼻炎|花粉过敏': ('Hay Fever', '过敏性鼻炎'),
}
SPECIAL_POPULATION = re.compile(r'儿童|小孩|孩子|宝宝|婴儿|幼儿|孕|老人|高龄|'
                                r'(?<!\d)(?:[0-9]|1[0-7]|6[5-9]|[7-9]\d|1\d\d)\s*岁')
CARE = re.compile(r'\b(?:rest|drink|fluids|humidifier|saline|lifestyle changes|self-care)\b', re.I)
TRIAGE = re.compile(r'\b(?:medical help|medical attention|see a doctor|health care provider)\b', re.I)
URGENT = re.compile(r'\b(?:right away|immediately|emergency)\b', re.I)
MEDICATION = re.compile(r'\b(?:antibiotics?|dosage|medicines?|medications?|drugs?|pain relievers?)\b', re.I)
SYMPTOM_LIST = re.compile(
    r'\b(?:symptoms|signs)\b[^.!?;]{0,100}\b(?:include|are|such as)\s*:\s*', re.I
)
# These are shared symptom concepts. A card requires an applicable source sentence.
SIGNALS = (
    ('head_injury', r'(?:after a blow to your head|after a head injury)', '近期是否撞到过头部？'),
    ('breathing', r'(?:difficulty breathing|trouble breathing|shortness of breath)', '现在有没有呼吸困难？'),
    ('blood', r'(?:blood in (?:your |the )?stool|bloody stools)', '有没有看到便血？'),
)


class SummaryParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.in_symptom_list = False

    def handle_data(self, data):
        if not self.in_symptom_list or data.strip():
            self.parts.append(data.replace('\n', ' ') if self.in_symptom_list else data)

    def handle_starttag(self, tag, attrs):
        if tag in {'ul', 'ol'}:
            previous = ''.join(self.parts).rstrip().rsplit('\n', 1)[-1]
            if SYMPTOM_LIST.search(previous) and previous.endswith(':') and not TRIAGE.search(previous):
                self.parts[:] = [''.join(self.parts).rstrip()]
                self.in_symptom_list = True
        if self.in_symptom_list:
            if tag == 'li':
                self.parts.append(' ')
            return
        if tag in {'p', 'li', 'br'}:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if self.in_symptom_list and tag == 'li':
            self.parts.append(';')
        if self.in_symptom_list and tag in {'ul', 'ol'}:
            self.in_symptom_list = False
            self.parts.append('\n')


@lru_cache(maxsize=128)
def fetch_topic(title, cache_window):
    params = urlencode({'db': 'healthTopics', 'term': f'title:"{title}"',
                        'rettype': 'topic', 'retmax': 5, 'tool': 'meditouch'})
    with urlopen(f'https://wsearch.nlm.nih.gov/ws/query?{params}', timeout=4) as response:
        body = response.read(1_000_001)
    if len(body) > 1_000_000:
        raise ValueError('oversized public knowledge response')
    root = ElementTree.fromstring(body)
    if root.tag != 'nlmSearchResult' or root.find('.//error') is not None:
        raise ValueError('invalid public knowledge response')
    for topic in root.findall('.//health-topic'):
        url = topic.get('url', '')
        parsed = urlsplit(url)
        if (topic.get('title', '').casefold() != title.casefold()
                or parsed.scheme != 'https' or parsed.netloc != 'medlineplus.gov'):
            continue
        parser = SummaryParser()
        parser.feed(topic.findtext('full-summary', ''))
        blocks = tuple(' '.join(line.split()) for line in ''.join(parser.parts).splitlines())
        return url, tuple(block for block in blocks if 35 <= len(block) <= 1000)
    return '', ()


OVERVIEW = r'是什么|什么情况|啥情况|怎么回事|咋回事|有哪些症状|科普|你知道吗|你了解吗'
COVID_SYMPTOMS_URL = 'https://www.cdc.gov/covid/signs-symptoms/index.html'
COVID_SYMPTOMS = (
    '新冠可能出现发热或畏寒、咳嗽、咽痛、鼻塞或流鼻涕，也可能伴有乏力、头痛、'
    '肌肉酸痛、新出现的嗅觉或味觉减退，以及恶心、呕吐或腹泻。'
    '新冠和流感的症状有重叠，仅凭症状不能区分。'
)
DYNAMIC_OVERVIEW = re.compile(
    r'(?:请问\s*)?(?:什么是\s*(?P<prefix>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z -]{1,48})|'
    r'(?P<suffix>[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z -]{1,48})\s*(?:是什么|你知道吗|你了解吗))'
)
ENGLISH_TOPIC = re.compile(r"[A-Za-z][A-Za-z0-9' -]{1,63}")


def topic_model_reply(instruction, content):
    reply = ModelGateway(timeout=6).complete([
        {'role': 'system', 'content': instruction},
        {'role': 'user', 'content': content},
    ])
    value = reply.get('content')
    return value.strip() if isinstance(value, str) and reply.get('role') == 'assistant' else ''


def fetch_with_retry(title, cache_window, trace):
    """Retry one transient failure within the public HTTP lookup budget."""
    key = ('MedlinePlus', title.casefold())
    cached = read_snapshot(key, trace)
    if cached is not None:
        return cached
    deadline = time.monotonic() + 8.5
    delay = 0.2
    for attempt in (1, 2):
        started = time.monotonic()
        try:
            result = fetch_topic(title, cache_window)
        except (OSError, ValueError, ElementTree.ParseError) as exc:
            if isinstance(exc, HTTPError):
                reason = f'http_{exc.code}'
                has_retry_after = bool(exc.headers and exc.headers.get('Retry-After'))
                retryable = exc.code in (408, 500, 502, 503, 504) and not has_retry_after
            else:
                cause = exc.reason if isinstance(exc, URLError) else exc
                if isinstance(cause, TimeoutError):
                    reason = 'timeout'
                elif isinstance(exc, OSError):
                    reason = 'network_error'
                else:
                    reason = 'invalid_response'
                retryable = reason in ('timeout', 'network_error')
            retry = retryable and attempt == 1 and time.monotonic() + delay + 4 <= deadline
            trace.append({'node': 'public_fetch', 'status': 'error', 'reason': reason,
                          'attempt': attempt, 'retrying': retry,
                          'latency_ms': round((time.monotonic() - started) * 1000)})
            if not retry:
                raise
            time.sleep(delay)
        else:
            if result[0] and result[1]:
                write_snapshot(key, result)
            trace.append({'node': 'public_fetch', 'status': 'ok' if result[1] else 'no_match',
                          'attempt': attempt, 'latency_ms': round((time.monotonic() - started) * 1000)})
            return result


def discover_topic(question, cache_window, trace):
    match = DYNAMIC_OVERVIEW.fullmatch(question.strip(' ？?。！!'))
    if not match:
        return None
    concept = (match['prefix'] or match['suffix']).strip()
    if re.search(r'我|患者|病人|没有|不确定|和|或者|是否|\b(?:and|or)\b', concept, re.I):
        return None
    english = bool(ENGLISH_TOPIC.fullmatch(concept))
    title = concept if english else topic_model_reply(
        '把用户提供的单一医疗概念翻译成 MedlinePlus 英文主题标题。保留概念的具体范围。'
        '只输出一个英文标题；不确定、不是医疗概念或包含多个概念时，只输出 UNKNOWN。',
        concept,
    )
    if title.upper() == 'UNKNOWN' or not ENGLISH_TOPIC.fullmatch(title):
        return None
    url, blocks = fetch_with_retry(title, cache_window, trace)
    if not blocks:
        return None
    if not english and topic_model_reply(
        '判断中文医疗概念与下面英文官方主题及原文是否明确指同一概念。'
        '如果仅相关、范围不同、有歧义或无法确定，只输出 NO。明确一致时只输出 YES。',
        f'中文概念：{concept}\n主题：{title}\n原文：{blocks[0]}',
    ).upper() != 'YES':
        return None
    return title, concept, url, blocks


def public_fallback(result, question, context, route_kind, previous, *, plan=None):
    if os.getenv('MED_AGENT_PUBLIC_KNOWLEDGE', '1') != '1':
        return result
    status = result['status']
    if status not in {'insufficient', 'answered', 'model_error', 'tool_error'}:
        return result
    plan = plan or understand_question(question, context)
    normalized = plan.normalized
    topics = [topic for pattern, topic in TOPICS.items()
              if plan.matches_topic(pattern)]
    if len(topics) > 1:
        trace = [*result.get('trace', []), {'node': 'public_knowledge', 'status': 'skipped',
                                          'reason': 'ambiguous_topic'}]
        return dict(result, trace=trace)
    if (SPECIAL_POPULATION.search(question + ' ' + context.get('population', ''))
            or re.search(r'合并|同时|肾病|肝病|基础病|怀孕|哺乳', question)
            or context.get('risk_factors')):
        return result
    intents = set(requested_intents(question))
    if not intents and not (re.search(OVERVIEW, question)
                            or (topics and normalized.strip('？?。！! ') == topics[0][1])
                            or DYNAMIC_OVERVIEW.fullmatch(question.strip(' ？?。！!'))):
        return result
    sections = result.get('answer_sections') or []
    covered = {key for key, (label, _, _) in INTENTS.items()
               if any(section['title'] == label and section.get('has_evidence') for section in sections)}
    needs = (intents or {'overview'}) - covered
    relation = symptom_relation(question)
    if (route_kind == 'education' and needs == {'symptoms'} and plan.matches_topic(r'新冠')
            and (not relation or relation[0] == '新冠' and relation[1] in COVID_SYMPTOMS)):
        source_id = 'public-cdc-covid-symptoms'
        answer = f'新冠可能出现{relation[1]}。{COVID_SYMPTOMS}' if relation else COVID_SYMPTOMS
        section = {'title': INTENTS['symptoms'][0], 'intent': 'symptoms', 'has_evidence': True,
                   'excerpts': [{'quote': answer, 'evidence_id': source_id}]}
        source = {'id': source_id, 'title': 'Symptoms of COVID-19', 'publisher': '美国 CDC',
                  'source_url': COVID_SYMPTOMS_URL}
        trace = [*result.get('trace', []), {'node': 'public_knowledge', 'status': 'used',
                                         'source': 'CDC', 'topic': 'COVID-19 symptoms'}]
        supported = [section for section in sections if section.get('has_evidence')]
        return dict(result, status='guidance', answer=answer,
                    answer_sections=[*supported, section], sources=[*result.get('sources', []), source],
                    trace=trace, error=None)
    if 'care' in needs and re.search(r'怎么办|咋办|怎么处理', question):
        needs.add('triage')
    trace = list(result.get('trace', []))
    cache_window = int(time.time()) // 43_200
    if topics and route_kind == 'education' and not relation:
        sources = list(result.get('sources') or [])
        additions = []
        for source, blocks in provider_answers(topics[0][0], needs, trace, cache_window):
            source['raw_quote'] = '\n\n'.join(quote for quotes in blocks.values() for quote in quotes)
            sources.append(source)
            for intent, quotes in blocks.items():
                additions.append({
                    'title': INTENTS[intent][0] if intent in INTENTS else '概况',
                    'intent': intent, 'has_evidence': True,
                    'excerpts': [{'quote': quote, 'evidence_id': source['id']} for quote in quotes],
                })
            needs.difference_update(blocks)
        if additions:
            filled = {section['intent'] for section in additions}
            sections = [section for section in sections if section.get('intent') not in filled] + additions
            extracts = [point['quote'] for section in additions for point in section['excerpts']]
            prior = [result['answer']] if status == 'answered' and result.get('answer') else []
            status = 'answered' if status == 'answered' else 'guidance'
            result = dict(result, status=status, sources=sources, answer_sections=sections,
                          answer='\n\n'.join(prior + extracts), trace=trace, error=None)
        elif trace != result.get('trace', []):
            result = dict(result, trace=trace)
    if not needs.intersection({'care', 'triage', 'overview', 'symptoms'}):
        if needs:
            trace.append({'node': 'public_knowledge', 'status': 'skipped', 'reason': 'unsupported_intent'})
            return dict(result, trace=trace)
        return result
    if status == 'answered' and not (intents - covered):
        return result
    try:
        if topics:
            title, label = topics[0]
            url, blocks = fetch_with_retry(title, cache_window, trace)
        else:
            match = discover_topic(question, cache_window, trace) if route_kind == 'education' and not intents else None
            if not match:
                trace.append({'node': 'public_knowledge', 'status': 'skipped', 'reason': 'no_verified_topic'})
                return dict(result, trace=trace)
            title, label, url, blocks = match
    except (OSError, ValueError, ElementTree.ParseError, ModelError):
        trace.append({'node': 'public_knowledge', 'status': 'unavailable'})
        if status == 'insufficient':
            return dict(result, trace=trace, answer='本地资料不足，公开健康资料暂时无法查询。请稍后重试。')
        return dict(result, trace=trace)

    triage = next((block for block in blocks if TRIAGE.search(block) and URGENT.search(block)), '')
    triage = triage or next((block for block in blocks if TRIAGE.search(block)), '')
    care = next((block for block in blocks if CARE.search(block) and not MEDICATION.search(block)), '')
    overview = next((block for block in blocks if not TRIAGE.search(block) and not MEDICATION.search(block)), '')
    symptoms = next((block for block in blocks
                     if (match := SYMPTOM_LIST.search(block)) and block[match.end():].strip()
                     and not TRIAGE.search(block) and not MEDICATION.search(block)), '')
    if relation:
        # A general symptom list does not answer a specific yes/no symptom.
        symptoms = ''
    candidates = [('care', '日常护理', care), ('triage', '就医指征', triage),
                  ('overview', f'{label}科普', overview), ('symptoms', INTENTS['symptoms'][0], symptoms)]
    source_id = f'public-medlineplus-{title.lower().replace(" ", "-")}'
    additions = [{'title': heading + '（英文原文）', 'intent': intent, 'has_evidence': True,
                  'excerpts': [{'quote': block, 'evidence_id': source_id}]}
                 for intent, heading, block in candidates if intent in needs and block]
    symptom_timing = ''
    if title == 'Common Cold' and 'symptoms' in needs and symptoms and all(
        word in symptoms.lower() for word in ('sneezing', 'stuffy nose', 'runny nose', 'sore throat', 'coughing', 'headache')
    ):
        section = next(section for section in additions if section['intent'] == 'symptoms')
        section['title'] = INTENTS['symptoms'][0]
        section['excerpts'] = [
            {'quote': '普通感冒常见的是打喷嚏、鼻塞、流鼻涕，也可能有咽痛、咳嗽和头痛。',
             'highlight': '打喷嚏、鼻塞、流鼻涕', 'evidence_id': source_id},
        ]
        symptom_timing = next((block for block in blocks
                               if re.search(r'start a few days after.*10[-–]14 days', block, re.I)), '')
        if symptom_timing:
            section['excerpts'].append({'quote': '症状通常在感染后几天出现，有些症状可能持续 10–14 天。',
                                        'evidence_id': source_id})
    trace.append({'node': 'public_knowledge', 'status': 'used' if additions else 'insufficient',
                  'topic': title, 'source': 'MedlinePlus', 'match': 'mapped' if topics else 'dynamic'})
    if not additions:
        return dict(result, trace=trace)

    followup = None
    signal_code = None
    if route_kind == 'consultation' and any(key == 'triage' and block and key in needs
                                           for key, _, block in candidates):
        for code, pattern, prompt in SIGNALS:
            sentence = next((part for part in re.split(r'(?<=[.!?])\s+', triage)
                             if re.search(pattern, part, re.I) and URGENT.search(part)), '')
            if not sentence:
                continue
            selected = context.get('public_signal')
            prior = previous or {}
            prior_signal = (prior.get('followup') or {}).get('signal') or prior.get('public_signal_code')
            if selected and prior_signal == code:
                message = {
                    '有': '你补充的情况与下方资料列出的及时就医信号相符，请尽快寻求医疗评估。',
                    '没有': '你表示没有这项情况。本轮少了一项已询问的就医信号，仍无法仅凭这一点判断病因。',
                    '不确定': '这项情况仍不确定，当前判断范围维持不变。',
                }.get(selected)
                if message:
                    signal_code = code
                    additions.insert(0, {'title': '这次补充改变了什么', 'has_evidence': True,
                                         'excerpts': [{'quote': message, 'evidence_id': source_id}]})
            elif not selected:
                followup = {'field': 'public_signal', 'question': prompt, 'signal': code,
                            'options': ['有', '没有', '不确定'], 'evidence_id': source_id}
            break

    missing = [section for section in sections if not section.get('has_evidence')
               and not any(addition['intent'] == section.get('intent')
                           or addition['title'].split('（', 1)[0] == section['title'] for addition in additions)]
    rendered_titles = {section['title'].split('（', 1)[0] for section in additions + sections}
    missing.extend({'title': INTENTS[intent][0], 'intent': intent, 'has_evidence': False, 'excerpts': []}
                   for intent in requested_intents(question)
                   if INTENTS[intent][0] not in rendered_titles)
    source = {'id': source_id, 'title': title, 'publisher': 'MedlinePlus / U.S. National Library of Medicine',
              'source_url': url, 'raw_quote': '\n\n'.join(dict.fromkeys(
                  [block for intent, _, block in candidates if intent in needs and block]
                  + ([symptom_timing] if symptom_timing else [])))}
    public_answer = '\n\n'.join(point['quote'] for section in additions for point in section['excerpts'])
    answer = '\n\n'.join(filter(None, [result.get('answer') if status in {'answered', 'guidance'} else None, public_answer]))
    return dict(result, status='guidance' if status != 'answered' else status, answer=answer,
                answer_sections=[section for section in sections if section.get('has_evidence')] + additions + missing,
                sources=[*(result.get('sources') or []), source], followup=followup,
                public_signal_code=signal_code, trace=trace, error=None)
