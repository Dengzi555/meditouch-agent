"""UI protocol is not a clinical evaluation; synthetic rows and a scripted generator."""
import json
import unittest
import threading
import urllib.request

from meditouch.pharmacy_ui import build_ui_cards, encode_sse, graph_ui_events, simulated_events, ui_event


def state():
    return {'inventory_results': [{'inventory_id': 2, 'name': '演示乙', 'shelf_location': 'A2',
                                  'stock': 2, 'is_rx': True}],
            'safety_status': 'unknown', 'safety_reason': '未核查',
            'excluded_drugs': ['演示甲'], 'excluded_reasons': {'演示甲': '标签甲禁用'},
            'safety_checks': []}


class PharmacyUiTests(unittest.TestCase):
    def test_four_card_types_are_structured_and_not_extracted_from_markdown(self):
        cards = build_ui_cards(state(), staff_script='演示话术')
        self.assertEqual([card['type'] for card in cards], [
            'intercept_warning', 'missing_information', 'drug_candidate', 'staff_script'])
        self.assertEqual(cards[0]['items'][0]['reason'], '标签甲禁用')
        self.assertEqual(cards[2]['safety_status'], 'unknown')
        self.assertEqual(cards[2]['warning'], '处方药需药师审核')
        self.assertFalse(cards[2]['sale_authorized'])

    def test_green_requires_explicit_matching_check_and_stock(self):
        value = state()
        value['safety_status'] = 'safe'
        cards = build_ui_cards(value)
        self.assertEqual(cards[1]['safety_status'], 'unknown')
        value['safety_checks'] = [{'inventory_id': 2, 'is_safe': True}]
        cards = build_ui_cards(value)
        self.assertEqual(cards[1]['safety_status'], 'safe')
        value['inventory_results'][0]['stock'] = 0
        cards = build_ui_cards(value)
        self.assertEqual(cards[1]['availability'], 'out_of_stock')
        self.assertNotIn('核查通过候选', cards[1]['title'])

    def test_missing_data_warning_reaches_cards_without_markdown(self):
        value = state()
        value['safety_checks'] = [{'inventory_id': 2, 'name': '演示乙', 'status': 'missing_data'}]
        warning = next(card for card in build_ui_cards(value) if card['type'] == 'missing_information')
        self.assertIn('系统未收录该药禁忌数据，请务必询问顾客是否有相关病史。', warning['message'])

    def test_unsafe_and_excluded_candidates_are_never_recommended(self):
        value = state()
        value['safety_status'] = 'unsafe'
        self.assertFalse(any(card['type'] == 'drug_candidate' for card in build_ui_cards(value)))
        value['safety_status'] = 'unknown'
        value['excluded_drugs'].append('演示乙')
        self.assertFalse(any(card['type'] == 'drug_candidate' for card in build_ui_cards(value)))

    def test_sse_is_utf8_json_in_complete_frames(self):
        events = list(simulated_events())
        self.assertEqual([event['seq'] for event in events], [1, 2, 3, 4])
        for event in events:
            frame = encode_sse(event).decode('utf-8')
            self.assertTrue(frame.endswith('\n\n'))
            data = next(line[6:] for line in frame.splitlines() if line.startswith('data: '))
            self.assertEqual(json.loads(data), event)
        with self.assertRaises(ValueError):
            ui_event('run\nevent: malicious', 1, 'progress', {})

    def test_real_graph_updates_stream_sanitized_snapshots_and_done(self):
        class Graph:
            def stream(self, initial, **kwargs):
                yield {'inventory_node': {**state(), 'inventory_attempts': 1,
                                         'messages': ['private internal instructions']}}
                yield {'safety_node': {'safety_status': 'unknown', 'safety_reason': '缺少数据'}}
                yield {'generator_node': {'staff_script': '核对病史后请药师确认'}}
        events = list(graph_ui_events(Graph(), {}, 'run1'))
        self.assertEqual(events[-1]['event'], 'done')
        snapshots = [event['payload'] for event in events if event['event'] == 'cards.snapshot']
        self.assertEqual(snapshots[0]['safety_status'], 'unknown')
        self.assertNotIn('private internal instructions', json.dumps(events))
        self.assertTrue(any(card['type'] == 'staff_script' for card in snapshots[-1]['cards']))

    def test_stream_failure_is_not_a_success_event(self):
        class Broken:
            def stream(self, *args, **kwargs):
                raise RuntimeError('private database path')
        events = list(graph_ui_events(Broken(), {}, 'run2'))
        self.assertEqual(events[-1]['event'], 'error')
        self.assertNotIn('private database path', json.dumps(events))

    def test_demo_http_serves_real_sse_not_ndjson(self):
        from http.server import ThreadingHTTPServer
        from examples.pharmacy_ui.server import Handler
        class Quiet(Handler):
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Quiet)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/events',
                                             data=b'{}', headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=5) as response:
                self.assertIn('text/event-stream', response.headers['Content-Type'])
                frames = response.read().decode('utf-8').strip().split('\n\n')
            self.assertEqual(len(frames), 4)
            self.assertIn('event: done', frames[-1])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_adapter_runs_real_compiled_graph_updates(self):
        from sqlmodel import Session
        from meditouch.inventory_models import DrugInventory, create_inventory_engine, create_inventory_tables
        from meditouch.pharmacy_graph import build_pharmacy_graph
        class Model:
            def complete(self, messages):
                return {'role': 'assistant', 'content': '{"script_id":"ask_history"}'}
        engine = create_inventory_engine('sqlite://')
        self.addCleanup(engine.dispose)
        create_inventory_tables(engine)
        with Session(engine) as session:
            session.add(DrugInventory(name='演示乙', shelf_location='B2', stock=3))
            session.commit()
        initial = {'user_query': '演示乙', 'extracted_entities': {'drug_name': '演示乙'},
                   'customer_tags': [], 'excluded_drugs': [], 'excluded_reasons': {},
                   'inventory_attempts': 0, 'trace': [], 'tool_calls': 0, 'model_calls': 0}
        events = list(graph_ui_events(build_pharmacy_graph(engine, model=Model()), initial, 'real-graph-fixture'))
        self.assertEqual(events[-1]['event'], 'done')
        snapshots = [event['payload']['cards'] for event in events if event['event'] == 'cards.snapshot']
        self.assertEqual(snapshots[-1][-1]['type'], 'staff_script')
