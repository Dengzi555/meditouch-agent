from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlmodel import Session, select

from meditouch.api import create_app
from meditouch.inventory_management import (
    EditDrug, MoveStock, StockMovement, InventoryProblem, edit_drug, move_stock, init_management,
)
from meditouch.inventory_models import DrugInventory, create_inventory_engine, create_inventory_tables
from meditouch.inventory_tools import search_inventory_batch


class InventoryManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_inventory_engine('sqlite:///' + (Path(self.temp.name) / 'stock.db').as_posix(),
                                               connect_args={'check_same_thread': False})
        self.addCleanup(self.engine.dispose)
        create_inventory_tables(self.engine)
        with Session(self.engine) as session:
            drug = DrugInventory(name='演示感冒药', brand='演示牌', shelf_location='A1', stock=10)
            session.add(drug); session.commit(); session.refresh(drug)
            self.drug_id = drug.id
        init_management(self.engine)
        def resolver(store):
            if store != 'A':
                raise ValueError()
            return self.engine
        self.client = TestClient(create_app(engine_resolver=resolver, write_engine_resolver=resolver))
        self.addCleanup(self.client.close)

    def edit(self, **kwargs):
        payload = dict(store_id='A', expected_version=0, name='演示感冒药', brand='演示牌',
                       shelf_location='A2', is_rx=False, unit='盒', specification='演示规格')
        payload.update(kwargs)
        return EditDrug(**payload)

    def movement(self, **kwargs):
        payload = dict(store_id='A', request_id=uuid4().hex, expected_version=1, expected_stock=10,
                       kind='sale', quantity=2, reason='柜台售出')
        payload.update(kwargs)
        return MoveStock(**payload)

    def stock(self):
        with Session(self.engine) as session:
            return session.get(DrugInventory, self.drug_id).stock

    def test_existing_stock_is_preserved_and_unit_must_be_confirmed(self):
        data = self.client.get('/api/pharmacy/inventory', params={'store_id': 'A'}).json()
        self.assertEqual(data['items'][0]['stock'], 10)
        self.assertEqual(data['items'][0]['unit'], '')
        response = self.client.post(f'/api/pharmacy/inventory/{self.drug_id}/movements',
            json=self.movement(expected_version=0).model_dump())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.stock(), 10)

    def test_sale_restock_return_adjustment_and_read_model_are_consistent(self):
        edit_drug(self.engine, self.drug_id, self.edit())
        version, current = 1, 10
        for kind, quantity, expected in [('sale', 2, 8), ('restock', 5, 13), ('return', 1, 14), ('adjustment', 0, 0)]:
            body = self.movement(kind=kind, quantity=quantity, expected_stock=current, expected_version=version)
            response = self.client.post(f'/api/pharmacy/inventory/{self.drug_id}/movements', json=body.model_dump())
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['movement']['after_stock'], expected)
            self.assertEqual(self.stock(), expected)
            version, current = version+1, expected
        ledger = self.client.get('/api/pharmacy/inventory/movements', params={'store_id': 'A'}).json()
        self.assertEqual(ledger['total'], 4)
        self.assertEqual(ledger['items'][0]['before_stock'], 14)
        result = search_inventory_batch(self.engine, ['演示感冒药'])
        self.assertIn('out_of_stock', str(result))

    def test_duplicate_submission_is_replayed_without_double_decrement(self):
        edit_drug(self.engine, self.drug_id, self.edit())
        body = self.movement()
        first = move_stock(self.engine, self.drug_id, body)
        replay = move_stock(self.engine, self.drug_id, body)
        self.assertFalse(first['replayed']); self.assertTrue(replay['replayed'])
        self.assertEqual(self.stock(), 8)
        with self.assertRaises(InventoryProblem):
            move_stock(self.engine, self.drug_id, body.model_copy(update={'quantity': 3}))

    def test_oversell_stale_version_and_invalid_inputs_never_change_stock(self):
        edit_drug(self.engine, self.drug_id, self.edit())
        for change in ({'quantity': 11}, {'expected_version': 0}, {'expected_stock': 9}):
            response = self.client.post(f'/api/pharmacy/inventory/{self.drug_id}/movements', json=self.movement(**change).model_dump())
            self.assertEqual(response.status_code, 409)
        for quantity in (-1, 1.5, True, '2'):
            payload = self.movement().model_dump(); payload['quantity'] = quantity
            self.assertEqual(self.client.post(f'/api/pharmacy/inventory/{self.drug_id}/movements', json=payload).status_code, 422)
        self.assertEqual(self.stock(), 10)

    def test_two_concurrent_sales_only_one_uses_the_snapshot(self):
        edit_drug(self.engine, self.drug_id, self.edit())
        def sale(_):
            try:
                move_stock(self.engine, self.drug_id, self.movement(quantity=7))
                return 200
            except InventoryProblem as error:
                return error.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(sale, range(2)))
        self.assertEqual(sorted(codes), [200, 409])
        self.assertEqual(self.stock(), 3)

    def test_ledger_insert_failure_rolls_back_stock(self):
        edit_drug(self.engine, self.drug_id, self.edit())
        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith('INSERT INTO STOCK_MOVEMENT'):
                raise RuntimeError('simulated ledger failure')
        event.listen(self.engine, 'before_cursor_execute', fail)
        try:
            with self.assertRaises(RuntimeError):
                move_stock(self.engine, self.drug_id, self.movement())
        finally:
            event.remove(self.engine, 'before_cursor_execute', fail)
        self.assertEqual(self.stock(), 10)
        with Session(self.engine) as session:
            self.assertEqual(session.exec(select(StockMovement)).all(), [])

    def test_edit_does_not_write_stock_and_unit_is_locked_after_a_movement(self):
        edit_drug(self.engine, self.drug_id, self.edit())
        move_stock(self.engine, self.drug_id, self.movement())
        with self.assertRaises(InventoryProblem):
            edit_drug(self.engine, self.drug_id, self.edit(expected_version=2, unit='瓶'))
        payload = self.edit(expected_version=2).model_dump(); payload['stock'] = 999
        self.assertEqual(self.client.post(f'/api/pharmacy/inventory/{self.drug_id}/edit', json=payload).status_code, 422)
        with self.assertRaises(InventoryProblem):
            edit_drug(self.engine, self.drug_id, self.edit(expected_version=0))
        self.assertEqual(self.stock(), 8)

    def test_search_pagination_scope_and_origin(self):
        edit_drug(self.engine, self.drug_id, self.edit())
        for term, count in [('演示牌', 1), ('A2', 1), ('%', 0), ('不存在', 0)]:
            response = self.client.get('/api/pharmacy/inventory', params={'store_id': 'A', 'q': term, 'limit': 1})
            self.assertEqual(response.json()['total'], count)
        self.assertEqual(self.client.get('/api/pharmacy/inventory', params={'store_id': 'B'}).status_code, 403)
        response = self.client.post(f'/api/pharmacy/inventory/{self.drug_id}/movements',
            headers={'origin': 'https://untrusted.example'}, json=self.movement().model_dump())
        self.assertEqual(response.status_code, 403)

    def test_edit_rejects_a_duplicate_product_identity(self):
        with Session(self.engine) as session:
            other = DrugInventory(name='另一演示药', shelf_location='B1', stock=1)
            session.add(other); session.commit(); session.refresh(other)
            other_id = other.id
        edit_drug(self.engine, self.drug_id, self.edit())
        with self.assertRaises(InventoryProblem):
            edit_drug(self.engine, other_id, self.edit())
        self.assertEqual(self.stock(), 10)


if __name__ == '__main__':
    unittest.main()
