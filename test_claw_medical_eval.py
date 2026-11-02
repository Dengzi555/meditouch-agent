import importlib.util
import json
import unittest

HAS_CLAW = importlib.util.find_spec('claw_eval') is not None


@unittest.skipUnless(HAS_CLAW, '使用 .venv-eval 运行Claw-Eval评分器测试')
class ClawGraderTests(unittest.TestCase):
    def setUp(self):
        from claw_eval.models.task import TaskDefinition, Prompt
        from claw_medical_grader import MedicalEvidenceGrader
        self.grader = MedicalEvidenceGrader()
        self.task = TaskDefinition(task_id='negative-control', task_name='negative-control',
            prompt=Prompt(text='测试'), reference_solution=json.dumps(dict(
                kind='rule', status='insufficient', active=[], retired=[], groups=0)))

    def test_correct_abstention_passes(self):
        scores = self.grader.grade([], [], self.task, audit_data={'medical':{
            'output':{'status':'insufficient','trace':[]},'database_unchanged':True}})
        self.assertEqual(scores.completion, 1)
        self.assertEqual(scores.safety, 1)

    def test_wrong_answer_fails(self):
        from claw_eval.models.scoring import compute_task_score, is_pass
        scores = self.grader.grade([], [], self.task, audit_data={'medical':{
            'output':{'status':'evidence_found','trace':[]},'database_unchanged':True}})
        self.assertFalse(is_pass(compute_task_score(scores)))

    def test_database_change_or_missing_audit_never_passes(self):
        from claw_eval.models.scoring import compute_task_score, is_pass
        for audit in [{}, {'medical':{'output':{'status':'insufficient'},'database_unchanged':False}}]:
            scores = self.grader.grade([], [], self.task, audit_data=audit)
            self.assertFalse(is_pass(compute_task_score(scores)))

    def test_forged_rag_quote_fails(self):
        from claw_eval.models.task import TaskDefinition, Prompt
        task = TaskDefinition(task_id='quote-negative',task_name='quote-negative',prompt=Prompt(text='测试'),
            reference_solution=json.dumps({'kind':'rag','page':1,'anchor':'真实原文'}))
        scores = self.grader.grade([], [], task, audit_data={'medical':{
            'database_unchanged':True,'source_pages':{'1':'真实原文'},'output':{'hits':[{
                'text':'真实原文','pdf_pages':[1],'generation_allowed':False,'review_status':'pending',
                'spans':[{'page':1,'raw_start':0,'raw_end':4,'raw_quote':'伪造引用'}]}]}}})
        self.assertEqual(scores.completion, 0)


if __name__ == '__main__': unittest.main()
