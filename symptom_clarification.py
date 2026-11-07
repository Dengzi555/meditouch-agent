"""Data-driven symptom clarification cards.

The registry in knowledge/symptom_differentials.json lists common
differentials and the clinical features that separate them. The host chooses
the questions at runtime by how well each one splits the remaining
candidates, so adding a symptom is a data change, not a new if/else branch.

The card only collects observations. It never names a likely diagnosis, and
red-flag options stop the flow with a time-sensitive care message.
"""
import json
import re
import time
from pathlib import Path
from uuid import uuid4

VERSION = 1
MAX_ROUNDS = 2
MAX_QUESTIONS = 4
REGISTRY_PATH = Path(__file__).parent / 'knowledge' / 'symptom_differentials.json'

EDUCATIONAL = re.compile(
    r'是什么|为什么|原因|科普|区别|原理|怎么回事|咋回事|如何预防|怎么预防|'
    r'怎么治疗|如何治疗|用什么药|吃什么药|严重吗|会不会'
)
COLLOQUIAL_WHY = re.compile(r'咋回事|怎么回事|咋了|什么毛病')
PERSONAL_SUBJECT = re.compile(r'我|本人|自己|孩子|宝宝|娃|妈|爸|家人|爱人')
EDUCATIONAL_ACTION = re.compile(r'怎么办|咋办|怎么处理|怎么缓解|不舒服|肿了|疼得|痛得|痒得|厉害|加重')
ACTION_QUESTION = re.compile(
    r'怎么办|咋办|怎么处理|怎么缓解|怎样|吃什么|用什么药|要不要|需不需要|能不能|可不可以|可以|应该|该不该|'
    r'什么症状|有哪些症状|症状有哪些|什么情况|什么时候|怎么护理|如何护理|是什么|为什么|原因|区别|科普|预防|防护|'
    r'传给|传染|避免|严重吗|会不会|怎么治|如何治|去医院|就医|就诊|打开|这篇|资料|原文|链接|查一下|查查'
)
NEGATION = re.compile(r'(?:没有|没|无|不|未|否认)(?:出现|感到|觉得|明显)?\s*$')


class ClarificationError(ValueError):
    """The client sent a clarification payload that does not match the registry."""


def _values(value):
    return value if isinstance(value, list) else [value]


def _load_registry():
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise RuntimeError(f'缺少症状鉴别数据：{REGISTRY_PATH}') from None
    if not isinstance(data, dict) or data.get('version') != VERSION:
        raise RuntimeError('症状鉴别数据版本无效')
    symptoms = data.get('symptoms')
    if not isinstance(symptoms, list) or not symptoms:
        raise RuntimeError('症状鉴别数据为空')

    seen_symptoms = set()
    for symptom in symptoms:
        sid = symptom.get('id')
        if not isinstance(sid, str) or not re.fullmatch(r'[a-z0-9_]{2,60}', sid) or sid in seen_symptoms:
            raise RuntimeError(f'症状 id 无效或重复：{sid!r}')
        seen_symptoms.add(sid)
        if not isinstance(symptom.get('label'), str) or not symptom['label'].strip():
            raise RuntimeError(f'{sid} 缺少 label')
        if not isinstance(symptom.get('notice'), str) or not symptom['notice'].strip():
            raise RuntimeError(f'{sid} 缺少 notice')
        patterns = symptom.get('patterns')
        if (not isinstance(patterns, list) or not patterns
                or any(not isinstance(p, str) or not p.strip() for p in patterns)):
            raise RuntimeError(f'{sid} patterns 无效')
        if symptom.get('trigger_policy', 'any') not in ('any', 'bare_only'):
            raise RuntimeError(f'{sid} trigger_policy 无效')
        differentials = symptom.get('differentials')
        if not isinstance(differentials, list) or len(differentials) < 2:
            raise RuntimeError(f'{sid} 至少需要两个鉴别候选')
        names = set()
        for candidate in differentials:
            name = candidate.get('name')
            if not isinstance(name, str) or not name.strip() or name in names:
                raise RuntimeError(f'{sid} 候选名称无效或重复')
            names.add(name)
            if not isinstance(candidate.get('features'), dict):
                raise RuntimeError(f'{sid} 候选 {name} 缺少 features')
            aliases = candidate.get('aliases', [])
            if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
                raise RuntimeError(f'{sid} 候选 {name} aliases 无效')

        questions = symptom.get('questions')
        if not isinstance(questions, list) or not questions:
            raise RuntimeError(f'{sid} 缺少 questions')
        seen_questions = set()
        for question in questions:
            qid = question.get('id')
            if not isinstance(qid, str) or not re.fullmatch(r'[a-z0-9_]{1,40}', qid) or qid in seen_questions:
                raise RuntimeError(f'{sid} 问题 id 无效或重复：{qid!r}')
            seen_questions.add(qid)
            if not isinstance(question.get('prompt'), str) or not question['prompt'].strip():
                raise RuntimeError(f'{sid}/{qid} 缺少 prompt')
            if question.get('mode') not in ('single', 'multi'):
                raise RuntimeError(f'{sid}/{qid} mode 必须是 single 或 multi')
            options = question.get('options')
            if not isinstance(options, list) or len(options) < 2:
                raise RuntimeError(f'{sid}/{qid} 至少需要两个选项')
            values = set()
            for option in options:
                value = option.get('value')
                if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9_]{1,40}', value) or value in values:
                    raise RuntimeError(f'{sid}/{qid} 选项 value 无效或重复')
                values.add(value)
                if not isinstance(option.get('label'), str) or not option['label'].strip():
                    raise RuntimeError(f'{sid}/{qid}/{value} 缺少 label')
                features = option.get('features')
                if not isinstance(features, dict):
                    raise RuntimeError(f'{sid}/{qid}/{value} 缺少 features')
                for key, expected in features.items():
                    if not isinstance(key, str) or not all(isinstance(v, str) for v in _values(expected)):
                        raise RuntimeError(f'{sid}/{qid}/{value} features 无效')
                if 'exclusive' in option and type(option['exclusive']) is not bool:
                    raise RuntimeError(f'{sid}/{qid}/{value} exclusive 必须是布尔值')
                red_flag = option.get('red_flag')
                if red_flag is not None:
                    if (not isinstance(red_flag, dict) or red_flag.get('level') not in ('prompt_care', 'emergency')
                            or not isinstance(red_flag.get('advice'), str) or not red_flag['advice'].strip()):
                        raise RuntimeError(f'{sid}/{qid}/{value} red_flag 无效')
                patterns = option.get('triggers', [])
                if not isinstance(patterns, list):
                    raise RuntimeError(f'{sid}/{qid}/{value} triggers 必须是数组')
                option['_patterns'] = []
                for trigger in patterns:
                    if not isinstance(trigger, dict) or not isinstance(trigger.get('pattern'), str):
                        raise RuntimeError(f'{sid}/{qid}/{value} trigger 无效')
                    try:
                        option['_patterns'].append(re.compile(trigger['pattern']))
                    except re.error as exc:
                        raise RuntimeError(f'{sid}/{qid}/{value} 正则无效：{exc}') from None
            # Question-level triggers are a compact way to map free text to
            # option values; attach them to the matching option.
            options_by_value = {option['value']: option for option in options}
            question_triggers = question.get('triggers', [])
            if not isinstance(question_triggers, list):
                raise RuntimeError(f'{sid}/{qid} triggers 必须是数组')
            for trigger in question_triggers:
                if (not isinstance(trigger, dict)
                        or trigger.get('value') not in options_by_value
                        or not isinstance(trigger.get('pattern'), str)):
                    raise RuntimeError(f'{sid}/{qid} question trigger 无效')
                try:
                    options_by_value[trigger['value']]['_patterns'].append(
                        re.compile(trigger['pattern']))
                except re.error as exc:
                    raise RuntimeError(f'{sid}/{qid} trigger 正则无效：{exc}') from None
    return {symptom['id']: symptom for symptom in symptoms}


BY_ID = _load_registry()
SYMPTOMS = list(BY_ID.values())


def _question_map(symptom):
    return {question['id']: question for question in symptom['questions']}


def _option_map(question):
    return {option['value']: option for option in question['options']}


def _mentions_differential(text, symptom):
    """Do not open a differential card when the user already names a condition."""
    for candidate in symptom['differentials']:
        for name in [candidate['name'], *candidate.get('aliases', [])]:
            if name in text:
                return True
    return False


def consultation_symptom(question):
    """Return the most specific symptom entry for a broad, personal description."""
    if not isinstance(question, str) or not question.strip():
        return None
    text = question.strip()
    matches = []
    for index, symptom in enumerate(SYMPTOMS):
        if symptom.get('trigger_policy') == 'bare_only' and ACTION_QUESTION.search(text):
            # Keep the existing fever/cold intake for action questions such as
            # "发烧怎么办" or "感冒吃什么药"; cards are for descriptions.
            continue
        for pattern in symptom['patterns']:
            if pattern in text:
                matches.append((len(pattern), -text.find(pattern), -index, symptom))
                break
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    symptom = matches[0][3]
    if _mentions_differential(text, symptom):
        return None
    if EDUCATIONAL.search(text) and not EDUCATIONAL_ACTION.search(text):
        # "我眼皮肿咋回事" is still a symptom consultation; a general
        # "眼皮肿是怎么回事" stays educational.
        if not (COLLOQUIAL_WHY.search(text) and PERSONAL_SUBJECT.search(text)):
            return None
    return symptom


def _negated(text, start):
    return bool(NEGATION.search(text[max(0, start - 8):start]))


def _observed(symptom, text):
    """Infer answered features from free text without overriding explicit answers."""
    found = []
    for question in symptom['questions']:
        for option in question['options']:
            for pattern in option.get('_patterns', []):
                for match in pattern.finditer(text):
                    if _negated(text, match.start()):
                        continue
                    found.append((match.end() - match.start(), question['id'], option['value']))
    found.sort(key=lambda item: -item[0])
    observed = {}
    for _, qid, value in found:
        question = _question_map(symptom)[qid]
        if question['mode'] == 'multi':
            values = observed.setdefault(qid, [])
            if value not in values:
                values.append(value)
        else:
            observed.setdefault(qid, value)
    return observed


def _enforce_exclusive(symptom, answers):
    qmap = _question_map(symptom)
    clean = {}
    for qid, value in answers.items():
        question = qmap.get(qid)
        if question is None:
            continue
        values = value if isinstance(value, list) else [value]
        options = _option_map(question)
        exclusive = [candidate for candidate in values if options[candidate].get('exclusive')]
        if exclusive:
            values = exclusive[:1]
        clean[qid] = values if question['mode'] == 'multi' else values[0]
    return clean


def parse_clarification(context):
    """Validate the JSON string stored in the ``clarification`` slot."""
    raw = (context or {}).get('clarification')
    if raw in (None, ''):
        return {'symptom': None, 'answers': {}, 'round': 0}
    if not isinstance(raw, str):
        raise ClarificationError('clarification 必须是 JSON 字符串')
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        raise ClarificationError('clarification 不是有效 JSON') from None
    if not isinstance(data, dict) or data.get('v') != VERSION:
        raise ClarificationError('clarification 结构无效')
    sid = data.get('symptom')
    if not isinstance(sid, str) or sid not in BY_ID:
        raise ClarificationError('未知的症状标识')
    round_no = data.get('round', 0)
    if type(round_no) is not int or not 0 <= round_no <= MAX_ROUNDS:
        raise ClarificationError('clarification round 无效')
    answers = data.get('answers', {})
    if not isinstance(answers, dict):
        raise ClarificationError('clarification answers 无效')
    question_map = _question_map(BY_ID[sid])
    clean = {}
    for qid, value in answers.items():
        question = question_map.get(qid)
        if question is None:
            raise ClarificationError('未知的追问字段')
        values = value if isinstance(value, list) else [value]
        if not values or any(not isinstance(item, str) for item in values):
            raise ClarificationError('追问答案必须是字符串或字符串数组')
        if question['mode'] == 'single' and len(values) != 1:
            raise ClarificationError('单选题只能有一个答案')
        allowed = _option_map(question)
        if any(item not in allowed for item in values):
            raise ClarificationError('追问答案不在选项内')
        clean[qid] = list(dict.fromkeys(values)) if question['mode'] == 'multi' else values[0]
    return {'symptom': sid, 'answers': _enforce_exclusive(BY_ID[sid], clean), 'round': round_no}


def _state_for(question, context):
    symptom = consultation_symptom(question)
    if symptom is None:
        return None, {}, 0
    state = parse_clarification(context)
    same = state['symptom'] == symptom['id']
    answers = dict(state['answers']) if same else {}
    round_no = state['round'] if same else 0
    text = ' '.join(str(context.get(key) or '') for key in ('symptoms', 'associated_symptoms', 'duration'))
    for qid, value in _observed(symptom, ' '.join([question, text])).items():
        answers.setdefault(qid, value)
    return symptom, _enforce_exclusive(symptom, answers), round_no


def _intersects(expected, actual):
    wanted = set(_values(expected))
    present = set(_values(actual))
    if 'possible' in wanted or 'possible' in present:
        return True
    return bool(wanted & present)


def _compatible(candidate, answers, symptom):
    question_map = _question_map(symptom)
    for qid, value in answers.items():
        question = question_map[qid]
        options = _option_map(question)
        values = value if isinstance(value, list) else [value]
        for selected in values:
            for feature, expected in options[selected].get('features', {}).items():
                actual = candidate['features'].get(feature)
                if actual is not None and not _intersects(expected, actual):
                    return False
    return True


def _split_score(question, candidates, symptom):
    total = len(candidates)
    if total < 2:
        return 0.0
    sizes = []
    for option in question['options']:
        features = option.get('features') or {}
        if not features:
            continue
        # Candidates with an unknown/'possible' value cannot tell us which
        # side of the split they belong to; leave them out of the score.
        known = [candidate for candidate in candidates
                 if all(candidate['features'].get(feature) not in (None, 'possible')
                        and 'possible' not in _values(candidate['features'].get(feature))
                        for feature in features)]
        subset = [candidate for candidate in known
                  if _compatible(candidate, {question['id']: option['value']}, symptom)]
        sizes.append(len(subset))
    if not sizes:
        return 0.0
    # Options can overlap (e.g. a location may keep several candidates), so use
    # the best single split rather than summing squared subset sizes.
    return max(0.0, (total - max(sizes)) / total)


def _plan(symptom, answers, round_no):
    candidates = [candidate for candidate in symptom['differentials']
                  if _compatible(candidate, answers, symptom)]
    if not candidates:
        candidates = list(symptom['differentials'])
    unanswered = [question for question in symptom['questions'] if question['id'] not in answers]
    priority = [question for question in unanswered if question.get('priority')]
    if len(candidates) < 2:
        chosen = priority[:MAX_QUESTIONS]
    else:
        ranked = sorted(
            ((_split_score(question, candidates, symptom), question) for question in unanswered
             if question not in priority),
            key=lambda item: -item[0],
        )
        chosen = priority[:MAX_QUESTIONS]
        for score, question in ranked:
            if len(chosen) >= MAX_QUESTIONS:
                break
            if score > 0.05:
                chosen.append(question)
    order = {question['id']: index for index, question in enumerate(symptom['questions'])}
    unique = []
    seen = set()
    for question in chosen:
        if question['id'] in seen:
            continue
        seen.add(question['id'])
        unique.append(question)
    selected = sorted(unique, key=lambda question: order[question['id']])
    return selected[:MAX_QUESTIONS], candidates


def _card(symptom, selected, round_no, retrieved_names):
    questions = [{
        'id': question['id'],
        'prompt': question['prompt'],
        'mode': question['mode'],
        'options': [dict({'value': option['value'], 'label': option['label']},
                         **({'exclusive': True} if option.get('exclusive') else {}))
                    for option in question['options']],
    } for question in selected]
    return {
        'version': VERSION,
        'symptom': symptom['id'],
        'label': symptom['label'],
        'round': round_no + 1,
        'intro': f'「{symptom["label"]}」这个描述还比较宽泛，先确认下面 {len(questions)} 点。',
        'notice': symptom['notice'],
        'questions': questions,
        'basis': 'retrieval+registry' if retrieved_names and len(retrieved_names) >= 2 else 'registry',
    }


def build_card(question, context=None, *, retrieved_names=None):
    """Return a clarification card, or None when the question is specific enough."""
    context = context or {}
    symptom, answers, round_no = _state_for(question, context)
    if symptom is None or round_no >= MAX_ROUNDS:
        return None
    selected, candidates = _plan(symptom, answers, round_no)
    if not selected:
        return None
    if len(candidates) < 2 and not any(question.get('priority') for question in selected):
        return None
    return _card(symptom, selected, round_no, retrieved_names)


def _card_result(symptom, question, card, candidates, answers, decision):
    summary = _summary(symptom, answers)
    return dict(
        id=uuid4().hex, question=question, status='needs_clarification',
        answer=f'「{card["label"]}」需要先确认几个细节，才能判断哪些资料适用。',
        clarification_card=card, clarification_summary=summary,
        missing_context=[], sources=[], citations=[], error=None,
        engine='症状追问 · 动态选项', model_calls=0, tool_calls=0, latency_ms=0,
        flow=decision or {'kind': 'consultation', 'missing_fields': []},
        trace=[{'node': 'symptom_clarification', 'result': {
            'symptom': symptom['id'], 'candidate_count': len(candidates),
            'questions': [item['id'] for item in card['questions']],
            'round': card['round'], 'basis': card['basis'],
        }}],
        created_at=time.strftime('%Y-%m-%d %H:%M:%S'),
    )


def _risk_result(symptom, question, option, answers, decision):
    flag = option['red_flag']
    level = flag['level']
    answer = (f'你提到「{option["label"]}」。{flag["advice"]}\n\n'
              '这是风险提醒，不是诊断；未出现这些情况也不代表可以排除。')
    return dict(
        id=uuid4().hex, question=question, status='risk_alert',
        risk_level=level, answer=answer,
        clarification_summary=_summary(symptom, answers),
        missing_context=[], sources=[], citations=[], error=None,
        risk_references=[], engine='症状追问 · 风险提醒',
        model_calls=0, tool_calls=0, latency_ms=0,
        flow=decision or {'kind': 'consultation', 'missing_fields': []},
        trace=[{'node': 'symptom_clarification', 'result': {
            'symptom': symptom['id'], 'red_flag': option['value'], 'level': level,
        }}],
        created_at=time.strftime('%Y-%m-%d %H:%M:%S'),
    )


def _red_flag_from_text(symptom, text):
    found = []
    for question in symptom['questions']:
        for option in question['options']:
            if not option.get('red_flag'):
                continue
            for pattern in option.get('_patterns', []):
                for match in pattern.finditer(text):
                    if not _negated(text, match.start()):
                        found.append((1 if option['red_flag']['level'] == 'emergency' else 0, option))
                        break
                else:
                    continue
                break
    return max(found, key=lambda item: item[0])[1] if found else None


def _red_flag_from_answers(symptom, answers):
    question_map = _question_map(symptom)
    found = []
    for qid, value in answers.items():
        question = question_map[qid]
        options = _option_map(question)
        for selected in (value if isinstance(value, list) else [value]):
            option = options[selected]
            if option.get('red_flag'):
                found.append((1 if option['red_flag']['level'] == 'emergency' else 0, option))
    return max(found, key=lambda item: item[0])[1] if found else None


def respond(question, context=None, *, corpus='real', decision=None):
    """Return a risk alert, a clarification card, or None to continue the flow."""
    context = context or {}
    symptom, answers, round_no = _state_for(question, context)
    if symptom is None:
        return None
    text = ' '.join([question, str(context.get('symptoms') or ''),
                     str(context.get('associated_symptoms') or '')])
    red_flag = _red_flag_from_text(symptom, text) or _red_flag_from_answers(symptom, answers)
    if red_flag:
        return _risk_result(symptom, question, red_flag, answers, decision)
    if round_no >= MAX_ROUNDS:
        return None
    selected, candidates = _plan(symptom, answers, round_no)
    if not selected:
        return None
    if len(candidates) < 2 and not any(item.get('priority') for item in selected):
        return None
    card = _card(symptom, selected, round_no, None)
    return _card_result(symptom, question, card, candidates, answers, decision)


def _summary(symptom, answers):
    parts = []
    question_map = _question_map(symptom)
    for question in symptom['questions']:
        value = answers.get(question['id'])
        if value is None:
            continue
        options = _option_map(question)
        values = value if isinstance(value, list) else [value]
        labels = [options[item]['label'] for item in values if item in options]
        if labels:
            parts.append('、'.join(labels))
    return '；'.join(parts)


def answer_summary(context):
    try:
        state = parse_clarification(context)
    except ClarificationError:
        return ''
    if not state['symptom'] or not state['answers']:
        return ''
    return _summary(BY_ID[state['symptom']], state['answers'])


def query_suffix(context):
    summary = answer_summary(context)
    if not summary:
        return ''
    parts = [part for part in summary.split('；') if part and '说不清' not in part and '以上都没有' not in part]
    return ' '.join(parts)[:160]


def context_hints(context):
    """Expose answered card fields as the slots the retrieval flow already understands."""
    try:
        state = parse_clarification(context)
    except ClarificationError:
        return {}
    if not state['symptom'] or not state['answers']:
        return {}
    symptom = BY_ID[state['symptom']]
    hints = {}
    summary = _summary(symptom, state['answers'])
    if summary:
        hints['symptoms'] = summary[:300]
    for qid, slot in (('course', 'duration'), ('age', 'population'), ('temperature', 'temperature')):
        value = state['answers'].get(qid)
        if not isinstance(value, str):
            continue
        question = _question_map(symptom).get(qid)
        option = _option_map(question).get(value) if question else None
        if option:
            hints[slot] = option['label'][:300]
    return hints