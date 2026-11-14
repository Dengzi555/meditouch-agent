"""Mandatory inventory -> safety workflow; store engines stay in host closures."""
import os
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from meditouch.inventory_tools import build_inventory_tools, build_medical_rag_tool, search_inventory_batch
from meditouch.model_gateway import ModelGateway
from meditouch.pharmacy_state import PharmacyState

MAX_INVENTORY_ATTEMPTS = 3  # Total queries, including the first (not three extra retries).
MAX_RAG_ATTEMPTS = 2


def route_start(state):
    """Highest-priority risk branch; no inventory/tool access happens in this router."""
    from meditouch.risk import risk_matches
    if state.get('intent') == 'emergency' or risk_matches(state.get('user_query', ''), {
            'symptoms': '、'.join(state.get('symptoms', []))}):
        return 'emergency_node'
    if state.get('intent') == 'chitchat':
        return 'chitchat_node'
    if state.get('intake_status') in ('unavailable', 'needs_clarification'):
        return 'fallback_node'
    return 'inventory_node'


def emergency_node(state):
    answer = '顾客可能存在需要紧急评估的危险表现。请停止推荐或换药，立即联系门店药师，并协助联系当地急救服务或前往急诊；不要等待库存查询。此提示不构成确诊。'
    return {'inventory_results': [], 'inventory_status': 'not_queried', 'safety_checks': [],
            'safety_status': 'unsafe', 'safety_reason': '急症风险，禁止进入查药流程',
            'answer': answer, 'staff_script': '', 'generation_status': 'host_emergency',
            'generation_branch': 'emergency', 'outcome': 'risk_alert',
            'ui_cards': [{'id': 'risk', 'type': 'intercept_warning', 'title': '优先处理急症风险',
                          'items': [{'name': '顾客当前情况', 'reason': answer}]}],
            'trace': state['trace'] + [{'node': 'emergency_node', 'status': 'stopped'}]}


def can_retry_rag(state):
    return (bool(state.get('symptoms') or state.get('medical_topics') or (state.get('extracted_entities') or {}).get('symptom'))
            and state.get('rag_attempts', 0) < MAX_RAG_ATTEMPTS
            and state['inventory_attempts'] < MAX_INVENTORY_ATTEMPTS)


def route_after_inventory(state):
    """Try symptom RAG once on a genuine miss; never hide tool errors or replace an explicit drug."""
    entities = state.get('extracted_entities') or {}
    if (not state['inventory_results'] and not state.get('has_tried_rag')
            and state.get('inventory_status') in ('not_found', 'unsupported_symptom')
            and (state.get('symptoms') or state.get('medical_topics') or entities.get('symptom')) and
            (not entities.get('drug_name') or 'intent' in state)
            and state['inventory_attempts'] < MAX_INVENTORY_ATTEMPTS):
        return 'rag_node'
    return 'safety_node'


def route_after_safety(state):
    """Unknown dominates safe; retry count is independent of exclusion-list length."""
    if not state['inventory_results'] or state.get('inventory_truncated'):
        return 'fallback_node'
    if state['safety_status'] == 'unsafe':
        if 'intent' in state and can_retry_rag(state):
            return 'rag_node'
        if state['inventory_attempts'] < MAX_INVENTORY_ATTEMPTS and len(state['excluded_drugs']) < 3:
            return 'inventory_node'
        return 'fallback_node'
    if ('intent' in state and can_retry_rag(state)
            and (any(item['stock'] == 0 for item in state['inventory_results']) or state.get('unmatched_terms')
                 or (len(state.get('symptoms', [])) > 1 or state.get('catalog_discovered_names'))
                    and not state.get('has_tried_rag'))):
        return 'rag_node'
    return 'generator_node'


def build_pharmacy_graph(engine, *, tool_factory=build_inventory_tools, on_progress=None,
                         deadline=None, model=None, rag_tool=None):
    """Build a fresh graph. The authorized engine is injected only through closures."""
    from langgraph.graph import START, END, StateGraph

    def inventory_node(state):
        if on_progress:
            on_progress('search')
        attempts = state['inventory_attempts'] + 1
        discovered = list(state.get('catalog_discovered_names', []))
        executed = 0
        if deadline is not None and time.monotonic() >= deadline:
            observation = {'status': 'timeout', 'items': []}
        else:
            options = {'excluded_drugs': tuple(state['excluded_drugs'])}
            if state.get('has_tried_rag') and 'intent' not in state:
                options['exact_names'] = True
            search_inventory, _ = tool_factory(engine, **options)
            entities = state.get('extracted_entities') or {}
            args = {key: entities[key] for key in ('symptom', 'drug_name') if entities.get(key)}
            if 'intent' in state and (state.get('target_drugs') or state.get('has_tried_rag')):
                names = (state.get('rag_recommended_drugs', []) + state.get('retained_drugs', [])
                         if state.get('has_tried_rag') else state.get('target_drugs', []))
                names = list(dict.fromkeys(name for name in names if name not in state.get('rag_excluded_drugs', [])))
                executed = int(bool(names))
                observation = (search_inventory_batch(engine, names, excluded_drugs=state['excluded_drugs'])
                               if names else {'status': 'not_found', 'items': []})
            elif state.get('medical_topics') and not state.get('has_tried_rag'):
                # Topic substring search discovers catalog records, never indications or diagnoses.
                executed = 1
                observation = search_inventory_batch(engine, state['medical_topics'], excluded_drugs=state['excluded_drugs'])
                observation['query_records'] = {}  # A missing topic is not an absent named drug.
                observation['unmatched_terms'] = []
                discovered = list(dict.fromkeys(discovered + [item['name'] for item in observation['items']]))
            elif state.get('has_tried_rag'):
                # Deliberately discard the original symptom filter for this lookup.
                names = [name for name in state.get('rag_recommended_drugs', [])
                         if name not in state['excluded_drugs']]
                observations = []
                for name in names:
                    if deadline is not None and time.monotonic() >= deadline:
                        observations.append({'status': 'timeout', 'items': []})
                        break
                    executed += 1
                    try:
                        observations.append(search_inventory.invoke({'drug_name': name}))
                    except Exception:
                        observations.append({'status': 'tool_error', 'items': []})
                failed = next((item for item in observations if item.get('status') not in ('ok', 'not_found')), None)
                found = {item['inventory_id']: item for result in observations for item in result.get('items', [])}
                observation = failed if failed is not None else {
                    'status': 'ok' if found else 'not_found', 'items': list(found.values()),
                    'truncated': any(item.get('truncated') for item in observations)}
                if failed is not None:
                    observation = {**failed, 'items': []}
            else:
                if not args:
                    args = {'drug_name': state['user_query']}
                executed = 1
                try:
                    observation = search_inventory.invoke(args)
                except Exception:
                    observation = {'status': 'tool_error', 'items': []}
        # Defense in depth if a tool implementation ignores host exclusions.
        items = [item for item in observation.get('items', [])
                 if item['name'] not in state['excluded_drugs']]
        for item in items:
            if item['name'] in discovered and item['name'] not in state.get('target_drugs', []):
                item['topic_only'] = True
        lookup_records = dict(state.get('inventory_lookup_records') or {})
        for term, record in observation.get('query_records', {}).items():
            # An excluded SKU disappearing from a later filtered query is not an absent product.
            if record['status'] == 'unconfirmed' and lookup_records.get(term, {}).get('status') == 'matched':
                continue
            lookup_records[term] = record
        return {'inventory_results': items, 'inventory_status': observation.get('status', 'error'),
                'catalog_discovered_names': discovered,
                'inventory_lookup_records': lookup_records,
                'unmatched_terms': observation.get('unmatched_terms', []),
                'stockout_results': list({item['inventory_id']: item for item in
                    state.get('stockout_results', []) + [item for item in items if item['stock'] == 0]}.values()),
                'inventory_truncated': bool(observation.get('truncated')),
                'inventory_attempts': attempts, 'tool_calls': state['tool_calls'] + executed,
                'trace': state['trace'] + [{'node': 'inventory_node', 'attempt': attempts,
                                          'excluded_drugs': list(state['excluded_drugs']),
                                          'result': observation}]}

    def rag_node(state):
        if on_progress:
            on_progress('rag')
        names, status, executed = [], 'timeout', 0
        if deadline is None or time.monotonic() < deadline:
            executed = 1
            try:
                symptoms = state.get('symptoms') or [(state.get('extracted_entities') or {}).get('symptom')]
                excluded = list(dict.fromkeys(state['excluded_drugs'] + state.get('rag_excluded_drugs', [])
                    + state.get('unmatched_terms', [])
                    + [item['name'] for item in state['inventory_results'] if item['stock'] == 0]))
                rag_args = {
                    'symptoms': [item for item in symptoms if item],
                    'customer_tags': state.get('customer_tags', []), 'excluded_drugs': excluded}
                if state.get('medical_topics'):
                    rag_args['medical_topics'] = state['medical_topics']
                raw = (rag_tool or build_medical_rag_tool(model=model)).invoke(rag_args)
                source, evidence = 'injected', []
                if isinstance(raw, dict):
                    source, evidence = raw.get('source', 'none'), raw.get('evidence', [])
                    raw = raw.get('recommended_drugs', [])
                if (not isinstance(raw, list) or len(raw) > 5
                        or any(not isinstance(name, str) or not name.strip() or len(name) > 200 for name in raw)):
                    raise ValueError('Invalid RAG candidates')
                names = list(dict.fromkeys(name.strip() for name in raw if name.strip() not in excluded))
                status = 'ok' if names else 'not_found'
            except Exception:
                status = 'tool_error'
        excluded = list(dict.fromkeys(state['excluded_drugs'] + state.get('rag_excluded_drugs', [])
                    + state.get('unmatched_terms', [])
                    + [item['name'] for item in state['inventory_results'] if item['stock'] == 0]))
        retained = [item['name'] for item in state['inventory_results']
                    if item['stock'] > 0 and item['name'] not in state['excluded_drugs']]
        return {'rag_recommended_drugs': names, 'has_tried_rag': True, 'rag_status': status,
                'rag_attempts': state.get('rag_attempts', 0) + 1,
                'rag_source': source if executed and status in ('ok', 'not_found') else 'none',
                'rag_evidence': evidence if executed and status in ('ok', 'not_found') else [],
                'rag_excluded_drugs': excluded, 'retained_drugs': retained,
                'tool_calls': state['tool_calls'] + executed,
                'trace': state['trace'] + [{'node': 'rag_node', 'status': status,
                                          'source': source if executed and status in ('ok', 'not_found') else 'none', 'drugs': names}]}

    def safety_node(state):
        if on_progress:
            on_progress('validate')
        _, check_drug_safety = tool_factory(engine)
        checks, excluded, executed = [], list(state['excluded_drugs']), 0
        excluded_reasons = dict(state.get('excluded_reasons') or {})
        for item in state['inventory_results']:
            if deadline is not None and time.monotonic() >= deadline:
                result = {'is_safe': 'unknown', 'reason': '核查超时，建议咨询药师'}
            else:
                executed += 1
                try:
                    result = check_drug_safety.invoke({'drug_name': item['name'],
                                                      'customer_tags': state.get('customer_tags', [])})
                except Exception:
                    result = {'is_safe': 'unknown', 'reason': '安全工具调用失败，建议咨询药师'}
            # A name-only safety tool can resolve a different SKU if stock changed.
            if (result.get('inventory_id') not in (None, item['inventory_id'])
                    or result.get('is_safe') is True and result.get('inventory_id') != item['inventory_id']):
                result = {'is_safe': 'unknown', 'reason': '商品身份不一致，请人工核对病史和具体规格'}
            checks.append({'inventory_id': item['inventory_id'], 'name': item['name'], **result})
            if result.get('is_safe') is False and item['name'] not in excluded:
                excluded.append(item['name'])
            if result.get('is_safe') is False:
                excluded_reasons[item['name']] = str(result.get('reason') or '命中禁忌，具体原因待药师核对')
        if any(check.get('is_safe') is False for check in checks):
            status = 'unsafe'
        elif checks and state.get('customer_tags') and all(check.get('is_safe') is True for check in checks):
            status = 'safe'
        else:
            status = 'unknown'
        reasons = list(dict.fromkeys(str(check.get('reason', '核查结果不完整')) for check in checks
                                     if check.get('is_safe') is not True))
        if not checks:
            reasons = ['未找到可核查的候选药品']
            if excluded:
                status = 'unsafe'
                reasons = ['已排除命中禁忌的药品，没有剩余候选可核查']
        elif status == 'unknown' and not reasons:
            reasons = ['顾客标签不完整，不能确认安全']
        combination = len(state.get('target_drugs', [])) > 1 or state.get('intent') == 'check_safety'
        if combination:
            if status == 'safe':
                status = 'unknown'
            reasons.append('联合用药与相互作用尚未完成核验，不能依据单药禁忌筛查批准同服')
            import re
            seen = {}
            for item in state['inventory_results']:
                for ingredient in filter(None, re.split(r'[、,，;；\s]+', item.get('ingredients') or '')):
                    if ingredient in seen and seen[ingredient] != item['inventory_id']:
                        reasons.append('候选存在重复成分：' + ingredient)
                    seen[ingredient] = item['inventory_id']
        return {'safety_status': status, 'safety_reason': '；'.join(reasons),
                'combination_status': 'unknown' if combination else 'not_requested',
                'safety_checks': checks, 'excluded_drugs': excluded,
                'excluded_reasons': excluded_reasons,
                'tool_calls': state['tool_calls'] + executed,
                'trace': state['trace'] + [{'node': 'safety_node', 'status': status,
                                          'checks': checks}]}

    def generator_node(state):
        from meditouch.pharmacy_generation import generate_staff_response
        result = generate_staff_response(state, model=model, deadline=deadline)
        return {**result, 'outcome': 'inventory_candidates',
                'trace': state['trace'] + [{'node': 'generator_node', 'status': state['safety_status'],
                                          'generation': result['generation_status'],
                                          'branch': result['generation_branch']}]}

    def fallback_node(state):
        from meditouch.pharmacy_generation import generate_staff_response
        result = generate_staff_response(state, use_llm=False)
        if state.get('intake_status') == 'needs_clarification':
            text = '请补充想咨询的症状、疾病主题或药名；目前还没有足够的查询条件，尚未查询本店库存。'
            result.update(answer=text, staff_script=text, ui_cards=[{
                'id': 'clarify-query', 'type': 'notice', 'title': '需要明确查询内容', 'message': text}])
        return {**result, 'outcome': 'inventory_fallback',
                'trace': state['trace'] + [{'node': 'fallback_node', 'status': state['safety_status']}]}

    def chitchat_node(state):
        text = '我可以帮店员查找本店药品、库存和货架，并提示需要药师核对的风险。'
        calls = 0
        try:
            calls = 1
            response = (model or ModelGateway(timeout=15)).complete([
                {'role': 'system', 'content': '你是医点通药店助手，简短回答普通闲聊。不诊断、开药或编造本店数据。'},
                {'role': 'user', 'content': state['user_query']}])
            if response.get('role') == 'assistant' and not response.get('tool_calls') and isinstance(response.get('content'), str):
                text = response['content'][:2000]
        except Exception:
            pass
        return {'answer': text, 'ui_cards': [{'id': 'chat', 'type': 'notice', 'title': '医点通', 'message': text}],
                'inventory_results': [], 'inventory_status': 'not_queried', 'safety_checks': [],
                'outcome': 'general_text', 'generation_status': 'general', 'staff_script': '',
                'model_calls': state.get('model_calls', 0) + calls,
                'trace': state['trace'] + [{'node': 'chitchat_node'}]}

    graph = StateGraph(PharmacyState)
    graph.add_node('inventory_node', inventory_node)
    graph.add_node('rag_node', rag_node)
    graph.add_node('safety_node', safety_node)
    graph.add_node('generator_node', generator_node)
    graph.add_node('fallback_node', fallback_node)
    graph.add_node('emergency_node', emergency_node)
    graph.add_node('chitchat_node', chitchat_node)
    graph.add_conditional_edges(START, route_start, {name: name for name in
        ('inventory_node', 'emergency_node', 'chitchat_node', 'fallback_node')})
    graph.add_conditional_edges('inventory_node', route_after_inventory, {
        'rag_node': 'rag_node', 'safety_node': 'safety_node',
    })
    graph.add_edge('rag_node', 'inventory_node')
    graph.add_conditional_edges('safety_node', route_after_safety, {
        'inventory_node': 'inventory_node', 'generator_node': 'generator_node',
        'fallback_node': 'fallback_node',
        'rag_node': 'rag_node',
    })
    graph.add_edge('generator_node', END)
    graph.add_edge('fallback_node', END)
    graph.add_edge('emergency_node', END)
    graph.add_edge('chitchat_node', END)
    return graph.compile()


def prepare_entities(question, customer, model=None, *, history=None):
    """Single semantic entry point, with normalized lists and conversational context."""
    from meditouch.pharmacy_semantics import understand_intake
    return understand_intake(question, customer, history=history, model=model)


def prepare_inventory_state(question, workspace, *, model=None, history=None):
    """Shared initial State for JSON invocation and real SSE graph streaming."""
    intake, status, model_calls = prepare_entities(question, workspace['customer'], history=history, model=model)
    entities = {}
    if intake['target_drugs']:
        entities['drug_name'] = intake['target_drugs'][0]
    if intake['symptoms']:
        entities['symptom'] = intake['symptoms'][0]
    tags = intake['customer_tags']
    trace = [{'node': 'query_understanding', 'status': status}]
    return {**intake, 'history': history or [], 'intake_status': status,
                          'rag_attempts': 0, 'rag_source': 'none', 'rag_evidence': [], 'catalog_discovered_names': [],
                          'rag_excluded_drugs': [], 'retained_drugs': [], 'stockout_results': [],
                          'audience': 'pharmacy_intern', 'workspace': workspace,
                          'user_query': question, 'extracted_entities': entities,
                          'customer_tags': tags, 'inventory_results': [], 'inventory_lookup_records': {},
                          'rag_recommended_drugs': [], 'has_tried_rag': False, 'rag_status': 'not_tried',
                          'safety_status': 'unknown', 'safety_reason': '尚未核查',
                          'excluded_drugs': [], 'inventory_attempts': 0,
                          'trace': trace, 'tool_calls': 0, 'model_calls': model_calls,
                          'excluded_reasons': {}}


def run_inventory_workflow(question, workspace, engine, *, model=None, on_progress=None,
                           deadline_seconds=90, history=None):
    started = time.monotonic()
    from meditouch.pharmacy_semantics import validate_history
    history = validate_history(history)
    initial = prepare_inventory_state(question, workspace, model=model, history=history)
    graph = build_pharmacy_graph(engine, on_progress=on_progress, model=model,
                                 deadline=started + deadline_seconds)
    state = graph.invoke(initial, config={'recursion_limit': 16})
    from meditouch.pharmacy_context import initial_workflow
    workflow = initial_workflow()
    workflow.update(stage='pharmacist_handoff',
                    inventory={'status': state.get('inventory_status', 'not_queried'), 'items': state['inventory_results'],
                               'checked_at': time.strftime('%Y-%m-%d %H:%M:%S')},
                    safety={'status': state['safety_status'], 'checks': state.get('safety_checks', []),
                            'reason': state['safety_reason']})
    return dict(id=uuid4().hex, question=question, status=state['outcome'], answer=state['answer'],
                intent=state['intent'], symptoms=state['symptoms'],
                medical_topics=state.get('medical_topics', []),
                target_drugs=state['target_drugs'], denied_symptoms=state['denied_symptoms'],
                intake_status=state['intake_status'], rag_source=state.get('rag_source', 'none'),
                rag_attempts=state.get('rag_attempts', 0),
                audience='pharmacy_intern', workspace=workspace, pharmacy_workflow=workflow,
                inventory_results=state['inventory_results'], safety_status=state['safety_status'],
                inventory_lookup_records=state.get('inventory_lookup_records', {}),
                rag_recommended_drugs=state.get('rag_recommended_drugs', []),
                has_tried_rag=state.get('has_tried_rag', False), rag_status=state.get('rag_status', 'not_tried'),
                safety_reason=state['safety_reason'], excluded_drugs=state['excluded_drugs'],
                excluded_reasons=state['excluded_reasons'],
                inventory_attempts=state['inventory_attempts'], safety_checks=state.get('safety_checks', []),
                sources=[], citations=[], missing_context=[], error=None,
                trace=state['trace'], model_calls=state['model_calls'], tool_calls=state['tool_calls'],
                generation_status=state['generation_status'],
                ui_cards=state['ui_cards'], staff_script=state['staff_script'],
                engine='LangGraph / 本店库存与禁忌筛查',
                flow={'kind': 'pharmacy_copilot', 'task': workspace['task']},
                latency_ms=round((time.monotonic() - started) * 1000),
                created_at=time.strftime('%Y-%m-%d %H:%M:%S'))


@lru_cache(maxsize=8)
def _readonly_engine(path):
    from meditouch.inventory_models import create_inventory_engine
    return create_inventory_engine('sqlite:///file:' + quote(path, safe='/:') + '?mode=ro&uri=true',
                                   connect_args={'check_same_thread': False})


def _inventory_path_for_store(store_id):
    path, configured_store = os.getenv('MED_AGENT_INVENTORY_DB'), os.getenv('MED_AGENT_INVENTORY_STORE_ID')
    if not path and not configured_store:
        return None
    if not path or not configured_store or store_id != configured_store:
        raise ValueError('库存门店未配置或请求门店不匹配')
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError('库存数据库尚未初始化')
    return path.as_posix()


def inventory_engine_for_store(store_id):
    """Demo single-store binding. Agent tools always get a read-only connection."""
    path = _inventory_path_for_store(store_id)
    return _readonly_engine(path) if path else None


@lru_cache(maxsize=8)
def _write_engine(path):
    from meditouch.inventory_models import create_inventory_engine
    return create_inventory_engine('sqlite:///file:' + quote(path, safe='/:') + '?mode=rw&uri=true',
                                   connect_args={'check_same_thread': False})


def inventory_write_engine_for_store(store_id):
    """Employee-confirmed imports and stock management only; never an Agent tool."""
    path = _inventory_path_for_store(store_id)
    return _write_engine(path) if path else None
