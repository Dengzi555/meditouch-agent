"""Host-owned evidence admission. Passing this gate is never a clinical approval."""
import re
from urllib.parse import urlsplit

from meditouch.evidence_applicability import applicability_reason

POPULATIONS = {
    'adult': 'adult', '成人': 'adult', '成年人': 'adult', '大人': 'adult',
    'child': 'child', 'children': 'child', '儿童': 'child', '孩子': 'child', '小孩': 'child',
    '婴幼儿': 'child', '婴儿': 'child', '宝宝': 'child',
    'pregnant': 'pregnant', '孕妇': 'pregnant', '孕期': 'pregnant', '怀孕': 'pregnant',
    'breastfeeding': 'breastfeeding', '哺乳期': 'breastfeeding', '哺乳': 'breastfeeding',
    'elderly': 'elderly', '老人': 'elderly', '老年人': 'elderly',
}
REASONS = {
    'invalid_source': '资料缺少有效标识或正文',
    'demo_source': '演示资料不能用于真实知识回答',
    'withdrawn_source': '资料已被撤回或停用',
    'snapshot_changed': '资料快照与记录不一致',
    'retrieval_only': '资料仅供检索，不能用于生成回答',
    'generation_disabled': '资料明确禁止用于生成回答',
    'provenance_missing': '资料缺少可追溯来源',
    'untrusted_public_source': '公开来源不在当前接入范围内',
    'scope_invalid': '资料适用范围标注无效',
    'question_out_of_scope': '资料不属于当前问题范围',
    'population_not_established': '尚未确认资料要求的适用人群',
    'population_out_of_scope': '资料适用人群与当前信息不匹配',
    'condition_not_established': '尚未确认资料要求的适用条件',
    'review_required': '待审核资料不能作为选药依据',
}


def _customer_context(state):
    workspace = state.get('workspace') or {}
    customer = workspace.get('customer') or {}
    context = {key: value for key, value in customer.items() if isinstance(value, str)}
    tags = state.get('customer_tags') or []
    populations = {POPULATIONS[tag.strip().casefold()] for tag in tags
                   if isinstance(tag, str) and tag.strip().casefold() in POPULATIONS}
    declared = context.get('population', '').strip().casefold()
    if declared in POPULATIONS:
        populations.add(POPULATIONS[declared])
    age = re.fullmatch(r'\s*(\d{1,3})\s*(岁|个月|月)?\s*', context.get('age', ''))
    if age:
        years = int(age[1]) / 12 if age[2] in ('个月', '月') else int(age[1])
        populations.add('child' if years < 18 else 'elderly' if years >= 65 else 'adult')
    # Only explicit wording in the current complete question; never mine assistant history.
    question = state.get('standalone_query') or state.get('user_query', '')
    for token, canonical in POPULATIONS.items():
        if re.search(r'[\u4e00-\u9fff]', token) and token in question:
            if not re.search(r'(?:没有|不是|未|不在|非)\s*' + re.escape(token), question):
                populations.add(canonical)
    if 'elderly' in populations:
        populations.add('adult')
    return context, populations


def _population_scope(record, url):
    policy = record.get('applicability_policy')
    declared = policy.get('populations', []) if isinstance(policy, dict) else []
    if not isinstance(declared, list) or any(not isinstance(value, str) or not value.strip() for value in declared):
        return None
    values = {POPULATIONS.get(value.strip().casefold(), value.strip()) for value in declared}
    audience = record.get('audience')
    if isinstance(audience, str) and audience.casefold() in POPULATIONS:
        values.add(POPULATIONS[audience.casefold()])
    # Provider URL scope survives cache/import even when there is no structured policy.
    path = urlsplit(url).path.lower()
    if re.search(r'(?:^|/)(?:children|babies)(?:/|$)|-(?:in|for)-(?:children|babies)(?:/|$)', path):
        values.add('child')
    elif re.search(r'(?:^|/)adults(?:/|$)|-(?:in|for)-adults(?:/|$)', path):
        values.add('adult')
    return values


def _public_origin(url):
    parsed = urlsplit(url)
    return (parsed.hostname in {'medlineplus.gov', 'www.nhs.uk', 'nhs.uk'}
            or parsed.hostname == 'www.showapi.com' and parsed.path.startswith('/apiGateway/view/546/'))


def admit_evidence(record, state, *, purpose='general_education'):
    """Return an auditable decision; pending content never becomes an approved record.

    Explicit retrieval-only / generation-disabled records are never fed to a generator.
    Traceable, unrestricted references may explain general knowledge without local clinical
    approval. Candidate-drug evidence additionally requires approved + generation_allowed.
    Population and condition restrictions apply to both uses, including cached responses.
    """
    if purpose not in ('general_education', 'candidate_search'):
        raise ValueError('Unknown evidence purpose')
    record = record if isinstance(record, dict) else {}
    decision = {'source_id': str(record.get('id', '')), 'allowed': False, 'level': 'rejected',
                'review_status': record.get('review_status', 'not_reviewed'), 'purpose': purpose,
                'clinical_approval': False}

    def reject(code, level='rejected'):
        return {**decision, 'code': code, 'reason': REASONS[code], 'level': level}

    if not isinstance(record.get('id'), str) or not record['id'].strip() or not isinstance(record.get('text'), str) or not record['text'].strip():
        return reject('invalid_source')
    if any(key in record and type(record[key]) is not bool
           for key in ('demo', 'retrieval_only', 'generation_allowed', 'evidence_snapshot_valid')):
        return reject('invalid_source')
    if record.get('demo') is True:
        return reject('demo_source')
    if record.get('review_status') in ('rejected', 'revoked', 'retired') or record.get('superseded_by') or record.get('retired') is True:
        return reject('withdrawn_source')
    if record.get('evidence_snapshot_valid') is False:
        return reject('snapshot_changed')
    if record.get('snapshot_sha256'):
        from meditouch.evidence_service import fingerprint
        if fingerprint(record) != record['snapshot_sha256']:
            return reject('snapshot_changed')
    if record.get('retrieval_only') is True:
        return reject('retrieval_only', 'reference_only')
    if record.get('generation_allowed') is False:
        return reject('generation_disabled', 'reference_only')
    metadata = record.get('source') or {}
    if not isinstance(metadata, dict):
        return reject('provenance_missing')
    url = record.get('url') or metadata.get('url') or record.get('source_url') or ''
    try:
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return reject('provenance_missing')
        if record.get('kind') == 'public' and not _public_origin(url):
            return reject('untrusted_public_source')
    except (TypeError, ValueError):
        return reject('provenance_missing')
    context, populations = _customer_context(state)
    scope = _population_scope(record, url)
    if scope is None:
        return reject('scope_invalid')
    if scope:
        if not populations:
            return reject('population_not_established')
        if not populations.intersection(scope):
            return reject('population_out_of_scope')
        if 'child' in scope and 'adult' in populations and 'adult' not in scope:
            return reject('population_out_of_scope')
        if 'adult' in scope and ('child' in populations or
                populations.intersection({'pregnant', 'breastfeeding'}) - scope):
            return reject('population_out_of_scope')
    policy = record.get('applicability_policy')
    if policy is not None:
        if not isinstance(policy, dict):
            return reject('scope_invalid')
        query = ' '.join([state.get('standalone_query') or state.get('user_query', ''),
                          *state.get('symptoms', []), *state.get('medical_topics', [])])
        # Population has already been checked with canonical names and age information.
        reason = applicability_reason({'applicability_policy': {**policy, 'populations': []}}, query, context)
        if reason != 'eligible':
            return reject(reason)
    reviewed = record.get('review_status') == 'approved' and record.get('generation_allowed') is True
    if purpose == 'candidate_search' and not reviewed:
        return reject('review_required', 'reference_only')
    return {**decision, 'allowed': True, 'code': 'admitted', 'reason': '',
            'level': 'reviewed_reference' if reviewed else 'general_reference'}


def select_evidence(records, state, *, purpose='general_education'):
    """Filter without mutating inputs. Return accepted records and text-free audit decisions."""
    accepted, decisions = [], []
    for record in records:
        decision = admit_evidence(record, state, purpose=purpose)
        decisions.append(decision)
        if decision['allowed']:
            accepted.append(record)
    return accepted, decisions
