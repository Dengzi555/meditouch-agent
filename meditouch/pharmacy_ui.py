"""Versioned UI snapshots and SSE frames, without Markdown parsing or raw model traces."""
import json
import re
from datetime import datetime, timezone

SCHEMA_VERSION = 'pharmacy.ui.v1'
EVENTS = {'progress', 'cards.snapshot', 'done', 'error'}


def inventory_summary(item):
    return (f"{item['name']}：品牌 {item.get('brand') or '未录入'}；规格 {item.get('specification') or '未录入'}；"
            f"剂型 {item.get('dosage_form') or '未录入'}；货架 {item['shelf_location']}；库存 {item['stock']}；"
            + ('处方药标记。' if item.get('is_rx') else '非处方标记（录入值）。'))


def build_ui_cards(state, *, staff_script=''):
    """Fail closed: green requires a positive per-SKU check and positive stock."""
    cards = []
    if state.get('inventory_data_mode') == 'mock':
        cards.append({'id': 'mock-data', 'type': 'notice', 'title': '演示数据',
                      'message': '当前库存和禁忌来自 Mock 测试库，未核实真实说明书，不得用于真实接待或售药。'})
    excluded = state.get('excluded_drugs', [])
    if state.get('medical_topics'):
        cards.append({'id': 'topic-context', 'type': 'notice', 'title': '本次咨询主题',
                      'message': '、'.join(state['medical_topics'])
                          + '。主题不代表已确诊；按药名中的主题字面查到的本店商品，只说明目录中有该商品，不代表适合本次情况。'})
    if state.get('has_tried_rag'):
        source = state.get('rag_source', 'none')
        cards.append({'id': 'knowledge-source', 'type': 'notice', 'title': '候选来源',
                      'message': ('已检索本地医学资料，结果仅作找药线索，适用性仍需药师核对。' if source == 'retrieval' else
                          '未取得可核对的实时医学依据，以下药名基于模型通用知识，仅作库存检索候选，请以说明书和药师判断为准。'
                          if source == 'llm_knowledge' else '知识检索未获得可用依据，或当前使用测试适配器；不得据此认定药品适用。')})
    if state.get('combination_status') == 'unknown':
        cards.append({'id': 'combination', 'type': 'missing_information', 'title': '联合用药尚未确认',
                      'message': '单药禁忌筛查不能证明可以一起用，请交药师核对成分与相互作用。',
                      'reason': state.get('safety_reason', '')})
    if state.get('stockout_results'):
        cards.append({'id': 'stockout', 'type': 'notice', 'title': '本轮缺货记录',
                      'message': '\n'.join(inventory_summary(item) for item in state['stockout_results'])
                                 + '\n上述商品库存为0。替代候选不代表已核实同效。'})
    reasons = state.get('excluded_reasons') or {}
    records = state.get('inventory_lookup_records') or {}
    local_items = {item['name']: item for record in records.values() for item in record.get('items', [])}
    missing_terms = [term for term, record in records.items() if record.get('status') == 'not_found']
    if missing_terms:
        cards.append({'id': 'local-not-found', 'type': 'notice', 'title': '本店未查询到的药品',
                      'message': '、'.join(missing_terms) + '：本店库存目录没有匹配商品记录。'})
    if excluded:
        cards.append({'id': 'intercepts', 'type': 'intercept_warning', 'title': '已拦截药品',
                      'items': [{'name': name, 'reason': reasons.get(name) or '原因待药师核对',
                                 'inventory_details': ('本店记录：' + inventory_summary(local_items[name])
                                    + '已拦截，不作为拿药推荐。')
                                    if name in local_items else None}
                                for name in dict.fromkeys(excluded)]})
    status = state.get('safety_status', 'unknown')
    if status == 'unknown':
        missing_data = any(check.get('status') == 'missing_data' for check in state.get('safety_checks', []))
        cards.append({'id': 'missing', 'type': 'missing_information', 'title': '需要人工核对',
                      'message': ('系统未收录该药禁忌数据，请务必询问顾客是否有相关病史。请人工核对病史。'
                                  if missing_data else '请人工核对病史'),
                      'reason': state.get('safety_reason') or '核查尚未完成，不能确认安全'})
    checks = {item.get('inventory_id'): item for item in state.get('safety_checks', [])}
    # Unsafe or incomplete/truncated queries must not expose recommended candidates.
    if status != 'unsafe' and not state.get('inventory_truncated'):
        for item in state.get('inventory_results', []):
            if item['name'] in excluded:
                continue
            check = checks.get(item['inventory_id'], {})
            if check.get('is_safe') is False:
                continue
            safe = status == 'safe' and check.get('is_safe') is True
            cards.append({
                'id': f"drug:{item['inventory_id']}", 'type': 'drug_candidate',
                'title': '核查通过候选 · 药师确认' if safe and item['stock'] > 0 else '库存候选 · 待核对',
                'drug': {key: item.get(key) for key in
                         ('inventory_id', 'name', 'brand', 'specification', 'dosage_form', 'ingredients',
                          'contraindications', 'shelf_location', 'stock', 'is_rx')},
                'safety_status': 'safe' if safe else 'unknown',
                'name_match': item.get('name_match', 'exact'),
                'matched_terms': item.get('matched_terms', []),
                'topic_only': bool(item.get('topic_only')),
                'availability': 'out_of_stock' if item['stock'] == 0 else 'in_stock',
                'warning': '处方药需药师审核' if item.get('is_rx') else None,
                'requires_pharmacist_review': True, 'sale_authorized': False,
            })
    if status == 'unsafe' or not state.get('inventory_results') or state.get('inventory_truncated'):
        query_status = state.get('inventory_status', 'unknown')
        message = ('当前仍存在风险，停止拿药并交药师确认。' if status == 'unsafe' else
                   '查询结果过多，请补充药名、品牌或规格。' if state.get('inventory_truncated') else
                   '本轮尚未获得该咨询主题对应的在库候选，请补充具体症状；不能据此断定本店没有相关药品。'
                        if query_status == 'not_found' and state.get('medical_topics') else
                   '本店库存目录中没有查到匹配药品。' if query_status == 'not_found' else
                   '查询未完成或暂无可用候选，请人工核对库存。')
        cards.append({'id': 'notice', 'type': 'notice', 'title': '当前处理状态', 'message': message})
    if staff_script:
        cards.append({'id': 'staff-script', 'type': 'staff_script', 'title': '店员话术', 'text': staff_script})
    return cards


def ui_event(run_id, seq, event, payload):
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
        raise ValueError('Invalid run ID')
    if type(seq) is not int or seq < 1 or event not in EVENTS:
        raise ValueError('Invalid event sequence/type')
    return {'schema_version': SCHEMA_VERSION, 'run_id': run_id, 'seq': seq, 'event': event,
            'emitted_at': datetime.now(timezone.utc).isoformat(), 'payload': payload}


def encode_sse(event):
    """Each complete JSON event is one SSE data field, terminated by a blank line."""
    validated = ui_event(event['run_id'], event['seq'], event['event'], event['payload'])
    validated['emitted_at'] = event['emitted_at']
    return (f"id: {event['run_id']}:{event['seq']}\nevent: {event['event']}\n"
            + 'data: ' + json.dumps(validated, ensure_ascii=False) + '\n\n').encode('utf-8')


def graph_ui_events(compiled_graph, initial_state, run_id, *, seq_start=0, on_complete=None):
    """Adapt actual LangGraph node updates to UI events; never expose message/trace contents.

    Progress events mean node completion, not hidden reasoning or a fabricated timer.
    The caller supplies the authorized graph and state; this adapter opens no databases.
    """
    state, seq, terminal = dict(initial_state), seq_start, False
    labels = {'inventory_node': ('inventory', '批量库存查询已完成'),
              'rag_node': ('rag', '知识候选检索已完成，准备回查库存'),
              'emergency_node': ('emergency', '急症风险已拦截，停止查药'),
              'chitchat_node': ('general', '通用回复已生成'),
              'safety_node': ('safety', '本轮禁忌核查已完成'),
              'generator_node': ('generation', '店员话术已整理'),
              'fallback_node': ('handoff', '已转交人工核对')}
    # Validate the run identifier before any graph work starts.
    ui_event(run_id, 1, 'progress', {})
    try:
        for update in compiled_graph.stream(initial_state, stream_mode='updates',
                                             config={'recursion_limit': 16}):
            for node, changes in update.items():
                if node not in labels:
                    continue
                state.update(changes)
                stage, label = labels[node]
                seq += 1
                yield ui_event(run_id, seq, 'progress', {
                    'stage': stage, 'phase': 'completed', 'label': label,
                    'attempt': state.get('inventory_attempts', 0)})
                # Inventory has changed; last round's checks must not color new cards green.
                visible = state if node != 'inventory_node' else {
                    **state, 'safety_status': 'unknown', 'safety_checks': [],
                    'safety_reason': '等待本轮禁忌核查'}
                terminal = node in ('generator_node', 'fallback_node', 'emergency_node', 'chitchat_node')
                cards = (state.get('ui_cards', []) if node in ('emergency_node', 'chitchat_node') else
                         build_ui_cards(visible, staff_script=state.get('staff_script', '') if terminal else ''))
                seq += 1
                yield ui_event(run_id, seq, 'cards.snapshot', {
                    'safety_status': visible.get('safety_status', 'unknown'), 'cards': cards})
        seq += 1
        if terminal and on_complete:
            on_complete(dict(state))
        yield ui_event(run_id, seq, 'done' if terminal else 'error',
                       {'status': 'completed', 'sale_authorized': False,
                        'safety_status': state.get('safety_status', 'unknown'),
                        'inventory_attempts': state.get('inventory_attempts', 0)} if terminal else
                       {'code': 'INCOMPLETE_RUN', 'message': '任务未完成，请人工核对', 'retryable': False})
    except Exception:
        seq += 1
        yield ui_event(run_id, seq, 'error', {'code': 'GRAPH_FAILED',
                       'message': '处理未完成，请人工核对库存和病史', 'retryable': False})


def simulated_events():
    """Synthetic protocol demo only; not real inventory, medication advice or live graph progress."""
    run_id = 'demo-run-01'
    state = {'user_query': '查演示药品', 'excluded_drugs': ['演示药甲'],
             'excluded_reasons': {'演示药甲': '演示标签禁用'},
             'inventory_results': [{'inventory_id': 2, 'name': '演示药乙', 'brand': None,
                                    'specification': '演示规格', 'shelf_location': '3排2号',
                                    'stock': 5, 'is_rx': False}],
             'safety_status': 'unknown', 'safety_reason': '缺乏禁忌数据，建议咨询药师',
             'safety_checks': [{'inventory_id': 2, 'name': '演示药乙', 'is_safe': 'unknown'}]}
    yield ui_event(run_id, 1, 'progress', {'stage': 'inventory', 'attempt': 1, 'label': '查询本店库存'})
    yield ui_event(run_id, 2, 'progress', {'stage': 'safety', 'attempt': 1, 'label': '逐项核对禁忌'})
    yield ui_event(run_id, 3, 'cards.snapshot', {'safety_status': 'unknown',
        'cards': build_ui_cards(state, staff_script='我先帮您核对病史，再请药师确认。')})
    yield ui_event(run_id, 4, 'done', {'status': 'completed', 'sale_authorized': False})
