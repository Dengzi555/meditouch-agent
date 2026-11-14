"""Staff response branches, exact inventory rendering and model-output constraints."""
import json
import unittest

from langchain_core.messages import HumanMessage, SystemMessage

from meditouch.pharmacy_generation import build_generator_messages, generate_staff_response


def state(**updates):
    value = {
        'user_query': '请帮我核对演示药', 'safety_status': 'unknown',
        'safety_reason': '缺乏禁忌数据，建议咨询药师',
        'inventory_status': 'ok', 'excluded_drugs': [], 'excluded_reasons': {},
        'inventory_results': [{'inventory_id': 1, 'name': '演示药', 'shelf_location': '3排2号',
                               'stock': 2, 'is_rx': True, 'specification': '演示规格'}],
        'safety_checks': [{'inventory_id': 1, 'name': '演示药', 'status': 'missing_data',
                           'is_safe': 'unknown'}], 'trace': [], 'model_calls': 0,
    }
    value.update(updates)
    return value


class StaffModel:
    def __init__(self, response=None):
        self.response = response
        self.messages = None

    def complete(self, messages):
        self.messages = messages
        return {'role': 'assistant', 'content': json.dumps(self.response or {'script_id': 'ask_history'})}


class PharmacyGenerationTests(unittest.TestCase):
    def test_human_message_contains_all_required_context(self):
        messages = build_generator_messages(state())
        self.assertIsInstance(messages[0], SystemMessage)
        self.assertIsInstance(messages[1], HumanMessage)
        payload = json.loads(messages[1].content)
        for key in ('user_query', 'inventory_results', 'excluded_drugs', 'safety_status', 'safety_reason'):
            self.assertIn(key, payload)
        self.assertEqual(payload['inventory_results'][0]['shelf_location'], '3排2号')

    def test_missing_data_renders_exact_warning_and_inventory(self):
        model = StaffModel()
        result = generate_staff_response(state(), model=model)
        self.assertEqual(result['generation_status'], 'validated')
        self.assertIn('系统未收录该药禁忌数据，请务必询问顾客是否有相关病史。', result['answer'])
        for text in ('【拿药指引】', '【店员话术】', '3排2号', '库存 2', '处方药需药师审核', '请人工核对病史'):
            self.assertIn(text, result['answer'])
        self.assertEqual(model.messages[1]['role'], 'user')

    def test_unknown_no_match_is_not_misrepresented_as_missing_data(self):
        result = generate_staff_response(state(safety_checks=[{'name': '演示药', 'status': 'no_match'}],
                                              safety_reason='未命中标签'), model=StaffModel())
        self.assertNotIn('系统未收录该药禁忌数据', result['answer'])
        self.assertIn('未命中标签', result['answer'])
        self.assertIn('请人工核对病史', result['answer'])

    def test_prior_exclusion_reason_is_preserved_for_retried_unknown_candidate(self):
        result = generate_staff_response(state(excluded_drugs=['演示旧药'],
                                              excluded_reasons={'演示旧药': '标签甲禁用'}),
                                         model=StaffModel({'script_id': 'recheck_alternative'}))
        self.assertIn('演示旧药：标签甲禁用', result['answer'])
        self.assertIn('【候选替代（待核）】', result['answer'])
        self.assertIn('请人工核对病史', result['answer'])

    def test_current_unsafe_candidate_is_not_offered_as_alternative(self):
        result = generate_staff_response(state(safety_status='unsafe', excluded_drugs=['演示旧药']),
                                         model=StaffModel())
        self.assertIn('停止拿药', result['answer'])
        self.assertIn('尚无可推荐的替代药', result['answer'])
        self.assertNotIn('【拿药指引】', result['answer'])
        self.assertEqual(result['model_calls'], 0)

    def test_safe_keeps_rx_warning_and_pharmacist_confirmation(self):
        result = generate_staff_response(state(safety_status='safe'),
                                         model=StaffModel({'script_id': 'checked'}))
        self.assertIn('【核查提示】', result['answer'])
        self.assertIn('处方药需药师审核', result['answer'])
        self.assertIn('不自动放行售药', result['answer'])

    def test_empty_and_failed_queries_are_not_called_store_stockout(self):
        for status in ('not_found', 'database_unavailable'):
            result = generate_staff_response(state(inventory_results=[], inventory_status=status),
                                             model=StaffModel())
            self.assertEqual(result['model_calls'], 0)
            self.assertNotIn('本店缺货', result['answer'])
            self.assertNotIn('3排2号', result['answer'])
        item = state()['inventory_results'][0]
        item['stock'] = 0
        result = generate_staff_response(state(inventory_results=[item]), model=StaffModel())
        self.assertIn('已查询商品当前缺货', result['answer'])

    def test_unapproved_text_or_fabricated_sku_never_reaches_answer(self):
        for draft in ({'script_id': '放心吃'}, {'script_id': 'ask_history', 'drug': '假药'},
                      {'script_id': ['ask_history']}):
            result = generate_staff_response(state(), model=StaffModel(draft))
            self.assertEqual(result['generation_status'], 'host_fallback')
            self.assertNotIn('放心吃', result['answer'])
            self.assertNotIn('假药', result['answer'])

    def test_model_failure_preserves_safe_branch_rendering_without_secret(self):
        class Broken:
            def complete(self, *args):
                raise RuntimeError('private provider credential')
        result = generate_staff_response(state(), model=Broken())
        self.assertEqual(result['generation_status'], 'host_fallback')
        self.assertIn('请人工核对病史', result['answer'])
        self.assertNotIn('private provider credential', str(result))
