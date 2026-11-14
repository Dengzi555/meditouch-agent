"""Seed an isolated MOCK SQLite database. No clinical facts or safety clearance are asserted.

Run from repository root: python seed_data.py
Existing matching records are preserved, not overwritten. Never truncate tables.
"""
import argparse
import json
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, func, select

from meditouch.inventory_models import (
    DrugDetails, DrugInventory, MOCK_APPLICATION_ID, create_inventory_engine, create_inventory_tables,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATABASE = ROOT / 'data' / 'pharmacy_inventory_mock.db'

# All four rows are fixtures requested for UI demonstration, NOT verified product labels.
# Empty contraindications mean unknown; they never imply safe or an approved substitute.
MOCK_DRUGS = (
    {'name': '布洛芬缓释胶囊', 'brand': '芬必得', 'shelf_location': '3排2号',
     'stock': 15, 'is_rx': False, 'dosage_form': '胶囊', 'contraindications': None},
    {'name': '复方感冒灵颗粒', 'brand': '999', 'shelf_location': '1排1号',
     'stock': 10, 'is_rx': False, 'dosage_form': '颗粒', 'contraindications': '孕妇禁用'},
    {'name': '板蓝根颗粒', 'brand': '白云山', 'shelf_location': '2排3号',
     'stock': 20, 'is_rx': False, 'dosage_form': '颗粒', 'contraindications': None},
    {'name': '阿莫西林胶囊', 'brand': '联邦', 'shelf_location': '5排1号',
     'stock': 0, 'is_rx': True, 'dosage_form': '胶囊', 'contraindications': None},
)


def seed_database(database=DEFAULT_DATABASE):
    """Create two tables and idempotently insert linked mock rows in one transaction.

    An unmarked populated inventory or an unrelated SQLite database is refused to
    prevent mixing fixtures into business data. Missing detail rows are repaired.
    The mock marker is committed with the rows; an exception rolls back all inserts.
    """
    path = Path(database).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_inventory_engine('sqlite:///' + path.as_posix())
    inserted, skipped, details_inserted = 0, 0, 0
    try:
        if set(inspect(engine).get_table_names()) - {'drug_inventory', 'drug_details', 'inventory_control', 'stock_movement'}:
            raise ValueError('目标不是独立库存库，请使用独立的演示数据库')
        create_inventory_tables(engine)
        with Session(engine) as session, session.begin():
            # Serialize concurrent seeds despite the intentionally non-unique drug name.
            session.exec(text('BEGIN IMMEDIATE'))
            marker = session.exec(text('PRAGMA application_id')).scalar()
            existing = session.exec(select(func.count()).select_from(DrugInventory)).one()
            existing_details = session.exec(select(func.count()).select_from(DrugDetails)).one()
            if marker != MOCK_APPLICATION_ID and (existing or existing_details):
                raise ValueError('目标库已有未标记的数据，请改用独立演示库，脚本不会清空或混入数据')
            if marker not in (0, MOCK_APPLICATION_ID):
                raise ValueError('目标数据库已属于其他应用，请使用独立演示库')
            session.exec(text(f'PRAGMA application_id = {MOCK_APPLICATION_ID}'))
            for row in MOCK_DRUGS:
                matches = session.exec(select(DrugInventory).where(
                    DrugInventory.name == row['name'], DrugInventory.brand == row['brand'],
                    DrugInventory.shelf_location == row['shelf_location'])).all()
                if len(matches) > 1:
                    raise ValueError('演示库已有重复商品，请先人工核对，脚本不会自行删除')
                if matches:
                    drug = matches[0]
                    skipped += 1
                else:
                    drug = DrugInventory.model_validate({key: row[key] for key in
                        ('name', 'brand', 'shelf_location', 'stock', 'is_rx')})
                    session.add(drug)
                    session.flush()  # Obtain the generated primary key before inserting the FK.
                    inserted += 1
                details = session.exec(select(DrugDetails).where(DrugDetails.inventory_id == drug.id)).first()
                if details is None:
                    session.add(DrugDetails(inventory_id=drug.id, dosage_form=row['dosage_form'],
                                            contraindications=row['contraindications']))
                    details_inserted += 1
            session.flush()
            total = session.exec(select(func.count()).select_from(DrugInventory)).one()
        return {'database': str(path), 'data_mode': 'mock', 'inserted': inserted, 'skipped': skipped,
                'details_inserted': details_inserted, 'total_inventory': total,
                'scope': 'UI fixtures only; empty contraindications remain unknown, never safe'}
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description='初始化独立 Mock 库存库；不清空、不覆盖现有记录')
    parser.add_argument('--db', type=Path, default=DEFAULT_DATABASE, help='演示 SQLite 数据库路径')
    args = parser.parse_args()
    try:
        result = seed_database(args.db)
    except ValueError as exc:
        parser.exit(1, str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
