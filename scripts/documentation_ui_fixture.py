"""Offline screenshot fixture: synthetic inventory/model plus attributed NHS excerpts.

No paid API calls, real chat database or real product claims. Not an evaluation result.
"""
import json
import time
from unittest.mock import patch

from meditouch.api import create_app
from scripts.pharmacy_ui_fixture import ScriptedModel, main

SOURCE = {
    'id': 'docs-cold', 'title': 'NHS：Common cold（离线节选）', 'publisher': 'NHS',
    'url': 'https://www.nhs.uk/conditions/common-cold/', 'kind': 'public',
    'text': 'The main symptoms include: a blocked or runny nose; sneezing; a sore throat; '
            'a hoarse voice; a cough; feeling tired and unwell. '
            'You may also have: a high temperature; aching muscles. '
            'Get plenty of rest. Drink lots of fluid, such as water, to avoid dehydration. '
            'GPs do not recommend antibiotics for colds because they will not relieve your symptoms or speed up your recovery.',
}


class DocumentationModel(ScriptedModel):
    def complete(self, messages):
        value = json.loads(messages[1]['content'])
        if 'query' in value and '感冒' in value['query']:
            return {'role': 'assistant', 'content': json.dumps({
                'intent': 'health_qa', 'medical_topics': ['感冒'], 'symptoms': [],
                'needs_health_answer': True, 'needs_inventory': False, 'knowledge_queries': []}, ensure_ascii=False)}
        if 'source_mode' in value:
            def point(text, highlights, quote):
                return {'text': text, 'highlights': highlights,
                        'citations': [{'source_id': SOURCE['id'], 'quote': quote}]}
            result = {'sections': [
                {'title': '常见表现', 'items': [point('普通感冒常见鼻塞、流鼻涕、打喷嚏、咽痛、声音嘶哑和咳嗽，也可能感到疲倦。',
                    ['鼻塞、流鼻涕', '咽痛'], SOURCE['text'].split(' You may')[0]),
                    point('部分人还会发热、肌肉酸痛；这些是可能出现的表现，不是每个人都会全部出现。',
                    ['发热、肌肉酸痛'], 'You may also have: a high temperature; aching muscles.')]},
                {'title': '日常处理', 'items': [point('注意充分休息、适量补充水分，避免脱水。',
                    ['充分休息', '补充水分'], 'Get plenty of rest. Drink lots of fluid, such as water, to avoid dehydration.')]},
                {'title': '用药注意', 'items': [point('普通感冒不应常规使用抗生素；抗生素不会缓解感冒症状或加快恢复。',
                    ['不应常规使用抗生素'], 'GPs do not recommend antibiotics for colds because they will not relieve your symptoms or speed up your recovery.')]},
            ], 'follow_up': {'question': '顾客目前主要是哪种不适？', 'options': ['鼻塞、流鼻涕', '咽痛、咳嗽', '发热、酸痛', '只是了解知识']}}
            return {'role': 'assistant', 'content': json.dumps(result, ensure_ascii=False)}
        return super().complete(messages)

    def stream(self, messages):
        value = self.complete(messages)['content']
        for offset in range(0, len(value), 80):
            yield value[offset:offset + 80]
            time.sleep(0.015)


def docs_app(**kwargs):
    kwargs['health_evidence_loader'] = lambda *args, **kw: ([SOURCE], [])
    return create_app(**kwargs)


if __name__ == '__main__':
    with patch('scripts.pharmacy_ui_fixture.ScriptedModel', DocumentationModel), \
         patch('scripts.pharmacy_ui_fixture.create_app', docs_app):
        main()
