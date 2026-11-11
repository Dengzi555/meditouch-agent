"""Structured user declarations for consultation intake, not a diagnostic system."""
import re

NAMED_DRUG = r'布洛芬|对乙酰氨基酚|奥司他韦|头孢|阿莫西林'
DRUG_REFERENCE = rf'药|{NAMED_DRUG}'
MEDICATION_USE = r'吃|服|用|剂量|几片|多少|叠加|一起|混|过敏'
PRODUCT_SPECIFIC = r'一起吃|一起服|同时(?:吃|用|服)|合用|叠加|混用|重复成分|几片|几粒|多少毫|剂量|用量|规格|成人.{0,4}一半|半片|这一盒|这两种'
PERSONAL_SUBJECT = r'我|本人|自己|家人|父亲|母亲|爸爸|妈妈|孩子|宝宝|爱人|怀孕|孕期|哺乳'

QUESTIONS={
    'public_signal':'是否出现刚才询问的情况？',
    'fever_intent':'你是想问发烧时吃什么食物，还是想了解退烧用药？',
    'population':'请说明年龄段，以及是否为儿童、孕产妇或老人；不清楚可填写“不清楚”。',
    'renal_function':'是否存在肾功能问题？不清楚可填写“不清楚”。',
    'symptoms':'目前主要有哪些不舒服？请保留没有出现的症状等描述。',
    'duration':'这些症状持续多久了，是否有加重或反复？',
    'temperature':'是否测过体温？请填写数值及测量时间；未测可填“未测量”。',
    'temperature_method':'体温是怎样测的（如腋下、口腔或耳温，使用什么体温计）？未测或不清楚可直接说明。',
    'associated_symptoms':'还伴随哪些症状？没有或不清楚也请说明。',
    'risk_factors':'有无基础疾病、免疫问题或其他需要说明的健康情况？不清楚可说明。',
    'drug_name':'这次想咨询哪种药？请提供完整药名和规格；不清楚可以说明。',
    'drug_ingredients':'说明书上标注了哪些有效成分？可以抄写原文；没有说明书或不清楚请直接说明，不要猜测。',
    'medications':'这次已经用了哪些药、每次多少、何时服用？有无已知药物过敏？没有或不清楚也请说明。',
    'label_sodium':'营养标签上钠的数值和单位是什么？请抄写原文，不清楚可说明。',
    'label_basis':'标签以每100克、每100毫升还是每份标注？一份多少？',
    'consumed_amount':'实际吃了多少克、毫升或多少份？不清楚可说明。',
}
FEVER_STATUS = ('目前有发热', '此前发热，目前已退热', '先前记录有误，目前没有发热', '当前情况不确定，需要重新测量')


def fever_conflict(context, question=''):
    if context.get('fever_status') in FEVER_STATUS:
        return False
    if (re.search(r'科普|区别|区分|是什么|为什么|会不会|是否|如果|假如|定义', question)
            and not re.search(r'我现在|我已经|目前', question)):
        question = ''
    text = '、'.join([question, *(context.get(key, '') for key in ('symptoms', 'associated_symptoms'))])
    denied = bool(re.search(r'(?:没有|没|无|不)(?:出现)?(?:发热|发烧)', text))
    positive = re.sub(r'(?:没有|没|无|不)(?:出现)?(?:发热|发烧)', '', text)
    temperature = re.match(r'^\s*(3[89]|4[0-2])(?:\.\d+)?', context.get('temperature', ''))
    temperature = temperature or re.search(r'(?:体温|测到|测出)\s*(?:是|为)?\s*(?:3[89]|4[0-2])(?:\.\d+)?\s*(?:℃|度)', question)
    return denied and (bool(re.search(r'发热|发烧|高热|高烧', positive)) or bool(temperature))


def confirmed_context(context):
    """Project an explicit correction while retaining raw declarations in storage."""
    status = context.get('fever_status')
    if status not in FEVER_STATUS:
        return dict(context)
    resolved = dict(context)
    for key in ('symptoms', 'associated_symptoms'):
        text = re.sub(r'(?:没有|没|无|不)(?:出现)?(?:发热|发烧)|发热|发烧|高热|高烧', '', resolved.get(key, ''))
        resolved[key] = re.sub(r'[、，,；;\s]+', '、', text).strip('、')
    observation = ('发热' if status == FEVER_STATUS[0] else
                   '没有发烧' if status in FEVER_STATUS[1:3] else '发热情况不确定')
    resolved['associated_symptoms'] = '、'.join(filter(None, [resolved.get('associated_symptoms'), observation]))
    if (status != FEVER_STATUS[0] and resolved.get('temperature')
            and not resolved['temperature'].startswith(('此前记录：', '此前填写有误：'))):
        prefix = '此前填写有误' if status == FEVER_STATUS[2] else '此前记录'
        resolved['temperature'] = f'{prefix}：{resolved["temperature"]}；当前体温未确认'
    return {key: value for key, value in resolved.items() if value}
# Kept as the complete supported intake field list for clients and fixtures.
# Required fields are selected per consultation by missing_context below.
# Structured clarification answers are an internal slot, not a user-facing prompt.
CONTEXT_LIMITS = {'clarification': 4000, 'fever_status': 100}
ALLOWED_CONTEXT = set(QUESTIONS) | set(CONTEXT_LIMITS)
INTAKE_FIELDS=['population','symptoms','duration','temperature','temperature_method',
               'associated_symptoms','risk_factors','drug_name','drug_ingredients','medications']


def validate_context(context,keep_empty=False):
    if (not isinstance(context,dict)
            or any(k not in ALLOWED_CONTEXT or not isinstance(v,str)
                   or len(v) > CONTEXT_LIMITS.get(k,300) for k,v in context.items())):
        raise ValueError('补充信息字段无效；文本长度超出限制')
    if context.get('fever_status') and context['fever_status'].strip() not in FEVER_STATUS:
        raise ValueError('请选择有效的当前发热情况')
    return {k:v.strip() for k,v in context.items() if keep_empty or v.strip()}


def resolve_fever_intent(question,context):
    if not re.fullmatch(r'\s*(?:发烧|发热)(?:了|时|的时候)?\s*吃什么(?:好|比较好)?[？?。！!\s]*',question):
        return question
    return {
        '饮食':'发烧时的饮食和补水建议是什么？',
        '用药':'发烧吃什么药？',
    }.get(context.get('fever_intent'))


def normalize_cough_question(question):
    """Map short cough descriptions to the shared consultation and retrieval intent."""
    if re.fullmatch(r'\s*(?:我\s*)?(?:刚(?:开始)?咳(?:嗽)?(?:了)?|咳(?:嗽)?(?:了)?(?:还)?(?:不到一天|一天|半天|[一两二三123]天|几天)|咳嗽(?:了)?\s*(?:怎么办|怎么处理|咋办))[？?。！!\s]*',question):
        return '咳嗽怎么办？'
    return question


def is_general_cold_question(question):
    """Recognize broad cold questions without assuming personal details."""
    topic = r'(?:普通感冒|感冒|流感)(?:了|时)?'
    separator = r'[\s，,。！？?！、]*'
    care = r'(?:怎么办|怎么处理|怎么缓解|怎么护理)'
    medication = r'(?:吃什么药|用什么药|要不要吃药|需要吃药吗)'
    return bool(re.fullmatch(
        rf'\s*{topic}\s*(?:{care}(?:{separator}{medication})?|{medication}){separator}',
        question,
    ))


def is_general_medication_question(question, context):
    """A general use question can go straight to evidence without patient intake."""
    question = resolve_fever_intent(question, context)
    if not question or set(context) - {'fever_intent'}:
        return False
    return bool(re.search(DRUG_REFERENCE, question) and re.search(MEDICATION_USE, question)
                and not re.search(PERSONAL_SUBJECT + '|' + PRODUCT_SPECIFIC
                                  + r'|儿童|老年|肾病|肾功能|基础病|过敏|已经|刚吃|刚服|\d+',
                                  question))


def missing_context(question,context):
    # These rules select questions; they do not diagnose, score severity, or
    # declare that missing/unknown facts are normal or that medication is safe.
    if is_general_cold_question(question):
        return []
    question=resolve_fever_intent(question,context)
    if question is None:
        return ['fever_intent']
    if is_general_medication_question(question, context):
        return []
    question=normalize_cough_question(question)
    if re.search(r'预防|防护|减少.*(?:传给|传染|传播)',question) and not re.search(r'怎么办|我现在|我已经|吃.*药|用.*药',question):
        return []
    personal=bool(re.search(PERSONAL_SUBJECT,question))
    action=bool(re.search(r'怎么办|怎么处理|吃什么药|能不能|可以|适合|应该|该不该|要不要|怎么吃|如何吃|能吃|能换|能用|能服|能补',question))
    education=bool(re.search(r'科普|区别|原理|是什么|为什么|了解|指南原文|说明书原文|原话|查一下|查查|查.*资料|有哪些症状|常见症状',question))
    complaint=bool(re.search(r'(?:我|本人|自己|家人|父亲|母亲|爸爸|妈妈|孩子|宝宝|爱人).{0,8}(?:发烧了|发热了|咳嗽了|不舒服|咳了|烧了)',question))
    if education and not action and not complaint:
        return []

    details=question+' '+context.get('symptoms','')
    symptom=bool(re.search(r'发烧|发热|感冒|流感|咳嗽|咽痛|喉咙痛|嗓子疼|鼻塞|流鼻涕|腹泻|拉肚子|头痛|头疼|不舒服',details))
    consultation=personal or bool(re.search(r'怎么办|怎么处理|吃什么药|发烧了|发热了',question))
    product_specific = bool(re.search(PRODUCT_SPECIFIC, question))
    drug_decision = bool(re.search(DRUG_REFERENCE, question) and re.search(MEDICATION_USE, question)
                         and (consultation or action or product_specific))
    required=[]
    if re.search(r'钠|盐',question) and re.search(r'算|换算',question) and re.search(r'包装|标签|一袋|含量|营养',question):
        required.extend(['label_sodium','label_basis','consumed_amount'])
    if symptom and consultation and not drug_decision:
        required.extend(['population','symptoms','duration'])
        # A negative mention alone must not turn cough intake into fever intake.
        fever_text=re.sub(r'(?:没有|没|无|不)(?:出现)?(?:发烧|发热)', '', details)
        if re.search(r'发烧|发热|高烧|高热|体温',fever_text):
            required.append('temperature')
            if not re.search(r'未测|没有测|没测',context.get('temperature','')):
                required.append('temperature_method')
    elif drug_decision:
        required.extend(['population', 'risk_factors', 'medications'])
        if product_specific or not re.search(NAMED_DRUG, question):
            required.append('drug_name')
        if product_specific:
            required.append('drug_ingredients')
    elif personal and action and re.search(r'补钾|钾盐|低钠盐|换盐',question):
        required.append('population')
    # Renal information remains specific to the original potassium/salt topic.
    # Other known background is collected for drug questions or explicit
    # background mentions, rather than demanding kidney information universally.
    if consultation and action and re.search(r'补钾|钾盐|低钠盐',question):
        required.append('renal_function')
    if consultation and required and re.search(r'基础病|基础疾病|孕|哺乳|肾病|肾功能|肝病|肝功能|免疫|糖尿病|高血压|心脏病|哮喘',question):
        required.append('risk_factors')
    return [k for k in dict.fromkeys(required) if not context.get(k)]
