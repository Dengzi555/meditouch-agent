import json
import tempfile
import unittest
from pathlib import Path

import symptom_clarification as clarification
from consultation import ConsultationStore, run_consultation
from consultation_flow import route


def payload(symptom, answers, round_no=1):
    return {'clarification': json.dumps(
        {'v': 1, 'symptom': symptom, 'answers': answers, 'round': round_no},
        ensure_ascii=False,
    )}


class CardTests(unittest.TestCase):
    def test_eyelid_card_uses_multiselect_clinical_indicators(self):
        card = clarification.build_card('眼皮肿')
        self.assertIsNotNone(card)
        self.assertEqual(card['symptom'], 'eyelid_swelling')
        self.assertEqual(card['round'], 1)
        questions = {item['id']: item for item in card['questions']}
        self.assertIn('features', questions)
        self.assertEqual(questions['features']['mode'], 'multi')
        labels = [option['label'] for option in questions['features']['options']]
        for expected in ('单纯浮肿', '伴有疼痛', '伴有瘙痒', '有硬结或脓点'):
            self.assertTrue(any(expected in label for label in labels), expected)
        for question in card['questions']:
            self.assertIn(question['mode'], ('single', 'multi'))
            self.assertGreaterEqual(len(question['options']), 2)
            values = [option['value'] for option in question['options']]
            self.assertEqual(len(values), len(set(values)))

    def test_card_does_not_expose_differential_names(self):
        for question in ('眼皮肿', '腹痛', '头痛', '脚踝肿'):
            dumped = json.dumps(clarification.build_card(question), ensure_ascii=False)
            for name in ('麦粒肿', '肾炎', '阑尾炎', '蛛网膜下腔出血', '深静脉血栓'):
                self.assertNotIn(name, dumped)

    def test_different_symptoms_get_different_questions(self):
        eyelid = {item['id'] for item in clarification.build_card('眼皮肿')['questions']}
        abdominal = {item['id'] for item in clarification.build_card('腹痛')['questions']}
        headache = {item['id'] for item in clarification.build_card('头痛')['questions']}
        ankle = {item['id'] for item in clarification.build_card('脚踝肿')['questions']}
        self.assertIn('features', eyelid)
        self.assertIn('location', abdominal)
        self.assertIn('sudden', headache)
        self.assertIn('trauma', ankle)
        self.assertNotEqual(eyelid, abdominal)
        self.assertNotEqual(abdominal, headache)

    def test_common_condition_cards_have_domain_questions(self):
        cases = {
            '感冒了': {'fever', 'allergy', 'smell', 'associated'},
            '发烧': {'age', 'temperature', 'respiratory', 'associated'},
            '孩子发烧': {'age', 'temperature', 'associated'},
            '皮疹': {'lesion', 'blanching', 'associated'},
            '膝盖疼': {'trauma', 'swelling', 'weight'},
            '胸闷': {'nature', 'exertional', 'duration', 'associated'},
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                card = clarification.build_card(question)
                self.assertIsNotNone(card)
                self.assertTrue(expected <= {item['id'] for item in card['questions']})

    def test_pediatric_card_wins_for_child_fever(self):
        self.assertEqual(clarification.consultation_symptom('宝宝发烧')['id'], 'pediatric_fever')
        self.assertEqual(clarification.consultation_symptom('发烧')['id'], 'fever')
        card = clarification.build_card('宝宝发烧')
        self.assertEqual(card['symptom'], 'pediatric_fever')
        self.assertIn('age', {item['id'] for item in card['questions']})

    def test_bare_only_keeps_existing_fever_and_cold_flows(self):
        for question in ('感冒怎么办', '感冒有什么症状', '发烧怎么办',
                         '我发烧了怎么办', '发烧吃什么药'):
            with self.subTest(question=question):
                self.assertIsNone(clarification.build_card(question))
        self.assertIsNotNone(clarification.build_card('感冒了'))
        self.assertIsNotNone(clarification.build_card('发烧'))
        self.assertNotEqual(route('发烧怎么办', {})['kind'], 'consultation')
        self.assertEqual(route('皮疹怎么办', {})['kind'], 'consultation')
        self.assertEqual(route('孩子发烧怎么办', {})['kind'], 'consultation')

    def test_context_hints_map_age_and_temperature(self):
        context = payload('fever', {'age': 'adult', 'temperature': 'high'})
        hints = clarification.context_hints(context)
        self.assertEqual(hints['population'], '成人')
        self.assertEqual(hints['temperature'], '39 到 39.9℃')

    def test_education_named_condition_and_unregistered_symptom_do_not_trigger(self):
        for question in ('眼皮肿是什么原因', '偏头痛怎么办', '过敏眼皮肿', '咳嗽怎么办', '高血压头痛'):
            self.assertIsNone(clarification.build_card(question), question)

    def test_observed_details_skip_answered_questions(self):
        card = clarification.build_card('眼皮肿，疼，有硬结')
        ids = {item['id'] for item in card['questions']}
        self.assertNotIn('features', ids)
        self.assertIn('associated', ids)
        # A co-occurring headache must not be read as eyelid pain.
        unrelated = clarification.build_card('眼皮肿，头痛')
        self.assertIn('features', {item['id'] for item in unrelated['questions']})

    def test_answers_narrow_candidates_and_ask_a_second_round(self):
        card = clarification.build_card('眼皮肿', payload('eyelid_swelling', {'features': 'only_swelling'}))
        self.assertIsNotNone(card)
        self.assertEqual(card['round'], 2)
        self.assertNotIn('features', {item['id'] for item in card['questions']})
        self.assertIn('associated', {item['id'] for item in card['questions']})

    def test_complete_answers_end_clarification(self):
        context = payload('eyelid_swelling', {
            'features': 'only_swelling', 'course': 'chronic', 'associated': ['none'],
        })
        self.assertIsNone(clarification.build_card('眼皮肿', context))

    def test_round_limit_stops_new_cards(self):
        context = payload('eyelid_swelling', {'features': 'only_swelling'}, round_no=2)
        self.assertIsNone(clarification.build_card('眼皮肿', context))

    def test_exclusive_option_wins_over_companions(self):
        state = clarification.parse_clarification(
            payload('eyelid_swelling', {'features': ['only_swelling', 'pain']}))
        self.assertEqual(state['answers']['features'], ['only_swelling'])

    def test_parse_rejects_invalid_payloads(self):
        invalid = [
            {'clarification': '{'},
            {'clarification': json.dumps({'v': 1, 'symptom': 'unknown', 'answers': {}})},
            payload('eyelid_swelling', {'features': 'not_an_option'}),
            payload('eyelid_swelling', {'course': ['acute', 'chronic']}),
            payload('eyelid_swelling', {'unknown_question': 'pain'}),
            {'clarification': json.dumps({'v': 1, 'symptom': 'eyelid_swelling',
                                          'answers': {}, 'round': 9})},
        ]
        for context in invalid:
            with self.subTest(context=context):
                with self.assertRaises(clarification.ClarificationError):
                    clarification.parse_clarification(context)

    def test_summary_and_retrieval_suffix_are_readable(self):
        context = payload('eyelid_swelling', {
            'features': ['pain', 'lesion'], 'course': 'acute', 'associated': ['none'],
        })
        summary = clarification.answer_summary(context)
        self.assertIn('伴有疼痛', summary)
        self.assertIn('3 天以内', summary)
        suffix = clarification.query_suffix(context)
        self.assertIn('伴有疼痛', suffix)
        self.assertNotIn('以上都没有', suffix)


class TriggerTests(unittest.TestCase):
    def test_bare_symptom_routes_to_consultation(self):
        self.assertEqual(route('眼皮肿', {})['kind'], 'consultation')
        self.assertEqual(route('肚子疼怎么办', {})['kind'], 'consultation')

    def test_education_question_is_not_forced_into_intake(self):
        self.assertNotEqual(route('眼皮肿是什么原因', {})['kind'], 'consultation')

    def test_red_flag_text_preempts_the_card(self):
        result = clarification.respond('眼皮肿，视力下降', {})
        self.assertEqual(result['status'], 'risk_alert')
        self.assertEqual(result['risk_level'], 'prompt_care')
        result = clarification.respond('头痛，突然最剧烈', {})
        self.assertEqual(result['status'], 'risk_alert')
        self.assertEqual(result['risk_level'], 'emergency')

    def test_negated_red_flag_is_not_escalated(self):
        result = clarification.respond('眼皮肿，没有视力下降', {})
        self.assertEqual(result['status'], 'needs_clarification')

    def test_red_flag_answer_preempts_the_next_card(self):
        result = clarification.respond(
            '眼皮肿',
            payload('eyelid_swelling', {'associated': ['vision']}),
        )
        self.assertEqual(result['status'], 'risk_alert')
        self.assertEqual(result['risk_level'], 'prompt_care')

    def test_common_condition_red_flags(self):
        cases = [
            ('皮疹', 'rash', {'blanching': 'no'}, 'emergency'),
            ('胸闷', 'chest_tightness', {'nature': 'pressure'}, 'prompt_care'),
            ('孩子发烧', 'pediatric_fever', {'age': 'under3m'}, 'emergency'),
            ('膝盖疼', 'knee_pain', {'weight': 'no'}, 'prompt_care'),
        ]
        for question, symptom, answers, level in cases:
            with self.subTest(question=question):
                result = clarification.respond(question, payload(symptom, answers))
                self.assertEqual(result['status'], 'risk_alert')
                self.assertEqual(result['risk_level'], level)

    def test_common_condition_red_flag_text(self):
        self.assertEqual(clarification.respond('胸闷，呼吸困难', {})['risk_level'], 'emergency')
        self.assertEqual(clarification.respond('皮疹，嘴唇肿', {})['risk_level'], 'emergency')


class ConsultationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ConsultationStore(Path(self.temp.name) / 'test.db')
        self.store.initialize()

    def test_first_turn_returns_card_without_model_calls(self):
        result = run_consultation(self.store, question='眼皮肿', corpus='real')
        self.assertEqual(result['status'], 'needs_clarification')
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(result['tool_calls'], 0)
        self.assertEqual(result['clarification_card']['symptom'], 'eyelid_swelling')
        self.assertTrue(result['session_id'])
        stored = self.store.view(result['session_id'])
        self.assertEqual(stored['result']['status'], 'needs_clarification')

    def test_new_condition_first_turns_return_cards(self):
        for question, symptom in (('发烧', 'fever'), ('孩子发烧', 'pediatric_fever'),
                                  ('皮疹', 'rash'), ('胸闷', 'chest_tightness')):
            with self.subTest(question=question):
                result = run_consultation(self.store, question=question, corpus='real')
                self.assertEqual(result['status'], 'needs_clarification')
                self.assertEqual(result['model_calls'], 0)
                self.assertEqual(result['clarification_card']['symptom'], symptom)

    def test_answers_are_persisted_and_used_for_the_next_round(self):
        first = run_consultation(self.store, question='眼皮肿', corpus='real')
        answers = payload('eyelid_swelling', {'features': ['pain', 'lesion'], 'course': 'acute'})
        second = run_consultation(
            self.store, session_id=first['session_id'], revision=first['revision'],
            known_context=answers,
        )
        self.assertEqual(second['status'], 'needs_clarification')
        self.assertEqual([item['id'] for item in second['clarification_card']['questions']], ['associated'])
        self.assertIn('伴有疼痛', second['clarification_summary'])
        stored = self.store.view(first['session_id'])
        self.assertIn('clarification', stored['context'])

    def test_answers_are_appended_to_the_retrieval_question(self):
        captured = []

        def runner(question, **kwargs):
            captured.append((question, kwargs.get('known_context')))
            return {'id': 'stub', 'question': question, 'status': 'insufficient',
                    'answer': '证据不足', 'sources': [], 'citations': [], 'trace': [],
                    'model_calls': 0, 'tool_calls': 0, 'latency_ms': 0}

        first = run_consultation(self.store, question='眼皮肿', corpus='real')
        answers = payload('eyelid_swelling', {
            'features': 'only_swelling', 'course': 'chronic', 'associated': ['none'],
        })
        second = run_consultation(
            self.store, session_id=first['session_id'], revision=first['revision'],
            known_context=answers, runner=runner,
        )
        self.assertEqual(second['status'], 'insufficient')
        self.assertTrue(captured)
        question, retrieval_context = captured[0]
        self.assertIn('单纯浮肿', question)
        self.assertIn('超过 2 周', question)
        self.assertEqual(retrieval_context['duration'], '超过 2 周')
        self.assertIn('单纯浮肿', retrieval_context['symptoms'])
        self.assertIn('clarification_summary', second)

    def test_risk_answer_does_not_reach_retrieval(self):
        def runner(question, **kwargs):
            raise AssertionError('retrieval must not run after a red flag')

        first = run_consultation(self.store, question='头痛', corpus='real')
        answers = payload('headache', {'sudden': 'yes'})
        second = run_consultation(
            self.store, session_id=first['session_id'], revision=first['revision'],
            known_context=answers, runner=runner,
        )
        self.assertEqual(second['status'], 'risk_alert')
        self.assertEqual(second['risk_level'], 'emergency')


if __name__ == '__main__':
    unittest.main()
