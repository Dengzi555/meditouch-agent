"""Database constraints and sparse OCR/manual entry; isolated in-memory SQLite."""
import unittest

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from meditouch.inventory_models import (
    DrugDetails, DrugInventory, create_inventory_engine, create_inventory_tables,
)


class InventoryModelTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_inventory_engine('sqlite://')
        self.addCleanup(self.engine.dispose)
        create_inventory_tables(self.engine)

    def test_sparse_entry_needs_no_medical_details(self):
        drug = DrugInventory.model_validate({'name': ' 演示药品 ', 'shelf_location': '3排2号'})
        with Session(self.engine) as session:
            session.add(drug)
            session.commit()
            session.refresh(drug)
            self.assertEqual(drug.name, '演示药品')
            self.assertEqual(drug.stock, 0)
            self.assertFalse(drug.is_rx)
            self.assertIsNotNone(drug.created_at)
            self.assertIsNone(drug.details)

    def test_optional_details_can_be_added_later(self):
        with Session(self.engine) as session:
            drug = DrugInventory(name='演示药品', shelf_location='A1', stock=5)
            session.add(drug)
            session.commit()
            detail = DrugDetails(inventory_id=drug.id, specification='演示规格')
            session.add(detail)
            session.commit()
            session.refresh(drug)
            self.assertEqual(drug.details.specification, '演示规格')
            self.assertIsNone(drug.details.ingredients)
            self.assertEqual(drug.details.inventory.id, drug.id)
            self.assertEqual(len(session.exec(select(DrugInventory)).all()), 1)
            with self.assertRaises(IntegrityError):
                session.add(DrugDetails(inventory_id=drug.id))
                session.commit()
            session.rollback()

    def test_input_validation_rejects_blank_required_and_negative_stock(self):
        for fields in ({}, {'name': ' ', 'shelf_location': 'A1'},
                       {'name': '演示', 'shelf_location': ''},
                       {'name': '演示', 'shelf_location': 'A1', 'stock': -1}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                DrugInventory.model_validate(fields)

    def test_database_rejects_orphan_and_negative_stock_without_input_validation(self):
        for row in (DrugDetails(inventory_id=999),
                    DrugInventory(name='演示', shelf_location='A1', stock=-1)):
            with self.subTest(row=type(row).__name__), Session(self.engine) as session:
                session.add(row)
                with self.assertRaises(IntegrityError):
                    session.commit()
                session.rollback()
