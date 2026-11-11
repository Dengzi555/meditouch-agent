"""Conservative query expansion and evidence checks for common consultation intents."""
import re
from dataclasses import dataclass
from patient_context import NAMED_DRUG


ALIASES = {
    '新冠': ('新型冠状病毒感染', '新冠肺炎', 'COVID-19', 'covid-19'),
    '腹泻': ('拉肚子', '窜稀', '跑肚'),
    '腹痛': ('肚子疼', '肚子痛', '肚子好疼'),
    '发热': ('发烧',),
    '咽痛': ('嗓子疼', '喉咙疼', '喉咙痛'),
    '流涕': ('流鼻涕',),
    '肌肉酸痛': ('浑身酸痛', '全身酸痛'),
    '头发沉': ('头沉重', '脑袋发沉', '脑袋发木', '脑子发木', '头昏沉', '头沉沉的'),
    '畏寒': ('身上发冷', '怕冷', '发冷'),
    '头痛': ('头疼',),
}
ALIAS_TERMS = {alias: term for term, aliases in ALIASES.items() for alias in aliases}
ALIAS_PATTERN = re.compile('|'.join(sorted(ALIAS_TERMS, key=len, reverse=True)))
TOPICS = re.compile('普通感冒|感冒|流感|咳嗽|鼻塞|头痛|高血压|'+ '|'.join(ALIASES))
INTENTS = {
    'symptoms': ('常见症状', r'(?:常见|典型)?症状(?:有|是)?(?:哪些|哪几种|什么|啥)|(?:有哪些|有什么|有啥|有哪些常见)症状|'
                          r'(?:常见|典型)症状|(?:有哪些|有什么)表现',
                 r'(?:症状|表现)(?:主要)?(?:包括|有|为|是|如下)[：:\s]*(?:发热|发烧|畏寒|咳嗽|咽痛|头痛|鼻塞|流涕|腹泻|乏力)'),
    'comparison': ('主要区别', r'有什么区别|有啥区别|怎么区分|如何区分|有哪些区别',
                   r'相比|区别在于|不同之处|相比之下|[与和]普通感冒(?:不同|相比)'),
    'care': ('日常护理', r'怎么办|咋办|怎么处理|怎么缓解|如何缓解|怎么护理|如何护理|在家|护理',
             r'居家.{0,10}(?:监测|观察|护理)|多休息|充分休息|注意休息|补充水分|多喝水|多饮水|生理盐水|口服补液'),
    'medication': ('用药注意事项', r'吃(?:点)?(?:什么|啥|哪种)药|用(?:什么|啥)药|用药|药能.*一起|药.*能一起'
                   rf'|(?:吃|服用|使用)(?:{NAMED_DRUG})',
                   r'避免.{0,16}(?:用药|服用|药物)|(?:药物|用药|药品).{0,14}(?:禁忌|说明书|重复|医生|药师)|(?:医生|药师).{0,16}(?:用药|指导|药物)|抗生素.{0,18}(?:无效|不适用)|复方(?:抗)?感冒(?:药|制剂).{0,30}(?:不良反应|对症)'),
    'contraindications': ('禁忌', r'禁忌|哪些人不能(?:吃|用)|什么人不能(?:吃|用)', r'禁忌|禁用|禁止使用'),
    'adverse_effects': ('不良反应', r'不良反应|副作用', r'不良反应|副作用'),
    'directions': ('说明书用法', r'用法|服用方法', r'用法[与及和]用量|用法[:：]|服用方法'),
    'triage': ('就医指征', r'要不要去医院|要不要就医|什么时候.*(?:就医|就诊|去医院)|需要.*(?:就医|就诊|去医院)|什么情况.*(?:就医|就诊|去医院|住院)',
               r'(?:应|须|需|建议|立即|及时|尽快).{0,8}(?:就医|就诊|急诊|前往医院|去医院)'),
    'prevention': ('预防措施', r'怎么预防|如何预防|怎么防止|如何防止',
                   r'勤洗手|佩戴口罩|开窗通风|接种疫苗'),
    'diet': ('饮食怎么调整', r'饮食|食养|怎么吃少一点盐|怎么(?:少|减)盐|吃饭该注意',
             r'减少食盐|减少钠|限盐|少盐|低盐|清淡饮食'),
}


def normalize_terms(question):
    return ALIAS_PATTERN.sub(lambda match: ALIAS_TERMS[match[0]], question)


CLAUSE_BREAK = re.compile(r'[，,。！？!?；;、\n]|但是|不过|而是|但')
NEGATION = re.compile(r'没有|没(?:有)?|无|未见|否认|不(?:觉得|感觉)')
UNCERTAIN = re.compile(r'不确定|不清楚|不知道|会不会|是不是|如果|假如|假设')
UNMEASURED = re.compile(r'未测|没(?:有)?(?:量|测)|没(?:有)?拿体温计')


def mention_state(clause, start):
    prefix = clause[:start]
    if UNCERTAIN.search(prefix):
        return 'unknown'
    if NEGATION.search(prefix):
        return 'denied'
    return 'reported'


@dataclass(frozen=True)
class QuestionPlan:
    normalized: str
    reported: tuple[str, ...]
    denied: tuple[str, ...]
    unknown: tuple[str, ...]

    def matches_topic(self, pattern):
        return any(mention_state(clause, match.start()) == 'reported'
                   for clause in CLAUSE_BREAK.split(self.normalized)
                   for match in re.finditer(pattern, clause))


def understand_question(question, context):
    normalized = normalize_terms(question)
    symptom_texts = [normalized, *(normalize_terms(context.get(key, ''))
                                   for key in ('symptoms', 'associated_symptoms'))]
    facts = {}
    for value in symptom_texts:
        for clause in CLAUSE_BREAK.split(value):
            for match in TOPICS.finditer(clause):
                term = match[0]
                state = mention_state(clause, match.start())
                facts[term] = state if facts.get(term, state) == state else 'unknown'
    if any(UNMEASURED.search(value) for value in [*symptom_texts, context.get('temperature', '')]):
        facts['体温'] = 'unknown'
    if context.get('fever_status'):
        from patient_context import FEVER_STATUS
        facts['发热'] = dict(zip(FEVER_STATUS, ('reported', 'denied', 'denied', 'unknown'))).get(context['fever_status'], 'unknown')
    return QuestionPlan(normalized, *(tuple(term for term, value in facts.items() if value == state)
                                      for state in ('reported', 'denied', 'unknown')))


SYMPTOM_RELATION = re.compile(
    r'(新冠|流感|普通感冒|感冒|偏头痛|胃肠炎)(?:也|通常|一定|会不会|会|是否|有可能|可能|出现|引起|伴随|有|导致|不会|不){1,5}'
    r'(咳嗽|发热|畏寒|咽痛|头痛|鼻塞|流涕|腹泻|乏力|肌肉酸痛|呕吐|恶心|嗅觉减退|味觉减退)'
)
RELATION_DISEASE = re.compile(r'新冠|流感|普通感冒|感冒|偏头痛|胃肠炎')


def symptom_relation(question):
    match = SYMPTOM_RELATION.search(normalize_terms(question))
    return match.groups() if match else None


def requested_intents(question):
    intents = [key for key, (_, pattern, _) in INTENTS.items() if re.search(pattern, question)]
    if 'symptoms' not in intents and symptom_relation(question):
        intents.append('symptoms')
    if 'care' in intents and not re.search(r'护理|在家|缓解', question):
        # A generic “怎么办” alongside an explicit triage request is not a second task.
        if 'triage' in intents:
            intents.remove('care')
    return intents


def retrieval_queries(question, intents):
    normalized = normalize_terms(question)
    variants = [question, normalized]
    topic_query = normalized
    for intent in intents:
        topic_query = re.sub(INTENTS[intent][1], ' ', topic_query)
    topic_query = topic_query.strip(' ，。！？?,')
    for intent in intents:
        variants.append(f'{topic_query or normalized} {INTENTS[intent][0]}')
    return list(dict.fromkeys(variants))


def matching_intents(item, question, intents):
    named = set(re.findall(NAMED_DRUG, question))
    if named and 'medication' in intents:
        text = item.get('raw_quote') or item.get('text') or ''
        if not any(display_points(text, 'medication', question)):
            intents = [intent for intent in intents if intent != 'medication']
    relation = symptom_relation(question) if 'symptoms' in intents else None
    if relation:
        disease, symptom = relation
        raw = normalize_terms(item.get('raw_quote') or item.get('text') or '')
        title = normalize_terms(item.get('source', {}).get('title') or '')
        if not any(symptom in clause and (disease in clause or disease in title)
                   and not (set(RELATION_DISEASE.findall(clause)) - {disease})
                   and re.search(r'症状|表现|伴有|出现', clause)
                   and not re.search(r'监测|加重|观察', clause)
                   and (not ordinary_cold_query(question) or ordinary_cold_query(clause))
                   for clause in re.split(r'[。；\n]', raw)):
            intents = [intent for intent in intents if intent != 'symptoms']
    if 'evidence_intents' in item:
        if item.get('audience') == 'adult' and not ordinary_cold_query(question):
            return []
        return [intent for intent in intents if intent in item['evidence_intents']]
    text = normalize_terms(item.get('raw_quote') or item.get('text') or '')
    title = normalize_terms(item.get('source', {}).get('title') or '')
    normalized_question = normalize_terms(question)
    if re.search(r'腹泻|胃肠炎', normalized_question) and not re.search(r'腹泻|胃肠炎', title):
        return []
    topics = TOPICS.findall(normalized_question)
    if topics and not any(topic in text or topic in title for topic in topics):
        return []
    if ordinary_cold_query(question):
        # Mixed respiratory articles can support care, but their influenza-only
        # passages cannot establish ordinary-cold medication coverage.
        cold_text = '\n'.join(part for part in re.split(r'(?<=[。！？])|\n+', text)
                              if ordinary_cold_query(part))
    else:
        cold_text = text
    product = item.get('medication_product')
    product_matched = isinstance(product, str) and bool(product) and product in question
    return [intent for intent in intents
            if (intent not in ('directions', 'contraindications', 'adverse_effects') or product_matched)
            and re.search(INTENTS[intent][2], cold_text if intent in ('medication', 'symptoms') else text)]


def ordinary_cold_query(text):
    return '感冒' in text and not re.search(r'流感|流行性感冒|甲流|乙流', text)


def display_points(quote, intent, question=''):
    """Select complete short statements and leave the full context in the citation."""
    named = set(re.findall(NAMED_DRUG, question)) if intent == 'medication' else set()
    sentences = re.split(r'(?<=[。！？])\s*|\n+', quote)
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or len(sentence) > 155 or sentence[-1] not in '。！？':
            continue
        if intent in ('medication', 'symptoms') and ordinary_cold_query(question) and not ordinary_cold_query(sentence):
            continue
        if named and (not all(drug in sentence for drug in named)
                      or (re.search(r'发烧|发热|退烧|退热|高热|高烧', question)
                          and not re.search(r'发烧|发热|退烧|退热|高热|高烧', sentence))):
            continue
        match = re.search(INTENTS[intent][2], sentence)
        if match:
            yield {'quote': sentence, 'highlight': match[0]}
