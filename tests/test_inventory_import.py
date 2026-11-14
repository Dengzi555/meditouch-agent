"""OCR prefill never writes; explicit employee submission inserts both rows atomically."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event
from sqlmodel import Session, func, select

from meditouch.api import create_app
from meditouch.inventory_import import ConfirmImport, VisionGateway, import_confirmed
from meditouch.inventory_models import DrugDetails, DrugInventory, create_inventory_engine, create_inventory_tables


def image_bytes():
    output = BytesIO()
    Image.new('RGB', (16, 16), 'white').save(output, format='PNG')
    return output.getvalue()


class FakeVision:
    def extract(self, data, mime):
        assert mime == 'image/png'
        return {'name': '演示新药', 'brand': None, 'is_rx': None,
                'ingredients': '图片可见演示成分', 'contraindications': None}


class InventoryImportTests(unittest.TestCase):
    def test_multi_drug_ocr_preserves_individual_fields_and_never_writes(self):
        class Multi:
            def extract(self, *args):
                return {'items': [
                    {'name': '演示甲', 'brand': '品牌甲', 'is_rx': False,
                     'ingredients': '甲成分', 'specification': '演示规格甲'},
                    {'name': '演示乙', 'brand': '品牌乙', 'is_rx': None,
                     'contraindications': '乙禁忌', 'dosage_form': '演示剂型乙'}], 'truncated': False}
        with self.app(Multi) as client:
            response = self.ocr(client)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['schema_version'], 'pharmacy.ocr.v2')
        self.assertNotIn('name', data)  # A batch must not silently expose the first drug as the only one.
        self.assertEqual(len(data['items']), 2)
        self.assertEqual(data['items'][0]['specification'], '演示规格甲')
        self.assertIsNone(data['items'][1]['ingredients'])
        self.assertIsNone(data['items'][1]['is_rx'])
        self.assertEqual(data['items'][1]['contraindications'], '乙禁忌')
        self.assertEqual(self.counts(), (0, 0))

    def test_duplicate_boxes_are_not_stock_and_different_specifications_stay_separate(self):
        class Multi:
            def extract(self, *args):
                return {'items': [{'name': '演示甲', 'brand': '甲', 'specification': '规格1'},
                                  {'name': '演示甲', 'brand': '甲', 'specification': '规格1'},
                                  {'name': '演示甲', 'brand': '甲', 'specification': '规格2'}]}
        with self.app(Multi) as client:
            data = self.ocr(client).json()
        self.assertEqual(len(data['items']), 2)
        self.assertEqual(data['duplicates_removed'], 1)
        self.assertTrue(all('stock' not in item for item in data['items']))
        self.assertEqual(self.counts(), (0, 0))

    def test_batch_limits_flags_and_tampered_second_item_are_validated(self):
        cases = [{'items': [{'name': str(index)} for index in range(13)]},
                 {'items': [{'name': '甲'}, {'name': '乙', 'stock': 5}]},
                 {'items': [{'name': '甲'}, {'name': '乙', 'is_rx': 'false'}]},
                 {'items': [{'name': '甲'}], 'truncated': 'true'}]
        for raw in cases:
            class Invalid:
                def extract(self, *args):
                    return raw
            with self.subTest(raw=raw), self.app(Invalid) as client:
                self.assertEqual(self.ocr(client).status_code, 502)
        class Empty:
            def extract(self, *args):
                return {'items': [], 'truncated': False}
        with self.app(Empty) as client:
            self.assertEqual(self.ocr(client).json()['items'], [])
        self.assertEqual(self.counts(), (0, 0))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / 'stock.db'
        self.engine = create_inventory_engine('sqlite:///' + path.as_posix())
        self.addCleanup(self.engine.dispose)
        create_inventory_tables(self.engine)
        self.client = self.app(FakeVision)
        self.addCleanup(self.client.close)
        self.body = {'store_id': 'A', 'name': '员工确认后的药名', 'brand': None,
                     'shelf_location': 'C7', 'stock': 7, 'is_rx': False,
                     'ingredients': '人工核对的成分', 'contraindications': None,
                     'dosage_form': '演示剂型', 'specification': '演示规格'}

    def app(self, vision):
        return TestClient(create_app(engine_resolver=lambda store: self.engine,
                          write_engine_resolver=lambda store: self.engine, vision_factory=vision))

    def counts(self):
        with Session(self.engine) as session:
            return (session.exec(select(func.count()).select_from(DrugInventory)).one(),
                    session.exec(select(func.count()).select_from(DrugDetails)).one())

    def ocr(self, client=None, content=None):
        return (client or self.client).post('/api/pharmacy/ocr_prefill', data={'store_id': 'A'},
            files={'image': ('box.png', image_bytes() if content is None else content, 'image/png')})

    def test_ocr_returns_null_unknown_rx_and_optional_brand_without_writing(self):
        response = self.ocr()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['brand'])
        self.assertIsNone(response.json()['is_rx'])
        self.assertTrue(response.json()['extraction_only'])
        self.assertNotIn('stock', response.json())
        self.assertNotIn('shelf_location', response.json())
        self.assertEqual(self.counts(), (0, 0))

    def test_confirm_uses_final_form_and_writes_linked_rows(self):
        self.ocr()
        response = self.client.post('/api/pharmacy/confirm_import', json=self.body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.counts(), (1, 1))
        with Session(self.engine) as session:
            drug = session.get(DrugInventory, response.json()['inventory_id'])
            self.assertEqual(drug.name, '员工确认后的药名')
            self.assertEqual(drug.stock, 7)
            self.assertEqual(drug.details.ingredients, '人工核对的成分')

    def test_required_manual_fields_and_strict_rx_are_not_defaulted(self):
        cases = [{key: value for key, value in self.body.items() if key != field}
                 for field in ('shelf_location', 'stock', 'is_rx')]
        cases += [{**self.body, 'stock': -1}, {**self.body, 'stock': '7'},
                  {**self.body, 'is_rx': None}, {**self.body, 'is_rx': 'false'},
                  {**self.body, 'shelf_location': ' '}, {**self.body, 'id': 999}]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.client.post('/api/pharmacy/confirm_import', json=body).status_code, 422)
        self.assertEqual(self.counts(), (0, 0))

    def test_duplicate_confirmation_is_conflict_not_another_stock_increment(self):
        self.assertEqual(self.client.post('/api/pharmacy/confirm_import', json=self.body).status_code, 201)
        response = self.client.post('/api/pharmacy/confirm_import', json=self.body)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.counts(), (1, 1))

    def test_invalid_and_oversize_images_do_not_invoke_vision_or_write(self):
        with patch.object(FakeVision, 'extract') as model:
            self.assertEqual(self.ocr(content=b'not an image').status_code, 422)
            self.assertEqual(self.ocr(content=b'x' * (5 * 1024 * 1024 + 1)).status_code, 413)
            model.assert_not_called()
        self.assertEqual(self.counts(), (0, 0))

    def test_model_cannot_supply_stock_or_convert_string_to_rx_bool(self):
        for result in ({'name': '药', 'stock': 100}, {'name': '药', 'is_rx': 'false'}):
            class BadVision:
                def extract(self, *args):
                    return result
            with self.app(BadVision) as client:
                self.assertEqual(self.ocr(client).status_code, 502)
        self.assertEqual(self.counts(), (0, 0))

    def test_details_failure_rolls_back_main_row(self):
        def fail(*args):
            raise RuntimeError('synthetic detail failure')
        event.listen(DrugDetails, 'before_insert', fail)
        try:
            with self.assertRaises(RuntimeError):
                import_confirmed(self.engine, ConfirmImport.model_validate(self.body))
        finally:
            event.remove(DrugDetails, 'before_insert', fail)
        self.assertEqual(self.counts(), (0, 0))

    def test_vision_gateway_sends_image_and_json_mode_without_network(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, limit):
                return json.dumps({'choices': [{'message': {'role': 'assistant', 'content':
                    '{"name":"演示新药","is_rx":null}'}}]}).encode()
        with patch.dict('os.environ', {'MED_AGENT_VISION_MODEL': 'test-vision',
                                      'MED_AGENT_VISION_API_KEY': 'fake-key',
                                      'MED_AGENT_VISION_BASE_URL': 'https://vision.example/v1'}), patch(
                'meditouch.inventory_import.urllib.request.urlopen', return_value=Response()) as send:
            result = VisionGateway().extract(image_bytes(), 'image/png')
        payload = json.loads(send.call_args.args[0].data)
        self.assertEqual(payload['response_format'], {'type': 'json_object'})
        self.assertTrue(payload['messages'][1]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
        self.assertIsNone(result['is_rx'])

    def test_unconfigured_vision_is_explicit_and_manual_import_still_works(self):
        with patch.dict('os.environ', {'MED_AGENT_VISION_MODEL': '', 'MED_AGENT_VISION_API_KEY': '',
                                      'MED_AGENT_API_KEY': ''}), self.app(None) as client:
            self.assertEqual(self.ocr(client).status_code, 503)
            response = client.post('/api/pharmacy/confirm_import', json={**self.body, 'stock': 0, 'is_rx': True})
            self.assertEqual(response.status_code, 201)
        self.assertEqual(self.counts(), (1, 1))

    def test_default_writer_is_store_bound_and_agent_reader_stays_readonly(self):
        from sqlalchemy.exc import OperationalError
        from meditouch.pharmacy_graph import inventory_engine_for_store, inventory_write_engine_for_store
        path = Path(self.temp.name) / 'stock.db'
        with patch.dict('os.environ', {'MED_AGENT_INVENTORY_DB': str(path),
                                      'MED_AGENT_INVENTORY_STORE_ID': 'A'}), TestClient(create_app(
                vision_factory=FakeVision)) as client:
            response = client.post('/api/pharmacy/confirm_import', json=self.body)
            self.assertEqual(response.status_code, 201)
            reader, writer = inventory_engine_for_store('A'), inventory_write_engine_for_store('A')
            self.addCleanup(reader.dispose)
            self.addCleanup(writer.dispose)
            with reader.connect() as connection:
                with self.assertRaises(OperationalError):
                    connection.exec_driver_sql('UPDATE drug_inventory SET stock=99')
            self.assertEqual(client.post('/api/pharmacy/confirm_import', json={**self.body,
                             'store_id': 'B', 'name': '另一商品'}).status_code, 403)
