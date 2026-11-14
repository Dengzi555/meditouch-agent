"""Local product facts and absence messages must match successful inventory observations."""
import unittest
from sqlmodel import Session

from meditouch.inventory_models import DrugInventory, DrugDetails, create_inventory_engine, create_inventory_tables
from meditouch.inventory_tools import search_inventory_batch
from meditouch.pharmacy_ui import build_ui_cards


class InventoryDisplayTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_inventory_engine('sqlite://')
        create_inventory_tables(self.engine)
        self.addCleanup(self.engine.dispose)
        with Session(self.engine) as session:
            item = DrugInventory(name='演示药胶囊', brand='演示品牌', stock=3, shelf_location='A2', is_rx=True)
            session.add(item); session.flush()
            session.add(DrugDetails(inventory_id=item.id, ingredients='演示成分', dosage_form='胶囊',
                                    specification='演示规格', contraindications='演示标签禁用'))
            session.commit()

    def state(self, response, **changes):
        return {'inventory_results': response['items'], 'inventory_status': response['status'],
                'inventory_lookup_records': response.get('query_records', {}), 'safety_status': 'unknown',
                'safety_checks': [], 'safety_reason': '测试未核验', 'excluded_drugs': [], **changes}

    def test_exact_brand_and_all_local_details_are_returned(self):
        result = search_inventory_batch(self.engine, ['演示品牌'])
        self.assertEqual(result['items'][0]['name_match'], 'brand_exact')
        card = next(item for item in build_ui_cards(self.state(result)) if item['type'] == 'drug_candidate')
        self.assertEqual(card['drug']['brand'], '演示品牌')
        self.assertEqual(card['drug']['specification'], '演示规格')
        self.assertEqual(card['drug']['ingredients'], '演示成分')
        self.assertEqual(card['drug']['contraindications'], '演示标签禁用')
        self.assertTrue(card['drug']['is_rx'])

    def test_partial_batch_reports_missing_drug_without_hiding_existing_one(self):
        result = search_inventory_batch(self.engine, ['演示品牌', '未收录药'])
        cards = build_ui_cards(self.state(result))
        notice = next(item for item in cards if item['id'] == 'local-not-found')
        self.assertIn('未收录药', notice['message'])
        self.assertNotIn('演示品牌', notice['message'])
        self.assertTrue(any(item['type'] == 'drug_candidate' for item in cards))

    def test_exclusions_and_database_errors_do_not_claim_absence(self):
        response = search_inventory_batch(self.engine, ['演示品牌'], excluded_drugs=['演示药胶囊'])
        self.assertEqual(response['query_records']['演示品牌']['status'], 'unconfirmed')
        self.assertFalse(any(item['id'] == 'local-not-found' for item in build_ui_cards(self.state(response))))
        response = {'status': 'database_unavailable', 'items': [], 'query_records': {
            '演示品牌': {'status': 'database_unavailable', 'items': []}}}
        cards = build_ui_cards(self.state(response))
        self.assertFalse(any('没有匹配' in item.get('message', '') for item in cards))

    def test_intercept_keeps_product_facts_but_not_recommendation(self):
        response = search_inventory_batch(self.engine, ['演示品牌'])
        value = self.state(response, safety_status='unsafe', excluded_drugs=['演示药胶囊'],
                           excluded_reasons={'演示药胶囊': '演示标签禁用'})
        cards = build_ui_cards(value)
        blocked = next(item for item in cards if item['type'] == 'intercept_warning')
        self.assertIn('货架 A2', blocked['items'][0]['inventory_details'])
        self.assertIn('库存 3', blocked['items'][0]['inventory_details'])
        self.assertFalse(any(item['type'] == 'drug_candidate' for item in cards))

    def test_stockout_is_not_absent_and_keeps_full_record(self):
        response = search_inventory_batch(self.engine, ['演示品牌'])
        item = {**response['items'][0], 'stock': 0}
        cards = build_ui_cards(self.state(response, stockout_results=[item]))
        notice = next(card for card in cards if card['id'] == 'stockout')
        self.assertIn('演示规格', notice['message'])
        self.assertIn('库存为0', notice['message'])
        self.assertFalse(any(card['id'] == 'local-not-found' for card in cards))
