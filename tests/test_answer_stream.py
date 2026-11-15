"""Prove that answer snapshots arrive before the provider finishes generating."""
import json
import unittest
from threading import Event
from unittest.mock import patch

from meditouch.model_gateway import ModelGateway, ModelError
from meditouch.pharmacy_health import completed_sections
from meditouch.pharmacy_graph import build_pharmacy_graph
from meditouch.pharmacy_ui import graph_ui_events


class AnswerStreamTests(unittest.TestCase):
    def test_parser_waits_for_complete_section_and_handles_json_escapes(self):
        section = {'title': '说明', 'items': [{'text': '引号"和括号}也可出现', 'highlights': [], 'citations': []}]}
        raw = json.dumps({'sections': [section]}, ensure_ascii=False)
        self.assertEqual(completed_sections(raw[:20]), [])
        self.assertEqual(completed_sections(raw)[0].items[0].text, section['items'][0]['text'])

    def test_health_snapshot_is_yielded_while_model_stream_is_still_open(self):
        first = {'title': '先做什么', 'items': [{'text': '先记录已知信息。', 'highlights': [], 'citations': []}]}
        second = {'title': '接下来', 'items': [{'text': '根据变化继续了解。', 'highlights': [], 'citations': []}]}
        release = Event()
        class StreamingModel:
            finished = False
            def stream(self, messages):
                yield '{"sections":[' + json.dumps(first, ensure_ascii=False) + ','
                if not release.wait(5):
                    raise TimeoutError('First section was buffered until completion')
                yield json.dumps(second, ensure_ascii=False) + '],"follow_up":null}'
                self.finished = True
            def complete(self, *args):
                raise AssertionError('Health generation must use provider streaming')
        model = StreamingModel()
        initial = {'intent': 'health_qa', 'user_query': '一般健康知识', 'trace': [], 'model_calls': 0, 'tool_calls': 0,
                   'needs_inventory': False}
        graph = build_pharmacy_graph(None, model=model, health_evidence_loader=lambda *a, **k: ([], []))
        events = graph_ui_events(graph, initial, 'stream-early')
        try:
            first_event = next(events)
            self.assertEqual(first_event['event'], 'cards.snapshot')
            self.assertEqual(len(first_event['payload']['cards'][0]['sections']), 1)
            self.assertFalse(model.finished)
        finally:
            release.set()
        remaining = list(events)
        self.assertTrue(model.finished)
        self.assertEqual(remaining[-1]['event'], 'done')

    def test_unsupported_health_point_is_not_streamed(self):
        source = {'id': 's', 'title': 'Synthetic source', 'publisher': 'fixture', 'kind': 'public',
                  'url': 'https://example.com/health', 'text': 'This is a synthetic source passage.'}
        class Model:
            def stream(self, messages):
                mode = json.loads(messages[1]['content'])['source_mode']
                point = {'text': '不受支持的结论。' if mode == 'evidence' else '一般知识说明。', 'highlights': [],
                         'citations': [{'source_id': 's', 'quote': source['text']}] if mode == 'evidence' else []}
                yield json.dumps({'sections': [{'title': '说明', 'items': [point]}], 'follow_up': None}, ensure_ascii=False)
            def complete(self, messages):
                return {'role': 'assistant', 'content': '{"supported":[false]}'}
        initial = {'intent': 'health_qa', 'user_query': '知识', 'trace': [], 'needs_inventory': False}
        graph = build_pharmacy_graph(None, model=Model(), health_evidence_loader=lambda *a, **k: ([source], []))
        events = list(graph_ui_events(graph, initial, 'stream-filter'))
        self.assertNotIn('不受支持的结论', json.dumps(events, ensure_ascii=False))

    def test_gateway_requests_stream_and_drops_reasoning_deltas(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self):
                for delta in ({'reasoning_content': 'private'}, {'content': '你'}, {'content': '好'}):
                    yield ('data: ' + json.dumps({'choices': [{'delta': delta}]}) + '\n\n').encode()
                yield b'data: [DONE]\n\n'
        with patch.dict('os.environ', {'MED_AGENT_API_KEY': 'fixture-key'}), patch(
                'meditouch.model_gateway.urllib.request.urlopen', return_value=Response()) as send:
            self.assertEqual(list(ModelGateway().stream([])), ['你', '好'])
            self.assertTrue(json.loads(send.call_args.args[0].data)['stream'])

    def test_truncated_provider_stream_fails(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def __iter__(self): return iter([b'data: {"choices":[{"delta":{"content":"x"}}]}\n'])
        with patch.dict('os.environ', {'MED_AGENT_API_KEY': 'fixture-key'}), patch(
                'meditouch.model_gateway.urllib.request.urlopen', return_value=Response()):
            with self.assertRaises(ModelError):
                list(ModelGateway().stream([]))


if __name__ == '__main__':
    unittest.main()
