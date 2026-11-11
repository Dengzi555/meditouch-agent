import tempfile
import unittest
from pathlib import Path

from consultation import ConsultationStore, run_consultation, values
from patient_context import FEVER_STATUS, confirmed_context, fever_conflict
from query_plan import understand_question
from respiratory_guidance import first_response


class ContextualAnswerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = ConsultationStore(Path(temp.name) / 'context.db')
        self.store.initialize()

    def test_supported_advice_has_multiple_source_linked_points(self):
        result = first_response('我浑身疼，喉咙疼', {'associated_symptoms': '发热、头痛',
                                                'temperature': '39℃ 或以上', 'duration': '超过 2 天'})
        sections = {section['title']: section['excerpts'] for section in result['sections']}
        for title in ('现在怎么做', '用药注意事项', '什么时候就医'):
            self.assertGreaterEqual(len(sections[title]), 2)
            self.assertTrue(all(point['source_url'].startswith('https://') for point in sections[title]))
        self.assertIn('超过两天不代表抗病毒治疗一定无益', result['answer'])
        self.assertNotIn('高热持续超过两天', result['answer'])

    def test_new_conditions_and_changed_assessment_are_identified(self):
        first = run_consultation(self.store, question='我浑身疼，喉咙疼')
        second = run_consultation(self.store, session_id=first['session_id'], revision=first['revision'],
                                  known_context={'associated_symptoms': '发热、头痛', 'temperature': '39℃ 或以上'})
        description = second['answer_context']
        self.assertEqual({item['label'] for item in description['changed']}, {'伴随表现', '体温'})
        self.assertIn('流感', description['assessment_change']['after']['quote'])

    def test_conflict_stops_inference_and_accepts_explicit_correction(self):
        result = run_consultation(self.store, question='我浑身疼，喉咙疼',
                                  known_context={'associated_symptoms': '发热、没有发烧、头痛',
                                                 'temperature': '39℃ 或以上'})
        self.assertEqual(result['status'], 'needs_clarification')
        self.assertEqual(result['model_calls'], 0)
        self.assertNotIn('更符合流感', result['answer'])
        corrected = run_consultation(self.store, session_id=result['session_id'], revision=result['revision'],
                                     known_context={'fever_status': FEVER_STATUS[0]})
        self.assertIn('更符合流感', corrected['answer'])
        self.assertNotIn('没有发烧', corrected['context']['associated_symptoms']['value'])
        raw = self.store.read(result['session_id'])
        self.assertIn('没有发烧', raw['slots']['associated_symptoms']['value'])

    def test_resolved_fever_does_not_use_historical_temperature_as_current(self):
        raw = {'associated_symptoms': '发热、没有发烧、头痛', 'temperature': '39℃ 或以上',
               'fever_status': FEVER_STATUS[1]}
        context = confirmed_context(raw)
        self.assertEqual(confirmed_context(context), context)
        self.assertIn('此前记录', context['temperature'])
        plan = understand_question('发热、咽痛', context)
        self.assertIn('发热', plan.denied)
        self.assertNotIn('发热', plan.reported)
        reply = first_response('我浑身疼，喉咙疼', context)
        self.assertNotIn('更符合流感', reply['answer'])

    def test_stored_conflicting_answer_is_guarded_when_reopened(self):
        state, lease = self.store.reserve(question='我浑身疼，喉咙疼',
                                          known_context={'associated_symptoms': '发热、没有发烧'})
        self.store.finish(state, lease, {'id': 'legacy', 'status': 'guidance', 'answer': '旧判断'})
        reopened = self.store.view(state['id'])
        self.assertEqual(reopened['status'], 'needs_clarification')
        self.assertEqual(reopened['result']['status'], 'needs_clarification')
        self.assertEqual(reopened['result']['clarification_card']['field'], 'fever_status')
        self.assertEqual(self.store.read(state['id'])['revision'], 1)

    def test_risk_takes_priority_over_a_context_conflict(self):
        result = run_consultation(self.store, question='我呼吸困难',
                                  known_context={'associated_symptoms': '发热、没有发烧'})
        self.assertEqual(result['status'], 'risk_alert')

    def test_new_temperature_reopens_consistency_check(self):
        first = run_consultation(self.store, question='我浑身疼，喉咙疼',
                                 known_context={'associated_symptoms': '没有发烧', 'fever_status': FEVER_STATUS[1]})
        second = run_consultation(self.store, session_id=first['session_id'], revision=first['revision'],
                                  known_context={'temperature': '39℃ 或以上'})
        self.assertEqual(second['status'], 'needs_clarification')
        self.assertNotIn('fever_status', values(self.store.read(first['session_id'])))

    def test_conflict_detection_respects_unknown_measurements(self):
        self.assertFalse(fever_conflict({'associated_symptoms': '没有发烧', 'temperature': '未测量'}))
        self.assertFalse(fever_conflict({'associated_symptoms': '没有发烧', 'temperature': '低于 38℃'}))
        self.assertTrue(fever_conflict({'associated_symptoms': '没有发烧', 'temperature': '38–38.9℃'}))
        self.assertFalse(fever_conflict({}, '科普一下发热和没有发热的区别'))
        self.assertTrue(fever_conflict({}, '我没有发烧，但体温39度'))


if __name__ == '__main__':
    unittest.main()
