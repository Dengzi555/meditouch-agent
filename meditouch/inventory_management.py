"""Employee-confirmed stock ledger. Never exposed as an Agent write tool."""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field as PField
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Field, Session, SQLModel, func, select

from meditouch.inventory_models import DrugInventory, DrugDetails

MAX_STOCK = 2_147_483_647


class InventoryControl(SQLModel, table=True):
    __tablename__ = 'inventory_control'
    inventory_id: int = Field(primary_key=True, foreign_key='drug_inventory.id')
    unit: str = ''  # Existing stock has no trusted counting unit.
    version: int = 0


class StockMovement(SQLModel, table=True):
    __tablename__ = 'stock_movement'
    id: int | None = Field(default=None, primary_key=True)
    request_id: str = Field(unique=True, index=True)
    inventory_id: int = Field(foreign_key='drug_inventory.id', index=True)
    kind: str
    quantity: int
    delta: int
    before_stock: int
    after_stock: int
    before_version: int
    unit: str
    drug_name: str
    reason: str
    operator: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Scoped(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    store_id: str = PField(min_length=1, max_length=100)


class EditDrug(Scoped):
    expected_version: int = PField(ge=0)
    name: str = PField(min_length=1, max_length=200)
    brand: str | None = PField(default=None, max_length=100)
    shelf_location: str = PField(min_length=1, max_length=100)
    is_rx: bool
    unit: Literal['盒', '瓶', '支', '袋', '板', '件']
    ingredients: str | None = PField(default=None, max_length=2000)
    contraindications: str | None = PField(default=None, max_length=4000)
    dosage_form: str | None = PField(default=None, max_length=100)
    specification: str | None = PField(default=None, max_length=200)


class MoveStock(Scoped):
    request_id: str = PField(min_length=8, max_length=64)
    expected_version: int = PField(ge=0)
    expected_stock: int = PField(ge=0, le=MAX_STOCK)
    kind: Literal['sale', 'restock', 'return', 'adjustment']
    quantity: int = PField(ge=0, le=MAX_STOCK)
    reason: str = PField(min_length=1, max_length=500)


class InventoryProblem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def init_management(engine):
    SQLModel.metadata.create_all(engine, tables=[InventoryControl.__table__, StockMovement.__table__])


def record(drug, details=None, control=None):
    result = drug.model_dump()
    result.update({key: getattr(details, key, None) for key in
                   ('ingredients', 'contraindications', 'dosage_form', 'specification')})
    result.update(unit=control.unit if control else '', version=control.version if control else 0)
    result['incomplete'] = not all(result.get(key) for key in ('unit', 'specification', 'ingredients', 'contraindications'))
    return result


def get_drug(session, inventory_id):
    drug = session.get(DrugInventory, inventory_id)
    if drug is None:
        raise InventoryProblem(404, '药品记录不存在')
    return drug, session.exec(select(DrugDetails).where(DrugDetails.inventory_id == inventory_id)).first(), session.get(InventoryControl, inventory_id)


def locked(session, engine):
    if engine.dialect.name == 'sqlite':
        session.exec(text('BEGIN IMMEDIATE'))


def edit_drug(engine, inventory_id, body):
    with Session(engine) as session, session.begin():
        locked(session, engine)
        drug, details, control = get_drug(session, inventory_id)
        if (control.version if control else 0) != body.expected_version:
            raise InventoryProblem(409, '资料已被更新，请刷新后重新核对')
        duplicate = session.exec(select(DrugInventory.id).outerjoin(DrugDetails,
            DrugDetails.inventory_id == DrugInventory.id).where(DrugInventory.id != inventory_id,
                DrugInventory.name == body.name, DrugInventory.brand == (body.brand or None),
                DrugInventory.shelf_location == body.shelf_location,
                DrugDetails.specification == (body.specification or None)).limit(1)).first()
        if duplicate is not None:
            raise InventoryProblem(409, '同名、品牌、规格和货架的商品已存在，请核对后再保存')
        if control is None:
            control = InventoryControl(inventory_id=inventory_id)
        # A unit change after transactions would make historical counts ambiguous.
        if control.unit and control.unit != body.unit and session.exec(select(StockMovement.id).where(
                StockMovement.inventory_id == inventory_id).limit(1)).first() is not None:
            raise InventoryProblem(409, '已有出入库记录，不能直接更改计数单位')
        for key in ('name', 'brand', 'shelf_location', 'is_rx'):
            value = getattr(body, key)
            setattr(drug, key, (value or None) if key == 'brand' else value)
        if details is None:
            details = DrugDetails(inventory_id=inventory_id)
        for key in ('ingredients', 'contraindications', 'dosage_form', 'specification'):
            setattr(details, key, getattr(body, key) or None)
        control.unit, control.version = body.unit, body.expected_version + 1
        session.add(drug); session.add(details); session.add(control)
        session.flush()
        return record(drug, details, control)


def move_stock(engine, inventory_id, body, *, operator='local-demo'):
    """SQLite write lock + version check + unique request ID, stock and ledger in one commit."""
    with Session(engine) as session, session.begin():
        locked(session, engine)
        previous = session.exec(select(StockMovement).where(StockMovement.request_id == body.request_id)).first()
        if previous:
            if (previous.inventory_id, previous.kind, previous.quantity, previous.reason, previous.before_stock, previous.before_version) != (
                    inventory_id, body.kind, body.quantity, body.reason, body.expected_stock, body.expected_version):
                raise InventoryProblem(409, '该交易编号已用于其他登记，请核对记录')
            return {'movement': previous.model_dump(), 'replayed': True}
        drug, _, control = get_drug(session, inventory_id)
        if not control or not control.unit:
            raise InventoryProblem(409, '请先编辑资料，确认库存计数单位')
        if control.version != body.expected_version or drug.stock != body.expected_stock:
            raise InventoryProblem(409, '库存或资料已变化，请刷新后重新确认')
        if body.kind != 'adjustment' and body.quantity == 0:
            raise InventoryProblem(422, '登记数量必须大于零')
        after = body.quantity if body.kind == 'adjustment' else drug.stock + (
            -body.quantity if body.kind == 'sale' else body.quantity)
        if after < 0:
            raise InventoryProblem(409, '库存不足，本次未登记')
        if after > MAX_STOCK:
            raise InventoryProblem(422, '库存数量超出范围')
        if after == drug.stock:
            raise InventoryProblem(422, '盘点结果与当前库存相同，无需调整')
        movement = StockMovement(request_id=body.request_id, inventory_id=inventory_id,
            kind=body.kind, quantity=body.quantity, delta=after-drug.stock, before_stock=drug.stock,
            after_stock=after, before_version=control.version, unit=control.unit, drug_name=drug.name, reason=body.reason, operator=operator)
        drug.stock = after
        control.version += 1
        session.add(drug); session.add(control); session.add(movement)
        session.flush()
        return {'movement': movement.model_dump(), 'replayed': False}


def management_router(resolve_writer, check_origin):
    router = APIRouter(prefix='/api/pharmacy/inventory')

    def engine_for(store_id):
        try:
            engine = resolve_writer(store_id)
            if engine is None:
                raise HTTPException(503, '请先配置本店库存')
            init_management(engine)  # Additive tables only; existing medicines and stock are untouched.
            return engine
        except ValueError:
            raise HTTPException(403, '门店不匹配或库存未初始化') from None
        except SQLAlchemyError:
            raise HTTPException(503, '库存数据库暂不可用') from None

    def execute(action):
        try:
            return action()
        except InventoryProblem as exc:
            raise HTTPException(exc.status, exc.message) from None
        except SQLAlchemyError:
            raise HTTPException(503, '数据库操作未完成，请核对记录后重试') from None

    @router.get('')
    def inventory(store_id: str = Query(min_length=1, max_length=100), q: str = Query('', max_length=200),
                  status: Literal['all', 'out', 'incomplete'] = 'all', offset: int = Query(0, ge=0),
                  limit: int = Query(30, ge=1, le=100)):
        engine = engine_for(store_id)
        def action():
            with Session(engine) as session:
                statement = select(DrugInventory, DrugDetails, InventoryControl).outerjoin(
                    DrugDetails, DrugDetails.inventory_id == DrugInventory.id).outerjoin(
                    InventoryControl, InventoryControl.inventory_id == DrugInventory.id)
                if q.strip():
                    term = q.strip()
                    statement = statement.where(DrugInventory.name.contains(term, autoescape=True)
                        | DrugInventory.brand.contains(term, autoescape=True)
                        | DrugInventory.shelf_location.contains(term, autoescape=True))
                if status == 'out':
                    statement = statement.where(DrugInventory.stock == 0)
                if status == 'incomplete':
                    statement = statement.where((InventoryControl.inventory_id.is_(None))
                        | (InventoryControl.unit == '') | (DrugDetails.specification.is_(None))
                        | (DrugDetails.specification == '') | (DrugDetails.ingredients.is_(None))
                        | (DrugDetails.ingredients == '') | (DrugDetails.contraindications.is_(None))
                        | (DrugDetails.contraindications == ''))
                total = session.exec(select(func.count()).select_from(statement.subquery())).one()
                rows = session.exec(statement.order_by(DrugInventory.id.desc()).offset(offset).limit(limit)).all()
                return {'items': [record(*row) for row in rows], 'total': total}
        return execute(action)

    @router.get('/movements')
    def movements(store_id: str = Query(min_length=1, max_length=100), inventory_id: int | None = None,
                  kind: Literal['all', 'sale', 'restock', 'return', 'adjustment'] = 'all',
                  offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
        engine = engine_for(store_id)
        def action():
            with Session(engine) as session:
                statement = select(StockMovement)
                if inventory_id is not None:
                    statement = statement.where(StockMovement.inventory_id == inventory_id)
                if kind != 'all':
                    statement = statement.where(StockMovement.kind == kind)
                total = session.exec(select(func.count()).select_from(statement.subquery())).one()
                items = session.exec(statement.order_by(StockMovement.id.desc()).offset(offset).limit(limit)).all()
                return {'items': [item.model_dump() for item in items], 'total': total}
        return execute(action)

    @router.post('/{inventory_id}/edit')
    def edit(inventory_id: int, body: EditDrug, request: Request):
        check_origin(request)
        return execute(lambda: edit_drug(engine_for(body.store_id), inventory_id, body))

    @router.post('/{inventory_id}/movements')
    def move(inventory_id: int, body: MoveStock, request: Request):
        check_origin(request)
        return execute(lambda: move_stock(engine_for(body.store_id), inventory_id, body))

    return router
