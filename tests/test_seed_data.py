"""Mock seed idempotency, foreign keys, data isolation and expected tool outcomes."""
from pathlib import Path
import tempfile
import unittest

from sqlmodel import Session, func, select

from meditouch.inventory_models import (
    DrugDetails, DrugInventory, create_inventory_engine, create_inventory_tables, inventory_is_mock,
)
from meditouch.inventory_tools import build_inventory_tools
from scripts.seed_data import seed_database


class SeedDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'mock.db'

    def test_twice_inserts_four_linked_rows_and_then_skips(self):
        first, second = seed_database(self.path), seed_database(self.path)
        self.assertEqual((first['inserted'], first['details_inserted']), (4, 4))
        self.assertEqual((second['inserted'], second['skipped'], second['details_inserted']), (0, 4, 0))
        engine = create_inventory_engine('sqlite:///' + self.path.as_posix())
        self.addCleanup(engine.dispose)
        self.assertTrue(inventory_is_mock(engine))
        with Session(engine) as session:
            self.assertEqual(session.exec(select(func.count()).select_from(DrugDetails)).one(), 4)
            for drug in session.exec(select(DrugInventory)).all():
                self.assertIsNotNone(drug.details)

    def test_preserves_edited_stock_and_repairs_only_missing_details(self):
        seed_database(self.path)
        engine = create_inventory_engine('sqlite:///' + self.path.as_posix())
        self.addCleanup(engine.dispose)
        with Session(engine) as session:
            drug = session.exec(select(DrugInventory).where(DrugInventory.brand == '芬必得')).one()
            drug.stock = 9
            session.add(drug)
            session.delete(drug.details)
            session.commit()
        result = seed_database(self.path)
        self.assertEqual(result['details_inserted'], 1)
        with Session(engine) as session:
            drug = session.exec(select(DrugInventory).where(DrugInventory.brand == '芬必得')).one()
            self.assertEqual(drug.stock, 9)

    def test_repeat_seed_after_stock_management_keeps_ledger_and_quantities(self):
        from meditouch.inventory_management import init_management, EditDrug, MoveStock, edit_drug, move_stock, StockMovement
        seed_database(self.path)
        engine = create_inventory_engine('sqlite:///' + self.path.as_posix())
        self.addCleanup(engine.dispose)
        init_management(engine)
        with Session(engine) as session:
            drug = session.exec(select(DrugInventory).where(DrugInventory.brand == '芬必得')).one()
            inventory_id = drug.id
        edit_drug(engine, inventory_id, EditDrug(store_id='local', expected_version=0, name='布洛芬缓释胶囊',
            brand='芬必得', shelf_location='3排2号', is_rx=False, unit='盒'))
        move_stock(engine, inventory_id, MoveStock(store_id='local', request_id='repeat-seed-sale',
            expected_version=1, expected_stock=15, kind='sale', quantity=2, reason='合成售出'))
        self.assertEqual(seed_database(self.path)['inserted'], 0)
        with Session(engine) as session:
            self.assertEqual(session.get(DrugInventory, inventory_id).stock, 13)
            self.assertEqual(len(session.exec(select(StockMovement)).all()), 1)

    def test_refuses_mixing_into_an_unmarked_populated_inventory(self):
        engine = create_inventory_engine('sqlite:///' + self.path.as_posix())
        self.addCleanup(engine.dispose)
        create_inventory_tables(engine)
        with Session(engine) as session:
            session.add(DrugInventory(name='原有商品', shelf_location='A1', stock=3))
            session.commit()
        with self.assertRaises(ValueError):
            seed_database(self.path)
        self.assertFalse(inventory_is_mock(engine))
        with Session(engine) as session:
            self.assertEqual(session.exec(select(func.count()).select_from(DrugInventory)).one(), 1)

    def test_mock_outcomes_remain_unknown_blocked_and_out_of_stock(self):
        seed_database(self.path)
        engine = create_inventory_engine('sqlite:///' + self.path.as_posix())
        self.addCleanup(engine.dispose)
        search, safety = build_inventory_tools(engine)
        for name in ('布洛芬缓释胶囊', '板蓝根颗粒'):
            self.assertEqual(safety.invoke({'drug_name': name, 'customer_tags': ['孕妇']})['is_safe'], 'unknown')
        self.assertIs(safety.invoke({'drug_name': '复方感冒灵颗粒', 'customer_tags': ['孕妇']})['is_safe'], False)
        drug = search.invoke({'drug_name': '阿莫西林'})['items'][0]
        self.assertEqual(drug['stock'], 0)
        self.assertEqual(drug['status'], 'out_of_stock')
        self.assertEqual(drug['warning'], '处方药需药师审核')
