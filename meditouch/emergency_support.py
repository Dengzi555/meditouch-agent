"""Offline first-aid prompts and employee-observed handover; never diagnoses or dispenses."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from meditouch.risk import risk_matches


class EmergencyChanges(BaseModel):
    """Only observations explicitly confirmed by the employee, not model-extracted guesses."""
    model_config = ConfigDict(extra='forbid', strict=True)
    call_status: Literal['unknown', 'not_called', 'called'] | None = None
    consciousness: Literal['unknown', 'responsive', 'unresponsive'] | None = None
    breathing: Literal['unknown', 'normal', 'abnormal', 'absent_or_gasping'] | None = None
    onset: str | None = Field(default=None, max_length=150)
    last_known_well: str | None = Field(default=None, max_length=150)
    location: str | None = Field(default=None, max_length=200)
    age: str | None = Field(default=None, max_length=50)
    history: str | None = Field(default=None, max_length=400)
    medications: str | None = Field(default=None, max_length=400)
    actions_taken: str | None = Field(default=None, max_length=500)


LABELS = {
    'call_status': {'unknown': '未确认是否已呼救', 'not_called': '尚未拨打120', 'called': '店员确认已拨打120'},
    'consciousness': {'unknown': '意识状态未确认', 'responsive': '呼唤有反应', 'unresponsive': '呼唤无反应'},
    'breathing': {'unknown': '呼吸未确认', 'normal': '目前正常呼吸', 'abnormal': '呼吸不正常',
                  'absent_or_gasping': '无呼吸或仅有濒死喘息'},
}
SOURCES = [
    {'title': '北京市卫生健康委员会：如何正确呼叫120',
     'url': 'https://www.beijing.gov.cn/fuwu/bmfw/jhsyfwzdzx/2021nsjrkr/2021sjrkrjkzs/202107/t20210707_2431055.html'},
    {'title': 'ANZCOR：基础生命支持',
     'url': 'https://www.anzcor.org/home/basic-life-support/guideline-8-cardiopulmonary-resuscitation-cpr'},
    {'title': 'ANZCOR：卒中识别与急救',
     'url': 'https://www.anzcor.org/home/first-aid/guideline-9-2-2-stroke'},
    {'title': 'Stroke Foundation：等待救护车时的处理',
     'url': 'https://strokefoundation.org.au/About-Stroke/Learn/signs-of-stroke/What-to-do-while-you-wait-for-an-ambulance'},
]


def build_emergency_card(question, *, symptoms=(), previous=None, changes=None):
    """Derive actions on the host. Unknown stays unknown; displaying advice is not performing it."""
    values = EmergencyChanges().model_dump()
    values.update(call_status='unknown', consciousness='unknown', breathing='unknown')
    if previous:
        values.update(previous['facts'])
    patch = EmergencyChanges.model_validate(changes or {}).model_dump(exclude_unset=True)
    if any(value is None for value in patch.values()):
        raise ValueError('请选择未知或填写空字符串，不能提交空值')
    values.update({key: value.strip() if isinstance(value, str) else value for key, value in patch.items()})
    raw_matches = risk_matches(question, {})
    matches = raw_matches + risk_matches('', {'symptoms': '、'.join(symptoms)})
    # Model-only emergency decisions stay urgent; never infer a reassuring level from no lexical match.
    severity = 'prompt_care' if matches and all(m['level'] == 'prompt_care' for m in matches) else 'emergency'
    if values['consciousness'] == 'unresponsive' or values['breathing'] in ('abnormal', 'absent_or_gasping'):
        severity = 'emergency'
    stroke = any(m['rule'] == 'stroke_signs' for m in raw_matches)
    actions = [
        '先确认现场安全。立即拨打120并开免提，说明准确地址和主要表现，按调度员指令操作；可让另一人接车、清理通道。'
        if severity == 'emergency' else
        '停止查药，尽快到医疗机构评估；若症状严重、加重，或出现无反应、呼吸异常，立即拨打120。',
        '立即呼唤病人并观察是否正常呼吸，判断不清马上告诉120。以下记录可由另一人补充，不要为填表延误救助。',
    ]
    cpr = values['consciousness'] == 'unresponsive' and values['breathing'] in ('abnormal', 'absent_or_gasping')
    normal_unresponsive = values['consciousness'] == 'unresponsive' and values['breathing'] == 'normal'
    actions += [
        ('目前记录符合该条件：' if cpr else '若呼唤无反应且没有正常呼吸（仅喘息也不算正常呼吸）：')
        + '立即按120调度员指导开始心肺复苏，叫人取AED并按设备语音操作；不要等填完记录。',
        ('目前记录符合该条件：' if normal_unresponsive else '若无反应但仍正常呼吸：')
        + '保持气道通畅，置于侧卧恢复体位并持续观察呼吸；若疑有头颈或脊柱损伤，避免随意搬动，听从120指导。',
        '仍清醒者保持安静，呼吸困难时取舒适体位，不强迫平卧；持续观察意识和呼吸变化，变化立即报告120。',
    ]
    if stroke:
        actions += ['出现卒中警示表现时，不喂水、食物或自行给药（包括阿司匹林），不自行快速降压。'
                    '记录最早出现症状和最后一次正常的时间；即使表现缓解也应立即呼救。不能在店内区分脑梗与脑出血。']
    else:
        actions += ['无反应或吞咽困难时不喂水、食物或口服药；不要为试药、换药或测量数据延误呼救。']
    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    revision = previous['revision'] + 1 if previous else 0
    timeline = list(previous.get('updates', [])) if previous else []
    if patch:
        timeline = (timeline + [{'recorded_at': now, 'changes': patch}])[-30:]
    text = [
        '现场交接记录（店员记录，非诊断）', '原始描述：' + question,
        '呼救：' + LABELS['call_status'][values['call_status']],
        '意识：' + LABELS['consciousness'][values['consciousness']],
        '呼吸：' + LABELS['breathing'][values['breathing']],
    ]
    for key, label in [('location', '地点'), ('age', '年龄'), ('onset', '最早出现症状时间'),
                       ('last_known_well', '最后一次正常时间'), ('history', '已知病史/过敏'),
                       ('medications', '已用药名、用量及时间'), ('actions_taken', '已实际采取的措施')]:
        text.append(label + '：' + (values[key] or '未确认'))
    text.append('记录更新时间（非发病时间）：' + now)
    return {'id': 'emergency', 'type': 'emergency_support',
            'title': '应急处置与交接' if severity == 'emergency' else '就医评估与情况记录',
            'severity': severity, 'original_query': question, 'symptoms': list(symptoms),
            'facts': values, 'actions': actions, 'handover': '\n'.join(text), 'revision': revision,
            'updates': timeline, 'updated_at': now, 'guidance_version': '2026-11-15', 'sources': SOURCES}
