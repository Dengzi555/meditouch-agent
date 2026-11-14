"""Lightweight single-store inventory; knowledge details are optional.

Use model_validate() for OCR/manual input: ORM constructors do not provide the
same validation guarantees. is_rx=False is an entry default, not OTC clearance.
"""
from datetime import datetime, timezone
from typing import Optional

from pydantic import ConfigDict
from sqlalchemy import CheckConstraint, event, text
from sqlmodel import Field, Relationship, SQLModel, create_engine

# SQLite application_id marks isolated mock inventory databases without adding business columns.
MOCK_APPLICATION_ID = 0x4D4F434B


def inventory_is_mock(engine):
    if engine.dialect.name != 'sqlite':
        return False
    with engine.connect() as connection:
        return connection.execute(text('PRAGMA application_id')).scalar() == MOCK_APPLICATION_ID


class DrugInventoryData(SQLModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200, index=True)
    brand: str | None = Field(default=None, max_length=100, index=True)
    shelf_location: str = Field(min_length=1, max_length=100)
    stock: int = Field(default=0, ge=0, nullable=False,
                       sa_column_kwargs={'server_default': '0'})
    is_rx: bool = Field(default=False, nullable=False,
                        sa_column_kwargs={'server_default': '0'})


class DrugInventory(DrugInventoryData, table=True):
    __tablename__ = 'drug_inventory'
    __table_args__ = (
        CheckConstraint('stock >= 0', name='ck_drug_inventory_stock_nonnegative'),
        CheckConstraint('length(trim(name)) > 0', name='ck_drug_inventory_name_not_blank'),
        CheckConstraint('length(trim(shelf_location)) > 0', name='ck_drug_inventory_shelf_not_blank'),
    )

    id: int | None = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc),
                                 nullable=False)
    details: Optional['DrugDetails'] = Relationship(
        back_populates='inventory', sa_relationship_kwargs={'uselist': False})


class DrugDetailsData(SQLModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    ingredients: str | None = Field(default=None, max_length=2000)
    contraindications: str | None = Field(default=None, max_length=4000)
    dosage_form: str | None = Field(default=None, max_length=100)
    specification: str | None = Field(default=None, max_length=200)


class DrugDetails(DrugDetailsData, table=True):
    __tablename__ = 'drug_details'

    id: int | None = Field(default=None, primary_key=True)
    inventory_id: int = Field(foreign_key='drug_inventory.id', unique=True,
                               index=True, nullable=False)
    inventory: DrugInventory = Relationship(back_populates='details')


def create_inventory_engine(database_url, **kwargs):
    """Create an engine without opening/mutating a database on module import."""
    engine = create_engine(database_url, **kwargs)
    if engine.dialect.name == 'sqlite':
        @event.listens_for(engine, 'connect')
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute('PRAGMA foreign_keys=ON')
            cursor.close()
    return engine


def create_inventory_tables(engine):
    """Explicit initialization; does not migrate existing schemas or load seed stock."""
    SQLModel.metadata.create_all(engine, tables=[DrugInventory.__table__, DrugDetails.__table__])
