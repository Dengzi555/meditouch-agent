"""Isolated synthetic stock and contraindications; no clinical efficacy evaluation."""
import unittest

from sqlmodel import Session

from meditouch.inventory_models import (
    DrugDetails, DrugInventory, create_inventory_engine, create_inventory_tables,
)
from meditouch.inventory_tools import build_inventory_tools


class InventoryToolTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_inventory_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        create_inventory_tables(self.engine)
        self.search, self.safety = build_inventory_tools(
            self.engine, symptom_terms={'演示症状': ['演示成分']})

    def add(self, name='演示甲', brand=None, stock=1, is_rx=False, contraindications=None,
            ingredients=None, specification=None):
        with Session(self.engine) as session:
            drug = DrugInventory(name=name, brand=brand, stock=stock, is_rx=is_rx,
                                 shelf_location='3排2号')
            session.add(drug)
            session.commit()
            session.refresh(drug)
            if any(value is not None for value in (contraindications, ingredients, specification)):
                session.add(DrugDetails(inventory_id=drug.id, contraindications=contraindications,
                                        ingredients=ingredients, specification=specification))
                session.commit()
            return drug.id

    def test_name_brand_and_stock_warnings(self):
        self.add(name='演示甲缓释胶囊', brand='演示品牌', stock=0, is_rx=True,
                 contraindications='标签甲禁用')
        for query in ('演示甲', '演示品牌'):
            with self.subTest(query=query):
                result = self.search.invoke({'drug_name': query})
                item = result['items'][0]
                self.assertEqual(item['status'], 'out_of_stock')
                self.assertEqual(item['warning'], '处方药需药师审核')
                self.assertEqual(item['shelf_location'], '3排2号')
                self.assertEqual(item['contraindications'], '标签甲禁用')

    def test_symptom_joins_ingredients_and_intersects_name(self):
        self.add(brand='A', ingredients='演示成分')
        self.add(name='演示乙', ingredients='其他成分')
        result = self.search.invoke({'symptom': '演示症状'})
        self.assertEqual([item['name'] for item in result['items']], ['演示甲'])
        self.assertEqual(self.search.invoke({'symptom': '演示症状', 'drug_name': '演示乙'})['items'], [])
        self.assertEqual(self.search.invoke({'symptom': '没有映射'})['status'], 'unsupported_symptom')

    def test_blank_and_wildcards_do_not_return_everything(self):
        self.add()
        self.assertEqual(self.search.invoke({})['status'], 'invalid_input')
        self.assertEqual(self.search.invoke({'drug_name': ' '})['status'], 'invalid_input')
        for value in ('%', '_', "' OR 1=1 --"):
            self.assertEqual(self.search.invoke({'drug_name': value})['items'], [])

    def test_matching_tag_blocks_with_original_reason(self):
        self.add(contraindications='标签甲禁用')
        result = self.safety.invoke({'drug_name': '演示甲', 'customer_tags': ['标签甲']})
        self.assertIs(result['is_safe'], False)
        self.assertEqual(result['reason'], '标签甲禁用')

    def test_empty_and_nonmatching_data_never_clear_safety(self):
        for name, contraindications in (('无详情', None), ('空白', ' '), ('有数据', '标签甲禁用')):
            self.add(name=name, contraindications=contraindications)
            result = self.safety.invoke({'drug_name': name, 'customer_tags': ['标签乙']})
            self.assertEqual(result['is_safe'], 'unknown')
            if name != '有数据':
                self.assertEqual(result['reason'], '缺乏禁忌数据，建议咨询药师')
        self.assertEqual(self.safety.invoke({'drug_name': '有数据', 'customer_tags': []})['is_safe'], 'unknown')

    def test_ambiguous_name_does_not_pick_first_product(self):
        self.add(specification='演示规格甲', contraindications='标签甲禁用')
        self.add(specification='演示规格乙', contraindications='标签乙禁用')
        result = self.safety.invoke({'drug_name': '演示甲', 'customer_tags': ['标签甲']})
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(result['is_safe'], 'unknown')

    def test_engine_is_not_exposed_and_stores_are_isolated(self):
        self.add()
        self.assertEqual(set(self.search.args), {'symptom', 'drug_name'})
        self.assertEqual(set(self.safety.args), {'drug_name', 'customer_tags'})
        other = create_inventory_engine('sqlite://')
        self.addCleanup(other.dispose)
        create_inventory_tables(other)
        search, _ = build_inventory_tools(other)
        self.assertEqual(search.invoke({'drug_name': '演示甲'})['items'], [])

    def test_database_failure_does_not_leak_details_or_claim_safety(self):
        empty = create_inventory_engine('sqlite://')
        self.addCleanup(empty.dispose)
        search, safety = build_inventory_tools(empty)
        self.assertEqual(search.invoke({'drug_name': '演示'})['status'], 'database_unavailable')
        result = safety.invoke({'drug_name': '演示', 'customer_tags': ['标签甲']})
        self.assertEqual(result['is_safe'], 'unknown')
        self.assertNotIn('SELECT', str(result))

    def test_result_limit_is_explicit(self):
        for index in range(51):
            self.add(name=f'演示{index}')
        result = self.search.invoke({'drug_name': '演示'})
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['items']), 50)
