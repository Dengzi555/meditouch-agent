"""Question selection tests; no diagnosis or clinical outcome assertions."""
import unittest
from patient_context import missing_context, validate_context


class ContextRoutingTests(unittest.TestCase):
    def test_general_education_does_not_collect_patient_background(self):
        for question in ['感冒和流感有什么区别？','我想了解发热科普','布洛芬说明书原文是什么？']:
            with self.subTest(question=question): self.assertEqual(missing_context(question,{}),[])

    def test_cough_only_asks_common_fields(self):
        self.assertEqual(missing_context('我咳嗽怎么办？',{}),['population','symptoms','duration'])
        self.assertNotIn('temperature',missing_context('我咳嗽，没有发烧，怎么办？',{}))

    def test_fever_adds_measurement_not_kidney_or_drug_details(self):
        fields=missing_context('我发烧了怎么办？',{})
        self.assertEqual(fields,['population','symptoms','duration','temperature','temperature_method'])
        self.assertNotIn('renal_function',fields);self.assertNotIn('drug_ingredients',fields)

    def test_symptom_details_can_change_followup_scenario(self):
        fields=missing_context('我咳嗽怎么办？',{'symptoms':'今天又发热'})
        self.assertIn('temperature_method',fields);self.assertNotIn('symptoms',fields)

    def test_drug_question_collects_identity_components_and_taken_doses(self):
        for question in ['我能吃布洛芬吗？','布洛芬和感冒药能不能一起吃？']:
            with self.subTest(question=question):
                fields=missing_context(question,{})
                for key in ['population','symptoms','duration','drug_name','drug_ingredients','medications','risk_factors']:
                    self.assertIn(key,fields)
                self.assertNotIn('renal_function',fields)

    def test_relevant_background_is_selected_without_universal_kidney_field(self):
        self.assertIn('risk_factors',missing_context('我有哮喘，现在咳嗽怎么办？',{}))
        self.assertIn('renal_function',missing_context('我可以补钾吗？',{}))
        self.assertNotIn('renal_function',missing_context('我鼻塞怎么办？',{}))

    def test_partial_and_unknown_replies_are_not_repeated_or_changed(self):
        context=validate_context({'population':'成人','symptoms':'发热','duration':'不清楚','temperature':'未测量'})
        self.assertEqual(missing_context('我发烧了怎么办？',context),[])
        self.assertEqual(context['duration'],'不清楚');self.assertEqual(context['temperature'],'未测量')
        self.assertEqual(missing_context('我发烧了怎么办？',{**context,'temperature':'38.0摄氏度，今天上午'}),['temperature_method'])
        self.assertEqual(validate_context({'drug_name':'不清楚','drug_ingredients':'没有说明书'})['drug_ingredients'],'没有说明书')


if __name__=='__main__':unittest.main()
