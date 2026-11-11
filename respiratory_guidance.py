"""Source-linked first response and optional follow-ups for sore throat reports."""
import re
from patient_context import FEVER_STATUS

SORE_THROAT = r'咽痛|嗓子(?:疼|痛)|喉咙(?:疼|痛)|喉痛'
BODY_ACHES = r'(?:浑身|全身)(?:酸)?(?:疼|痛)|肌肉(?:酸)?(?:疼|痛)|酸痛'
FEVER = r'发热|发烧|高热|高烧'
OPTIONS = {
    '发热': FEVER,
    '头痛': r'头(?:痛|疼)',
    '咳嗽': r'咳嗽|咳了',
    '鼻塞或流鼻涕': r'鼻塞|流鼻涕|流涕',
    '吞咽困难': r'吞咽困难|咽不下',
}
REFERENCES = [
    {'title': '美国 CDC · 普通感冒与流感的症状差异', 'url': 'https://www.cdc.gov/flu/about/qa/coldflu.htm'},
    {'title': '美国 CDC · 咽痛', 'url': 'https://www.cdc.gov/sore-throat/about/'},
    {'title': '美国 CDC · 普通感冒的护理', 'url': 'https://www.cdc.gov/common-cold/treatment/'},
    {'title': 'MedlinePlus · 感冒用药与成分核对', 'url': 'https://www.medlineplus.gov/ency/patientinstructions/000466.htm'},
    {'title': '美国 CDC · 流感与新冠的区别', 'url': 'https://www.cdc.gov/flu/about/flu-vs-covid19.html'},
]
ADULT_FEVER = {'title': 'NHS · 成人发热', 'url': 'https://www.nhs.uk/symptoms/fever-in-adults/'}
CHILD_FEVER = {'title': 'NHS · 儿童发热', 'url': 'https://www.nhs.uk/symptoms/fever-in-children/'}
FLU_TREATMENT = {'title': '美国 CDC · 流感抗病毒治疗', 'url': 'https://www.cdc.gov/flu/treatment/antiviral-drugs.html'}


def guidance_response(sections, followup):
    """Keep the same modules in streamed, saved, and plain-text replies."""
    references = {source['url']: source for _, _, source in sections}
    grouped = {}
    for title, quotes, source in sections:
        grouped.setdefault(title, []).extend(
            dict(quote=quote, source_url=source['url'])
            for quote in (quotes if isinstance(quotes, list) else [quotes]))
    return dict(
        answer='\n\n'.join(point['quote'] for points in grouped.values() for point in points),
        sections=[dict(title=title, excerpts=points) for title, points in grouped.items()],
        followup=followup, references=list(references.values()),
    )


def fever_response(question, context):
    measured = re.fullmatch(
        r'\s*(?:我|孩子|宝宝|成人)?\s*(?:发烧|发热|高烧|体温)?\s*(39(?:\.\d+)?)\s*(?:℃|°C|度)'
        r'\s*(?:高烧|发烧|发热)?[，,\s]*(?:怎么办|咋办|怎么处理)[？?。\s]*', question
    )
    if not measured:
        return None
    population = context.get('population', '')
    sections = [
        ('什么时候就医', '如果出现呼吸困难、意识异常或抽搐，请立即寻求急救。未满 6 个月的婴儿如果测到这个体温，应尽快就医评估。', CHILD_FEVER),
    ]
    if population == '成人' or not population and question.startswith('成人'):
        sections.extend([
            ('现在怎么做', '成人如果没有危险信号，可以先休息、补充水分并观察体温和症状。发热持续不改善或加重时，尽快就医。', ADULT_FEVER),
            ('退烧药怎么考虑', '成人明显不适时，可以先核对说明书或请药师确认对乙酰氨基酚或布洛芬是否适合自己。', ADULT_FEVER),
        ])
    elif population in ('儿童', '6个月及以上儿童') or not population and question.startswith(('孩子', '宝宝')):
        sections.append(('现在怎么做', '等待评估期间，观察孩子的精神状态、饮水和尿量。是否适合用退烧药需要结合年龄、体重和具体药品判断。', CHILD_FEVER))
    else:
        sections.append(('现在怎么做', '如果能正常喝水，可以先休息并补充水分。是否适合使用退烧药，要先确认发烧者的年龄和具体情况。', ADULT_FEVER))
    followup = None
    if not population:
        followup = dict(field='population', question='发烧的是哪个年龄段？',
                        options=['未满6个月', '6个月及以上儿童', '成人', '不清楚'])
    elif population != '未满6个月' and not context.get('duration'):
        followup = dict(field='duration', question='发热持续多久了？',
                        options=['不到一天', '1–3 天', '超过 3 天', '不清楚'])
    return guidance_response(sections, followup)


def mentioned(pattern, text):
    return bool(re.search(pattern, text))


def present(pattern, text):
    return any(not re.search(r'(?:没有|没|无|否认|不)(?:出现|觉得|感觉)?\s*$',
                             text[max(0, match.start()-10):match.start()])
               for match in re.finditer(pattern, text))


def first_response(question, context, *, head_and_chills=False):
    fever = fever_response(question, context)
    if fever:
        return fever
    head_discomfort = r'头(?:有点|很)?(?:沉重?|发沉|昏沉)|脑袋(?:有点|很)?(?:沉重?|发沉|昏沉)'
    chills = r'怕冷|畏寒|发冷|冷得发抖|寒战'
    if (head_and_chills and present(head_discomfort, question) and present(chills, question)
            and not re.search(r'科普|原理|预防|药|儿童|孩子|宝宝|婴儿|孕', question + ' ' + context.get('population', ''))):
        temperature = context.get('temperature')
        followup = None
        if not temperature and not re.search(r'\d{2}(?:\.\d+)?\s*(?:℃|°C|度)', question):
            followup = dict(field='temperature', question='量过体温吗？',
                            options=['低于 38℃', '38–38.9℃', '39℃ 或以上', '未测量', '测过但不记得数值'])
        elif not context.get('duration') and not re.search(r'今天|昨天|刚开始|(?:\d+|[一两二三几])天', question):
            followup = dict(field='duration', question='这种情况持续多久了？',
                            options=['不到一天', '1–2 天', '超过 2 天', '不清楚'])
        answer = '怕冷可能伴随发热。单凭头发沉和怕冷，还看不出原因。'
        if temperature in ('38–38.9℃', '39℃ 或以上'):
            answer += '你选的体温范围提示发热。体温可以帮助判断下一步，但不能单凭数值判断病因。'
        else:
            answer += '可以用体温计按说明测量，记下数值和测量部位。'
        return guidance_response([('怎么判断', answer, ADULT_FEVER)], followup)
    if re.fullmatch(r'\s*(?:我(?:感觉)?)?(?:额头|头|脑袋)(?:摸起来|感觉|有点|很)?(?:发)?热[\s，,。？?]*(?:是(?:不是)?|算(?:不算)?)(?:发烧|发热)(?:了)?[吗么？?。\s]*', question):
        return dict(
            answer='额头摸起来热，单凭手感不能判断是否发烧。可以用体温计按说明测量，并记下数值和测量部位。体温测出来后，再结合年龄、测量方式和其他症状来判断。',
            followup=None,
            references=[{'title': 'NHS · 成人发热与体温测量', 'url': 'https://www.nhs.uk/symptoms/fever-in-adults/'}],
        )
    sore_throat = present(SORE_THROAT, question)
    if not sore_throat and not (
        present(OPTIONS['咳嗽'], question) and present(BODY_ACHES, question)
    ):
        return None
    if re.search(r'科普|原理|是什么|为什么|预防|是否就是|是不是|药|剂量|几片|多少毫克|布洛芬|对乙酰氨基酚|抗生素|头孢|阿莫西林|奥司他韦', question):
        return None

    symptoms = '，'.join([question, context.get('symptoms', ''), context.get('associated_symptoms', '')])
    aches = present(BODY_ACHES, symptoms)
    fever = present(FEVER, symptoms)
    no_fever = not fever and bool(re.search(r'(?:没有|没|无)(?:出现)?(?:发热|发烧)', symptoms))
    if context.get('fever_status'):
        fever = context['fever_status'] == FEVER_STATUS[0]
        no_fever = context['fever_status'] in FEVER_STATUS[1:3]
    headache = present(OPTIONS['头痛'], symptoms)
    high_temperature = bool(re.search(
        r'^\s*(?:39|4[0-2])(?:\.\d+)?\s*(?:℃|°C|度)(?:\s*或以上)?\s*$',
        context.get('temperature', ''),
    ))
    flu_pattern = aches and not no_fever and (high_temperature or fever and headache)
    if aches:
        respiratory_symptom = '咽痛' if sore_throat else '咳嗽'
        opening = f'你提到{respiratory_symptom}和全身酸痛。普通感冒、流感等呼吸道感染都可能出现这些症状。'
    else:
        opening = '咽痛常见于呼吸道感染，比如普通感冒或流感，也可能有其他原因。'
    if flu_pattern:
        observed = '体温达到 39℃ 或以上' if high_temperature else '发热'
        if headache:
            observed += '、头痛'
        opening = f'比起普通感冒，这组症状更符合流感的常见表现。你提到{observed}，还伴有全身酸痛和{respiratory_symptom}。'
        clues = '新冠等呼吸道感染也可能有类似表现，单凭症状无法区分。医生可结合病程及必要的检测进一步判断。'
    elif fever:
        clues = '你还提到了发热。若起病突然、全身酸痛明显，流感值得考虑。'
    elif no_fever:
        clues = '你补充说没有发烧，少了一个提示典型流感的线索，但仍不能排除流感。'
        if headache and sore_throat:
            clues += '头痛也可见于呼吸道感染，和咽痛同时出现仍无法判断炎症是否仅限于咽喉。'
    else:
        clues = '如果起病突然，并伴发热、头痛和明显酸痛，会更提示流感。'
    if not flu_pattern:
        if sore_throat and present(OPTIONS['咳嗽'], symptoms):
            clues += '咽痛伴咳嗽时，病毒性咽痛比链球菌性咽炎更值得考虑。'
        if present(OPTIONS['鼻塞或流鼻涕'], symptoms):
            clues += '你提到的鼻部症状在普通感冒中较常见，流感也可能出现。'
        else:
            clues += '以鼻塞、流鼻涕为主时，普通感冒更常见。'
        clues += '这些症状有重叠，单凭描述无法确诊。'

    followup = None
    if fever and not context.get('temperature') and not re.search(r'\d{2}(?:\.\d+)?\s*(?:度|℃)', symptoms):
        followup = dict(field='temperature', question='测到的最高体温大约是多少？',
                        options=['低于 38℃', '38–38.9℃', '39℃ 或以上', '未测量', '测过但不记得数值'])
    elif flu_pattern and not context.get('duration'):
        followup = dict(field='duration', question='这些症状是从什么时候开始的？',
                        options=['不到一天', '1–2 天', '超过 2 天', '不清楚'])
    elif not context.get('associated_symptoms'):
        relevant = ['发热','头痛','咳嗽','鼻塞或流鼻涕'] if aches else ['发热','咳嗽','鼻塞或流鼻涕','吞咽困难']
        choices = [name for name in relevant if not mentioned(OPTIONS[name], symptoms)]
        if '发热' in choices:
            choices.insert(choices.index('发热')+1, '没有发烧')
        if choices:
            followup = dict(field='associated_symptoms', question='还伴有哪些表现？', options=choices,
                            option_conflicts={'发热': ['没有发烧'], '没有发烧': ['发热']})

    care = ['尽量休息，保证饮水。咳嗽、咽痛等症状可先对症缓解，同时观察是否出现加重或反复。']
    medication = ['退热镇痛药可暂时缓解发热、头痛或肌肉酸痛，但不能据此治疗病因。使用前核对说明书，并说明正在服用的其他药。',
                  '复方感冒药可能包含多种成分，合用前逐一核对，避免同一种成分重复服用。']
    seek_care = ['出现呼吸困难请立即寻求急救。',
                 '无法吞咽、喝不进水或出现明显脱水时，请及时就医。',
                 '症状在几天内没有改善、持续加重或反复发作时，应联系医生评估。']
    if flu_pattern:
        duration = context.get('duration')
        if duration in ('不到一天', '1–2 天'):
            seek_care.append('你补充说刚起病不久。若怀孕、有慢性病或免疫功能低下，应尽早联系医生评估抗病毒治疗，避免耽误早期治疗。')
        elif duration == '超过 2 天':
            seek_care.append('你补充说症状已超过两天。超过两天不代表抗病毒治疗一定无益，高风险或病情较重的人仍应尽快联系医生评估。')
        else:
            seek_care.append('怀孕、有慢性病或免疫功能低下的人出现疑似流感症状时，应尽早联系医生；这些人即使症状较轻也需要关注。')
    if present(OPTIONS['鼻塞或流鼻涕'], symptoms):
        care.append('如果鼻塞、流鼻涕明显，可了解生理盐水鼻喷或滴鼻等缓解方式，并按产品说明使用。')
    sections = [
        ('目前更符合哪种情况' if flu_pattern else '可能的原因', opening, REFERENCES[0]),
        ('目前更符合哪种情况' if flu_pattern else '可能的原因', clues, REFERENCES[4] if flu_pattern else REFERENCES[0]),
        ('现在怎么做', care, REFERENCES[2]),
        ('现在怎么做', '咽痛时可尝试温饮，并避开吸烟和二手烟等刺激。若吞咽困难到影响喝水，应及时就医。', REFERENCES[1]),
        ('用药注意事项', medication, REFERENCES[3]),
        ('用药注意事项', '如果病因是病毒，抗生素不能起到治疗作用。是否需要抗生素，应由医生结合检查判断。', REFERENCES[1]),
        ('什么时候就医', seek_care[:3], REFERENCES[1]),
    ]
    if context.get('temperature'):
        sections.append(('现在怎么做', f'你记录的体温为「{context["temperature"]}」。后续按体温计说明复测，留意体温和不适是否持续或加重；一次读数不能确定病因。', ADULT_FEVER))
    if flu_pattern:
        sections.append(('什么时候就医', seek_care[3:], FLU_TREATMENT))
        sections.append(('用药注意事项', '流感抗病毒药属于处方药，由医生评估是否适用。通常越早使用获益越大；不能仅凭症状自行认定需要服药。', FLU_TREATMENT))
    if context.get('population') == '成人':
        sections.append(('用药注意事项', '成人发热且明显不适时，可核对对乙酰氨基酚或布洛芬的说明书，或请药师确认是否适合自己；有基础病或合用药物时需特别核对。', ADULT_FEVER))
    result = guidance_response(sections, followup)
    if flu_pattern:
        result['sections'][0]['excerpts'][0]['highlight'] = '更符合流感的常见表现'
    return result
