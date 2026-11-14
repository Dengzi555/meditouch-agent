"""Conservative routing and risk prompts; no disease/severity diagnosis."""
import re

from meditouch.patient_context import NAMED_DRUG, is_general_cold_question, is_general_medication_question, missing_context, normalize_cough_question
from meditouch.respiratory_guidance import first_response
# Compatibility exports for existing C-side callers.
from meditouch.risk import RISK_SOURCES, RISK_RULES, risk_matches, stopped_result
from meditouch.diarrhea_guidance import child_diarrhea_response
from meditouch.query_plan import understand_question
from meditouch.symptom_clarification import consultation_symptom



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
