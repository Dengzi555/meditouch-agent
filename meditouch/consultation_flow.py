"""Conservative routing and risk prompts; no disease/severity diagnosis."""
import re
import time
from uuid import uuid4

from meditouch.patient_context import NAMED_DRUG, is_general_cold_question, is_general_medication_question, missing_context, normalize_cough_question
from meditouch.respiratory_guidance import SORE_THROAT, first_response
from meditouch.diarrhea_guidance import child_diarrhea_response
from meditouch.query_plan import understand_question
from meditouch.symptom_clarification import consultation_symptom

RISK_SOURCES=[
    {'title':'中国疾控中心：呼吸道疾病防护与就医信号',
     'url':'https://www.chinacdc.cn/jkkp/crb/qtcr/202605/t20260511_1835710.html'},
    {'title':'NHS：Shortness of breath（仅参考危险信号，不沿用英国电话号码）',
     'url':'https://www.nhs.uk/symptoms/shortness-of-breath/'},
]
RISK_RULES=[('severe_breathing',r'喘不过气|无法呼吸|不能呼吸|呼吸困难|喘不上气|说不出话', 'emergency'),
            ('consciousness',r'意识不清|昏迷|叫不醒|突然意识混乱','emergency'),
            ('seizure',r'正在抽搐|抽搐不止|首次抽搐|抽搐停不下来','emergency'),
            ('cyanosis',r'嘴唇发紫|口唇发紫','emergency'),
            ('persistent_fever',r'持续高热不退|持续高烧不退|高烧不退','prompt_care'),
            ('swallowing',r'无法吞咽|吞咽困难|咽不下水','prompt_care'),
            ('chest_or_blood',r'胸痛|胸口疼|胸口不舒服|咳血|血痰','prompt_care')]


def risk_matches(question,context):
    personal=bool(re.search(r'我|本人|家人|父亲|母亲|爸爸|妈妈|孩子|宝宝|爱人|现在|目前|已经|后.*(?:喘|疼)|'
                            r'怎么办|咋办|怎么处理|要不要(?:就医|去医院)|'
                            r'\d{2}(?:\.\d+)?\s*(?:℃|度)(?:高烧|发烧|发热)|'+SORE_THROAT,question))
    if re.search(r'是什么意思|定义|科普|如何识别',question) and not re.search(r'我现在|我出现|我已经|目前|现在.*(?:喘|呼吸|意识)',question):
        personal=False
    results=[]
    for field,text in [('question',question)]+[(k,context.get(k,'')) for k in ('symptoms','associated_symptoms')]:
        if field=='question' and not personal: continue
        for clause in re.split(r'[，。！？；\n]|但是|(?<!喘)不过|然而|但',text):
            if re.search(r'如果|假如|假设|比如|例如|万一|之前|以前|去年|曾经|上次',clause): continue
            for code,pattern,level in RISK_RULES:
                for match in re.finditer(pattern,clause):
                    before=clause[max(0,match.start()-10):match.start()]
                    after=clause[match.end():match.end()+10]
                    if re.search(r'(没有|没|无|不|未见|否认)(?:出现|感到|明显|任何|发生|了)?\s*$',before): continue
                    if re.search(r'不确定|不清楚|担心|会不会',before): continue
                    if re.match(r'(?:已经|已)?(?:消失|缓解|好转|没有了)',after): continue
                    results.append(dict(rule=code,level=level,field=field,matched_text=match.group(0)))
    return results


TEXT_TASK = re.compile(
    r'写|创作|续写|润色|改写|扩写|缩写|翻译|起草|拟|概括|总结|整理|编|构思|生成|做|列|设计',
    re.I,
)
TEXT_DELIVERABLE = re.compile(
    r'小说|故事|诗歌|首诗|作文|文章|文案|邮件|书信|一封信|通知|脚本|剧本|段落|文字|文本|摘要|提纲|大纲|'
    r'汇报|PPT|幻灯片|演讲稿|发言稿|简历|报告', re.I,
)
MEDICAL_TASK = re.compile(
    r'症状|就医|诊断|确诊|治疗|处方|用药|药物|吃药|药量|剂量|禁忌|副作用|病情|'
    r'(?<![A-Za-z])(?i:HIV)(?![A-Za-z])|人类免疫缺陷病毒|'
    r'胸(?:口)?疼|胸痛|胸闷|呼吸困难|喘不过气|过敏|呕吐|出血|怀孕|体检|化验|检验报告|'
    + NAMED_DRUG
)


def route(question,context,*,plan=None):
    matches=risk_matches(question,context)
    if matches:
        return dict(kind='risk',risk_level='emergency' if any(m['level']=='emergency' for m in matches) else 'prompt_care',
                    matches=matches,missing_fields=[])
    blocked=bool(re.search(r'确诊|告诉我是.*还是|猜.*(?:含|成分)|补钾剂量|停(?:降压)?药|今天.*(?:感染率|流感)',question))
    if blocked: return dict(kind='insufficient',reason='unsupported_individual_or_missing_external_evidence',missing_fields=[])
    if is_general_cold_question(question) or is_general_medication_question(question, context):
        return dict(kind='education',missing_fields=[])
    plan = plan or understand_question(question, context)
    if (not (plan.reported or plan.denied or plan.unknown or MEDICAL_TASK.search(question))
            and TEXT_TASK.search(question) and TEXT_DELIVERABLE.search(question)):
        return dict(kind='general_text', missing_fields=[])
    if {'头发沉', '畏寒'}.issubset(plan.reported):
        guidance_context = context
        if '体温' in plan.unknown and not context.get('temperature'):
            guidance_context = {**context, 'temperature': '未测量'}
        guidance = first_response(plan.normalized, guidance_context, head_and_chills=True)
    else:
        guidance = child_diarrhea_response(question, context) or first_response(question, context)
    if guidance:
        return dict(kind='guidance', missing_fields=[], guidance=guidance)
    # A bare or personal symptom description needs clinical details before
    # retrieval can select applicable material; the card collects them.
    symptom = consultation_symptom(question)
    if symptom:
        return dict(kind='consultation', missing_fields=[], symptom=symptom['id'])
    missing=missing_context(question,context)
    if missing: return dict(kind='clarification',missing_fields=missing)
    question=normalize_cough_question(question)
    if re.search(r'剂量|几片|吃多少|多少毫克|降多少|标签.*(?:算|多少)',question):
        return dict(kind='insufficient',reason='individual_calculation_not_supported',missing_fields=[])
    educational=bool(re.search(r'如果|假如|假设|科普|区别|原理|是什么|为什么|预防|防护|减少.*(?:传给|传染|传播)',question))
    personal=bool(re.search(r'我|本人|家人|父亲|母亲|爸爸|妈妈|孩子|宝宝|怎么办|怎么处理',question))
    personal = personal or bool(plan.reported and re.search(r'什么情况|啥情况|怎么回事|咋回事|咋办', question))
    if educational and not re.search(r'怎么办|怎么处理|我现在|我已经',question):personal=False
    return dict(kind='consultation' if personal else 'education',missing_fields=[])


def stopped_result(question,decision):
    risk=decision['kind']=='risk'
    if risk:
        action=('你描述的情况可能需要紧急处理。请立即联系当地急救服务或前往急诊，不要等待本工具继续追问或检索。'
                if decision['risk_level']=='emergency' else
                '你描述了需要尽快就医评估的信号。请及时联系医疗机构；若症状严重或正在加重，请联系当地急救服务。')
        answer=action+'\n\n这只是风险提醒，不能据此确认病因或严重程度。未命中提示也不代表安全。'
        status = 'risk_alert'
    else:
        answer='当前任务或证据不足以支持这个判断。本工具不能确诊、猜测未知成分、计算个体处方或决定停药；当前不生成这些结论。'
        status = 'insufficient'
    return dict(id=uuid4().hex,question=question,status=status,
                answer=answer,engine='咨询流程 · 确定性路由',sources=[],citations=[],error=None,
                missing_context=[],model_calls=0,tool_calls=0,latency_ms=0,
                flow=decision,risk_references=RISK_SOURCES if risk else [],
                trace=[{'node':'route','result':decision}],created_at=time.strftime('%Y-%m-%d %H:%M:%S'))
