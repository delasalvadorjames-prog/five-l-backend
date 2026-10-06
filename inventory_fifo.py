import os
import uuid
from datetime import date, datetime, timedelta
from typing import List, Optional
from urllib.parse import quote_plus

import pymysql
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
    text,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

pymysql.install_as_MySQLdb()


def build_mysql_url() -> str:
    explicit_url = os.getenv("MYSQL_URL")
    if explicit_url:
        return explicit_url

    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    db_name = os.getenv("DB_NAME", "five-l")

    user_part = quote_plus(user or "root")
    password_part = quote_plus(password) if password else ""
    auth = f"{user_part}:{password_part}" if password_part else user_part
    return f"mysql://{auth}@{host}:{port}/{db_name}"


engine = create_engine(build_mysql_url(), pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
Base = declarative_base()
router = APIRouter(tags=["FIFO Inventory"])


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(255), index=True, nullable=False)
    category = Column(String(120), index=True, nullable=True)
    unit_price = Column(Float, nullable=False, default=0.0)
    reorder_level = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    batches = relationship("InventoryBatch", back_populates="product")


class InventoryBatch(Base):
    __tablename__ = "inventory_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False, index=True)
    received_date = Column(Date, nullable=False, index=True)
    expiry_date = Column(Date, nullable=False, index=True)
    supplier = Column(String(255), nullable=True)
    batch_reference = Column(String(120), nullable=True)
    is_removed = Column(Boolean, nullable=False, default=False, index=True)
    removed_reason = Column(String(120), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    product = relationship("Product", back_populates="batches")


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sale_number = Column(String(80), unique=True, index=True, nullable=False)
    cashier_name = Column(String(255), nullable=True)
    customer_name = Column(String(255), nullable=True)
    total_amount = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, server_default=func.now(), index=True)

    items = relationship("SaleItem", back_populates="sale", cascade="all, delete-orphan")


class SaleItem(Base):
    __tablename__ = "sale_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sale_id = Column(Integer, ForeignKey("sales.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    batch_id = Column(Integer, ForeignKey("inventory_batches.id"), nullable=False, index=True)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    line_total = Column(Float, nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    sale = relationship("Sale", back_populates="items")


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ProductCreate(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=255)
    category: Optional[str] = None
    unit_price: float = Field(0, ge=0)
    reorder_level: int = Field(0, ge=0)


class AddBatchRequest(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)
    received_date: date
    expiry_date: date
    supplier: Optional[str] = None
    batch_reference: Optional[str] = None


class BatchOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    quantity: int
    remaining_quantity: int
    received_date: date
    expiry_date: date
    supplier: Optional[str] = None
    batch_reference: Optional[str] = None
    is_removed: bool


class AvailableStockOut(BaseModel):
    product_id: int
    available_stock: int
    status: str
    near_expiry_count: int
    nearest_expiry_date: Optional[date] = None


class InventoryProductOut(BaseModel):
    id: int
    sku: str
    name: str
    category: Optional[str]
    unit_price: float
    available_stock: int
    status: str
    near_expiry_count: int
    nearest_expiry_date: Optional[date]


class SaleLineRequest(BaseModel):
    product_id: int
    quantity: int = Field(..., gt=0)
    unit_price: Optional[float] = Field(None, ge=0)


class ProcessSaleRequest(BaseModel):
    items: List[SaleLineRequest] = Field(..., min_length=1)
    cashier_name: Optional[str] = None
    customer_name: Optional[str] = None


class SaleItemOut(BaseModel):
    product_id: int
    batch_id: int
    quantity: int
    unit_price: float
    line_total: float


class SaleOut(BaseModel):
    sale_id: int
    sale_number: str
    total_amount: float
    items: List[SaleItemOut]


class RemoveExpiredOut(BaseModel):
    removed_batches: int
    removed_units: int


def gen_sale_number() -> str:
    return f"SALE-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


def add_stock_batch(db: Session, payload: AddBatchRequest) -> InventoryBatch:
    if payload.expiry_date <= date.today():
        raise HTTPException(status_code=400, detail="Cannot add an already expired batch.")

    product = db.query(Product).filter(Product.id == payload.product_id, Product.is_active.is_(True)).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")

    batch = InventoryBatch(
        product_id=payload.product_id,
        quantity=payload.quantity,
        remaining_quantity=payload.quantity,
        received_date=payload.received_date,
        expiry_date=payload.expiry_date,
        supplier=payload.supplier,
        batch_reference=payload.batch_reference,
    )
    db.add(batch)
    db.flush()
    return batch


def get_available_stock(db: Session, product_id: int) -> AvailableStockOut:
    today = date.today()
    
    # Get all active batches (remaining_quantity > 0, not removed) to compute nearest_expiry and near_expiry_count
    all_active_batches = (
        db.query(InventoryBatch)
        .filter(
            InventoryBatch.product_id == product_id,
            InventoryBatch.remaining_quantity > 0,
            InventoryBatch.is_removed.is_(False),
        )
        .all()
    )
    
    # Exclude batches with 30 days or less remaining
    limit_date = today + timedelta(days=30)
    available_stock = sum(
        batch.remaining_quantity for batch in all_active_batches if batch.expiry_date > limit_date
    )
    
    # Near expiry is 31-60 days remaining (inclusive)
    near_expiry_limit = today + timedelta(days=60)
    near_expiry_count = sum(
        batch.remaining_quantity for batch in all_active_batches if limit_date < batch.expiry_date <= near_expiry_limit
    )
    
    # Nearest expiry date among all active batches (so we can display next expiry in the UI even if expired or within 30 days)
    nearest_expiry = min((batch.expiry_date for batch in all_active_batches), default=None)

    return AvailableStockOut(
        product_id=product_id,
        available_stock=available_stock,
        status="OUT_OF_STOCK" if available_stock <= 0 else "AVAILABLE",
        near_expiry_count=near_expiry_count,
        nearest_expiry_date=nearest_expiry,
    )


def allocate_fifo_batches(db: Session, product_id: int, quantity: int) -> List[tuple[InventoryBatch, int]]:
    today = date.today()
    limit_date = today + timedelta(days=30)
    batches = (
        db.query(InventoryBatch)
        .filter(
            InventoryBatch.product_id == product_id,
            InventoryBatch.remaining_quantity > 0,
            InventoryBatch.expiry_date > limit_date,
            InventoryBatch.is_removed.is_(False),
        )
        .order_by(
            InventoryBatch.expiry_date.asc(),
            InventoryBatch.received_date.is_(None).asc(),
            InventoryBatch.received_date.asc(),
            InventoryBatch.id.asc(),
        )
        .with_for_update()
        .all()
    )

    available = sum(batch.remaining_quantity for batch in batches)
    if available < quantity:
        raise HTTPException(
            status_code=409,
            detail=f"Insufficient stock (excluding expired and batches within 30 days of expiry). Requested {quantity}, available {available}.",
        )

    allocations: List[tuple[InventoryBatch, int]] = []
    remaining = quantity
    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.remaining_quantity, remaining)
        allocations.append((batch, take))
        remaining -= take

    return allocations


def process_sale_fifo(db: Session, payload: ProcessSaleRequest) -> Sale:
    sale = Sale(
        sale_number=gen_sale_number(),
        cashier_name=payload.cashier_name,
        customer_name=payload.customer_name,
        total_amount=0,
    )
    db.add(sale)
    db.flush()

    total_amount = 0.0
    for item in payload.items:
        product = (
            db.query(Product)
            .filter(Product.id == item.product_id, Product.is_active.is_(True))
            .with_for_update()
            .first()
        )
        if not product:
            raise HTTPException(status_code=404, detail=f"Product {item.product_id} not found.")

        unit_price = item.unit_price if item.unit_price is not None else product.unit_price

        # FEFO is enforced by locking all valid batches and consuming the
        # batch with the earliest stored actual expiry date first.
        # Expired batches are excluded by expiry_date > today before allocation, so POS cannot sell expired stock.
        # Partial deductions leave the batch open with a smaller remaining_quantity; larger sales consume many batches.
        from datetime import date
        today = date.today()
        allocations = allocate_fifo_batches(db, item.product_id, item.quantity)
        for batch, take in allocations:
            days_until_expiry = (batch.expiry_date - today).days
            if days_until_expiry <= 30:
                raise HTTPException(
                    status_code=400,
                    detail=f"Product batch {batch.id} cannot be sold as it has {days_until_expiry} days remaining before expiry (30 days or less remaining MUST NOT be sold)."
                )
            batch.remaining_quantity -= take
            line_total = round(unit_price * take, 2)
            total_amount += line_total
            db.add(
                SaleItem(
                    sale_id=sale.id,
                    product_id=item.product_id,
                    batch_id=batch.id,
                    quantity=take,
                    unit_price=unit_price,
                    line_total=line_total,
                )
            )

    sale.total_amount = round(total_amount, 2)
    db.flush()
    return sale


def remove_expired_batches(db: Session) -> RemoveExpiredOut:
    today = date.today()
    expired_batches = (
        db.query(InventoryBatch)
        .filter(
            InventoryBatch.remaining_quantity > 0,
            InventoryBatch.expiry_date <= today,
            InventoryBatch.is_removed.is_(False),
        )
        .with_for_update()
        .all()
    )

    removed_units = sum(batch.remaining_quantity for batch in expired_batches)
    for batch in expired_batches:
        batch.remaining_quantity = 0
        batch.is_removed = True
        batch.removed_reason = "expired"

    return RemoveExpiredOut(removed_batches=len(expired_batches), removed_units=removed_units)


@router.post("/products", response_model=InventoryProductOut, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, db: Session = Depends(get_db)):
    product = Product(
        sku=payload.sku.strip(),
        name=payload.name.strip(),
        category=payload.category,
        unit_price=payload.unit_price,
        reorder_level=payload.reorder_level,
    )
    db.add(product)
    try:
        db.commit()
        db.refresh(product)
        stock = get_available_stock(db, product.id)
        return InventoryProductOut(
            id=product.id,
            sku=product.sku,
            name=product.name,
            category=product.category,
            unit_price=product.unit_price,
            available_stock=stock.available_stock,
            status=stock.status,
            near_expiry_count=stock.near_expiry_count,
            nearest_expiry_date=stock.nearest_expiry_date,
        )
    except Exception:
        db.rollback()
        raise


@router.get("/products", response_model=List[InventoryProductOut])
def list_products(db: Session = Depends(get_db)):
    products = db.query(Product).filter(Product.is_active.is_(True)).order_by(Product.name.asc()).all()
    rows: List[InventoryProductOut] = []
    for product in products:
        stock = get_available_stock(db, product.id)
        rows.append(
            InventoryProductOut(
                id=product.id,
                sku=product.sku,
                name=product.name,
                category=product.category,
                unit_price=product.unit_price,
                available_stock=stock.available_stock,
                status=stock.status,
                near_expiry_count=stock.near_expiry_count,
                nearest_expiry_date=stock.nearest_expiry_date,
            )
        )
    return rows


@router.post("/inventory/add-batch", response_model=BatchOut, status_code=status.HTTP_201_CREATED)
def add_batch(payload: AddBatchRequest, db: Session = Depends(get_db)):
    try:
        batch = add_stock_batch(db, payload)
        db.commit()
        db.refresh(batch)
        return batch
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/inventory/available/{product_id}", response_model=AvailableStockOut)
def get_inventory_available(product_id: int, db: Session = Depends(get_db)):
    product = db.query(Product.id).filter(Product.id == product_id, Product.is_active.is_(True)).first()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    return get_available_stock(db, product_id)


@router.get("/inventory/{product_id}", response_model=List[BatchOut])
def get_inventory(product_id: int, db: Session = Depends(get_db)):
    return (
        db.query(InventoryBatch)
        .filter(InventoryBatch.product_id == product_id)
        .order_by(
            InventoryBatch.expiry_date.asc(),
            InventoryBatch.received_date.is_(None).asc(),
            InventoryBatch.received_date.asc(),
            InventoryBatch.id.asc(),
        )
        .all()
    )


@router.post("/sales/process", response_model=SaleOut, status_code=status.HTTP_201_CREATED)
def process_sale(payload: ProcessSaleRequest, db: Session = Depends(get_db)):
    try:
        sale = process_sale_fifo(db, payload)
        db.commit()
        db.refresh(sale)
        return SaleOut(
            sale_id=sale.id,
            sale_number=sale.sale_number,
            total_amount=sale.total_amount,
            items=[
                SaleItemOut(
                    product_id=item.product_id,
                    batch_id=item.batch_id,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    line_total=item.line_total,
                )
                for item in sale.items
            ],
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/inventory/remove-expired", response_model=RemoveExpiredOut)
def remove_expired(db: Session = Depends(get_db)):
    try:
        result = remove_expired_batches(db)
        db.commit()
        return result
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
