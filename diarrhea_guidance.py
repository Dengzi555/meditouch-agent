"""Source-linked progressive guidance for a child's current diarrhea."""
import re

from respiratory_guidance import guidance_response, present

CARE = {'title': '首都儿科研究所 · 儿童急性胃肠炎',
        'url': 'https://wjw.beijing.gov.cn/bmfw_20143/jkzs/jbzs/202307/t20230725_3206683.html'}
MEDICINES = {'title': 'NHS · 腹泻与呕吐',
             'url': 'https://www.nhs.uk/symptoms/diarrhoea-and-vomiting/'}
DIARRHEA = r'腹泻|拉肚子|拉稀|窜稀|拉水样便'


def child_diarrhea_response(question, context):
    population = context.get('population') or question
    if (not re.search(r'孩子|儿童|宝宝|小孩|娃|婴幼儿', population)
            or not present(DIARRHEA, question)
            or re.search(r'是什么|为什么|预防|区别|药|剂量|确诊|如果|假设', question)):
        return None
    symptoms = '，'.join((question, context.get('symptoms', ''), context.get('associated_symptoms', '')))
    warning = next((label for label, pattern in (
        ('便血', r'便血|大便带血|血便'),
        ('明显腹痛', r'剧烈腹痛|明显腹痛|肚子痛得厉害'),
        ('尿量减少', r'尿量减少|尿量明显减少|尿少了|尿变少|小便少'),
        ('喝不进液体', r'喝不进(?:水|液体)|喝了就吐|频繁呕吐'),
    ) if present(pattern, symptoms)), None)
    assessment = '腹泻可见于急性胃肠炎。现在更有用的是判断孩子能否补进水分，以及有无脱水或需要就医的线索。'
    if warning:
        assessment = f'你补充的「{warning}」让就医评估更重要。' + (
            '尿量减少提示可能脱水。' if warning == '尿量减少' else '这会改变下一步处理。')
    elif context.get('associated_symptoms') == '能喝水、尿量和平时差不多':
        assessment = '能喝水且尿量接近平时，暂未报告这两项脱水线索。继续观察精神状态和尿量变化。'
    if not warning and context.get('duration') == '超过 3 天':
        warning = '腹泻持续数日'
        assessment += '你补充说腹泻已持续数日，建议联系医生评估。'
    seek_care = ('请尽快联系医疗机构评估。' if warning else '') + (
        '如果出现便血、明显腹痛、喝不进液体或尿量明显减少，应尽快就医。')
    sections = [
        ('目前重点看什么', assessment, CARE),
        ('现在怎么做', '如果还能喝进去，可以少量多次补液，并观察饮水和排尿。', CARE),
        ('用药注意事项', '可以向药师咨询适合儿童的口服补液盐，并按说明冲配。给孩子使用止泻药前先咨询医生或药师。', MEDICINES),
        ('什么时候就医', seek_care, CARE),
    ]
    if warning:
        sections.insert(0, sections.pop())
    followup = None
    if not warning and not context.get('associated_symptoms'):
        followup = dict(field='associated_symptoms', question='孩子现在喝水和排尿怎么样？',
                        options=['能喝水、尿量和平时差不多', '尿量明显减少', '喝了就吐', '大便带血', '不清楚'])
    elif not warning and not context.get('duration'):
        followup = dict(field='duration', question='腹泻持续多久了？',
                        options=['不到一天', '1–3 天', '超过 3 天', '不清楚'])
    return guidance_response(sections, followup)
