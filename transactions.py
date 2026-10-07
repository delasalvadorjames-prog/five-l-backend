import os
import math
import uuid
from datetime import datetime, timedelta, date, timezone
from typing import List, Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Float,
    DateTime,
    Text,
    Date,
    Boolean,
    and_,
    or_,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

# Build DB URL from env (mirror backend/main.py behavior)
def get_mysql_db_name() -> str:
    return os.getenv("DB_NAME", "five-l")


def build_mysql_url() -> str:
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    db_name = get_mysql_db_name()
    user_part = quote_plus(user or "root")
    password_part = quote_plus(password) if password else ""
    auth = f"{user_part}:{password_part}" if password_part else user_part
    return f"mysql+pymysql://{auth}@{host}:{port}/{db_name}"


def build_mysql_server_url() -> str:
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    user_part = quote_plus(user or "root")
    password_part = quote_plus(password) if password else ""
    auth = f"{user_part}:{password_part}" if password_part else user_part
    return f"mysql+pymysql://{auth}@{host}:{port}/"


def create_database_if_missing() -> None:
    db_name = get_mysql_db_name()
    if not db_name:
        return

    server_url = build_mysql_server_url()
    temp_engine = create_engine(server_url, pool_pre_ping=True)
    try:
        with temp_engine.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        temp_engine.dispose()


MYSQL_URL = build_mysql_url()
create_database_if_missing()
engine = create_engine(MYSQL_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
Base = declarative_base()


class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_number = Column(String(64), unique=True, index=True, nullable=False)
    sale_reference = Column(String(64), nullable=True, index=True)
    medicine_id = Column(Integer, nullable=False, index=True)
    medicine_name = Column(String(255), nullable=False)
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    customer_name = Column(String(255), nullable=True)
    cashier_name = Column(String(255), nullable=True)
    payment_method = Column(String(64), nullable=True)
    transaction_type = Column(String(64), nullable=False, server_default='sale')
    cash_received = Column(Float, nullable=True)
    change_amount = Column(Float, nullable=True)
    customer_id = Column(Integer, nullable=True, index=True)
    customer_id_number = Column(String(100), nullable=True)
    discount_eligible = Column(Boolean, nullable=True)
    eligible_subtotal = Column(Float, nullable=True)
    non_eligible_subtotal = Column(Float, nullable=True)
    sale_unit = Column(String(32), nullable=True)
    unit_factor = Column(Float, nullable=True)
    category = Column(String(255), nullable=True)
    classification = Column(String(64), nullable=True)
    dosage_form = Column(String(100), nullable=True)
    customer_type = Column(String(32), nullable=True)
    gross_amount = Column(Float, nullable=True)
    vatable_sales = Column(Float, nullable=True)
    vat_amount = Column(Float, nullable=True)
    vat_exempt_sales = Column(Float, nullable=True)
    discount_amount = Column(Float, nullable=True)
    senior_citizen_discount = Column(Float, nullable=True)
    pwd_discount = Column(Float, nullable=True)
    other_discount = Column(Float, nullable=True)
    net_sales = Column(Float, nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    owner_admin_id = Column(Integer, nullable=True, index=True)


class TransactionVoidRequest(Base):
    __tablename__ = "transaction_void_requests"
    id = Column(Integer, primary_key=True, autoincrement=True)
    transaction_id = Column(Integer, nullable=False, index=True)
    requested_by = Column(String(255), nullable=False)
    reason = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, server_default="PENDING", index=True)
    reviewed_by = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    reviewed_at = Column(DateTime, nullable=True)
    owner_admin_id = Column(Integer, nullable=True, index=True)


Base.metadata.create_all(bind=engine)


def ensure_sale_reference_column() -> None:
    """Add the sale grouping column for databases created before multi-item POS sales."""
    with engine.begin() as conn:
        exists = conn.execute(text("SHOW COLUMNS FROM transactions LIKE 'sale_reference'")).first()
        if not exists:
            conn.execute(text("ALTER TABLE transactions ADD COLUMN sale_reference VARCHAR(64) NULL"))
            conn.execute(text("CREATE INDEX ix_transactions_sale_reference ON transactions (sale_reference)"))


ensure_sale_reference_column()

router = APIRouter()


async def get_transaction_user(authorization: str = Header(None)):
    """Reuse the application's auth checks without importing main at module load time."""
    from main import get_current_user

    db = SessionLocal()
    try:
        return await get_current_user(authorization=authorization, db=db)
    finally:
        db.close()


class TransactionCreate(BaseModel):
    medicine_id: int
    quantity: int = Field(..., gt=0)
    price: float = Field(..., gt=0)
    sale_reference: Optional[str] = None
    customer_name: Optional[str] = None
    cashier_name: Optional[str] = None
    payment_method: Optional[str] = None
    transaction_type: Optional[str] = 'sale'
    cash_received: Optional[float] = None
    change_amount: Optional[float] = None
    customer_id: Optional[int] = None
    customer_id_number: Optional[str] = None
    discount_eligible: Optional[bool] = None
    eligible_subtotal: Optional[float] = None
    non_eligible_subtotal: Optional[float] = None
    sale_unit: Optional[str] = None
    unit_factor: Optional[float] = None
    category: Optional[str] = None
    classification: Optional[str] = None
    dosage_form: Optional[str] = None
    customer_type: Optional[str] = None
    gross_amount: Optional[float] = None
    vatable_sales: Optional[float] = None
    vat_amount: Optional[float] = None
    vat_exempt_sales: Optional[float] = None
    discount_amount: Optional[float] = None
    senior_citizen_discount: Optional[float] = None
    pwd_discount: Optional[float] = None
    other_discount: Optional[float] = None
    net_sales: Optional[float] = None


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    transaction_number: str
    sale_reference: Optional[str]
    medicine_id: int
    medicine_name: str
    quantity: int
    price: float
    total_amount: float
    customer_name: Optional[str]
    cashier_name: Optional[str]
    payment_method: Optional[str]
    transaction_type: str
    cash_received: Optional[float]
    change_amount: Optional[float]
    customer_id: Optional[int]
    customer_id_number: Optional[str]
    discount_eligible: Optional[bool]
    eligible_subtotal: Optional[float]
    non_eligible_subtotal: Optional[float]
    sale_unit: Optional[str]
    unit_factor: Optional[float]
    category: Optional[str]
    classification: Optional[str]
    dosage_form: Optional[str]
    customer_type: Optional[str]
    gross_amount: Optional[float]
    vatable_sales: Optional[float]
    vat_amount: Optional[float]
    vat_exempt_sales: Optional[float]
    discount_amount: Optional[float]
    senior_citizen_discount: Optional[float]
    pwd_discount: Optional[float]
    other_discount: Optional[float]
    net_sales: Optional[float]
    created_at: datetime


class VoidRequestCreate(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class VoidRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    transaction_id: int
    requested_by: str
    reason: Optional[str]
    status: str
    reviewed_by: Optional[str]
    created_at: datetime
    reviewed_at: Optional[datetime]


def gen_txn_number() -> str:
    return f"TXN-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"


@router.get("/transactions", response_model=List[TransactionOut])
def list_transactions(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=500),
    search: Optional[str] = None,
    current_user=Depends(get_transaction_user),
):
    db = SessionLocal()
    try:
        q = db.query(Transaction).order_by(Transaction.created_at.desc())
        from main import data_owner_id
        owner_id = data_owner_id(current_user)
        if owner_id is not None:
            q = q.filter(Transaction.owner_admin_id == owner_id)
        if (getattr(current_user, "role", "staff") or "staff").lower() != "admin":
            q = q.filter(Transaction.cashier_name == current_user.email)
        if search:
            term = f"%{search}%"
            q = q.filter(or_(Transaction.transaction_number.like(term), Transaction.medicine_name.like(term), Transaction.customer_name.like(term)))
        offset = (page - 1) * size
        rows = q.offset(offset).limit(size).all()
        return rows
    finally:
        db.close()


@router.post("/transactions", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
def create_transaction(payload: TransactionCreate, current_user=Depends(get_transaction_user)):
    db = SessionLocal()
    try:
        from main import data_owner_id
        if payload.customer_id is not None:
            customer = db.execute(
                text("SELECT id, customer_type, id_number, full_name, status FROM customers WHERE id = :id"),
                {"id": payload.customer_id},
            ).first()
            if not customer:
                raise HTTPException(status_code=400, detail="Customer record not found")
            if customer[4] != "active":
                raise HTTPException(status_code=400, detail="Customer record is inactive")
            if payload.customer_type not in ("senior_citizen", "pwd") or customer[1] != payload.customer_type:
                raise HTTPException(status_code=400, detail="Customer type does not match the selected record")

        # validate medicine exists
        owner_id = data_owner_id(current_user)
        med_owner_sql = "" if owner_id is None else " AND owner_admin_id = :owner_admin_id"
        med = db.execute(
            text(f"SELECT id, name, stock FROM medicines WHERE id = :id{med_owner_sql}"),
            {"id": payload.medicine_id, "owner_admin_id": owner_id},
        ).first()
        if not med:
            raise HTTPException(status_code=404, detail="Medicine not found")

        available = db.execute(
            text(
                """
                SELECT SUM(quantity) as total
                FROM medicine_supplies
                WHERE medicine_id = :id
                  AND COALESCE(is_archived, 0) = 0
                  AND quantity > 0
                  AND (expiry_date IS NULL OR expiry_date > DATE_ADD(CURDATE(), INTERVAL 30 DAY))
                """
            ),
            {"id": payload.medicine_id},
        ).scalar() or 0
        if available < payload.quantity:
            raise HTTPException(status_code=400, detail="Insufficient stock (excluding expired and batches within 30 days of expiry)")

                # Deduct from the earliest-expiring valid supply first (FEFO), matching
                # the inventory UI's FIFO Next indicator. Received date and id provide
                # deterministic tie-breakers for batches with the same expiry.
        remaining = payload.quantity
        supplies_rows = db.execute(
            text(
                """
                                SELECT id, quantity, expiry_date, received_date, created_at
                FROM medicine_supplies
                WHERE medicine_id = :id
                  AND COALESCE(is_archived, 0) = 0
                  AND quantity > 0
                  AND (expiry_date IS NULL OR expiry_date > DATE_ADD(CURDATE(), INTERVAL 30 DAY))
                                ORDER BY
                                        CASE WHEN expiry_date IS NULL THEN 1 ELSE 0 END ASC,
                                        expiry_date ASC,
                                        CASE WHEN received_date IS NULL THEN 1 ELSE 0 END ASC,
                                        received_date ASC,
                                        CASE WHEN created_at IS NULL THEN 1 ELSE 0 END ASC,
                                        created_at ASC,
                                        id ASC
                FOR UPDATE
                """
            ),
            {"id": payload.medicine_id},
        ).fetchall()
        for row in supplies_rows:
            if remaining <= 0:
                break
            sid = row[0]
            sqty = int(row[1])
            exp_date = row[2]
            if exp_date:
                days_left = (exp_date - date.today()).days
                if days_left <= 30:
                    raise HTTPException(status_code=400, detail=f"Supply batch {sid} has only {days_left} days left and cannot be sold.")
            take = min(sqty, remaining)
            db.execute(text("UPDATE medicine_supplies SET quantity = quantity - :take WHERE id = :sid"), {"take": take, "sid": sid})
            remaining -= take

        if remaining > 0:
            raise HTTPException(status_code=400, detail="Insufficient supplies after allocation")

        # Keep displayed stock aligned with sellable, non-expired supply stock.
        db.execute(
            text(
                """
                UPDATE medicines
                SET stock = (
                    SELECT COALESCE(SUM(quantity), 0)
                    FROM medicine_supplies
                    WHERE medicine_id = :id
                      AND COALESCE(is_archived, 0) = 0
                      AND quantity > 0
                      AND (expiry_date IS NULL OR expiry_date > DATE_ADD(CURDATE(), INTERVAL 30 DAY))
                )
                WHERE id = :id
                """
            ),
            {"id": payload.medicine_id},
        )

        # PWD receives the discount only; VAT remains included. Senior citizens
        # are the only protected customer type that is VAT-exempt.
        gross_amount = payload.gross_amount or round(payload.quantity * payload.price, 2)
        vatable_sales = payload.vatable_sales
        vat_amount = payload.vat_amount
        vat_exempt_sales = payload.vat_exempt_sales
        pwd_discount = payload.pwd_discount
        if payload.customer_type == "pwd":
            vatable_sales = round(gross_amount / 1.12, 2)
            vat_amount = round(gross_amount - vatable_sales, 2)
            vat_exempt_sales = 0
            pwd_discount = payload.discount_amount or 0

        txn = Transaction(
            transaction_number=gen_txn_number(),
            sale_reference=payload.sale_reference,
            medicine_id=payload.medicine_id,
            medicine_name=med[1] if isinstance(med, tuple) or isinstance(med, list) else med.name,
            quantity=payload.quantity,
            price=payload.price,
            total_amount=round(payload.quantity * payload.price, 2),
            customer_name=payload.customer_name,
            cashier_name=(
                payload.cashier_name
                if (getattr(current_user, "role", "staff") or "staff").lower() == "admin"
                else current_user.email
            ),
            payment_method=payload.payment_method,
            transaction_type=payload.transaction_type or 'sale',
            cash_received=payload.cash_received,
            change_amount=payload.change_amount,
            customer_id=payload.customer_id,
            customer_id_number=payload.customer_id_number,
            discount_eligible=payload.discount_eligible,
            eligible_subtotal=payload.eligible_subtotal,
            non_eligible_subtotal=payload.non_eligible_subtotal,
            sale_unit=payload.sale_unit,
            unit_factor=payload.unit_factor,
            category=payload.category,
            classification=payload.classification,
            dosage_form=payload.dosage_form,
            customer_type=payload.customer_type,
            gross_amount=gross_amount,
            vatable_sales=vatable_sales,
            vat_amount=vat_amount,
            vat_exempt_sales=vat_exempt_sales,
            discount_amount=payload.discount_amount,
            senior_citizen_discount=payload.senior_citizen_discount,
            pwd_discount=pwd_discount,
            other_discount=payload.other_discount,
            net_sales=payload.net_sales,
            owner_admin_id=data_owner_id(current_user),
        )
        db.add(txn)
        db.commit()
        db.refresh(txn)
        response_payload = {
            "id": txn.id,
            "transaction_number": txn.transaction_number,
            "sale_reference": txn.sale_reference,
            "medicine_id": txn.medicine_id,
            "medicine_name": txn.medicine_name,
            "quantity": txn.quantity,
            "price": txn.price,
            "total_amount": txn.total_amount,
            "customer_name": txn.customer_name,
            "cashier_name": txn.cashier_name,
            "payment_method": txn.payment_method,
            "transaction_type": txn.transaction_type,
            "cash_received": txn.cash_received,
            "change_amount": txn.change_amount,
            "customer_id": txn.customer_id,
            "customer_id_number": txn.customer_id_number,
            "discount_eligible": txn.discount_eligible,
            "eligible_subtotal": txn.eligible_subtotal,
            "non_eligible_subtotal": txn.non_eligible_subtotal,
            "sale_unit": txn.sale_unit,
            "unit_factor": txn.unit_factor,
            "category": txn.category,
            "classification": txn.classification,
            "dosage_form": txn.dosage_form,
            "customer_type": txn.customer_type,
            "gross_amount": txn.gross_amount,
            "vatable_sales": txn.vatable_sales,
            "vat_amount": txn.vat_amount,
            "vat_exempt_sales": txn.vat_exempt_sales,
            "discount_amount": txn.discount_amount,
            "senior_citizen_discount": txn.senior_citizen_discount,
            "pwd_discount": txn.pwd_discount,
            "other_discount": txn.other_discount,
            "net_sales": txn.net_sales,
            "created_at": txn.created_at,
        }

        # Audit log
        detail = f"Transaction {txn.transaction_number} added: medicine_id={txn.medicine_id} qty={txn.quantity} total={txn.total_amount} cashier={txn.cashier_name}"
        db.execute(text("INSERT INTO audit_logs (event_type, detail, created_at) VALUES (:et, :dt, NOW())"), {"et": "TRANSACTION_ADDED", "dt": detail})
        db.commit()
        return response_payload
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


def _void_transaction(db, txn_row: Transaction, actor_email: str) -> None:
    """Restore stock and remove a completed transaction after approval."""
    transaction_number = txn_row.transaction_number
    quantity = txn_row.quantity
    medicine_id = txn_row.medicine_id
    price = txn_row.price

    db.execute(text("UPDATE medicines SET stock = stock + :qty WHERE id = :id"), {"qty": quantity, "id": medicine_id})
    rev_batch = f"REVERSAL-{transaction_number}"
    expiry = (date.today() + timedelta(days=3650)).isoformat()
    db.execute(text("INSERT INTO medicine_supplies (medicine_id, batch_number, quantity, supplier, expiry_date, unit_cost, selling_price, created_at) VALUES (:mid, :batch, :qty, :supplier, :expiry, :unit_cost, :selling_price, NOW())"), {
        "mid": medicine_id,
        "batch": rev_batch,
        "qty": quantity,
        "supplier": "reversal",
        "expiry": expiry,
        "unit_cost": price * 0.7,
        "selling_price": price,
    })
    db.delete(txn_row)
    db.flush()
    db.execute(text("INSERT INTO audit_logs (event_type, detail, created_at) VALUES (:et, :dt, NOW())"), {
        "et": "TRANSACTION_VOIDED",
        "dt": f"Transaction {transaction_number} voided by {actor_email}; stock restored: {quantity}",
    })


@router.post("/transactions/{txn_id}/void-request", response_model=VoidRequestOut, status_code=status.HTTP_201_CREATED)
def request_transaction_void(txn_id: int, payload: VoidRequestCreate, current_user=Depends(get_transaction_user)):
    db = SessionLocal()
    try:
        if (getattr(current_user, "role", "staff") or "staff").lower() != "staff":
            raise HTTPException(status_code=400, detail="Administrators can void transactions directly")
        txn_row = db.query(Transaction).filter(Transaction.id == txn_id).first()
        if not txn_row:
            raise HTTPException(status_code=404, detail="Transaction not found")
        existing = db.query(TransactionVoidRequest).filter(
            TransactionVoidRequest.transaction_id == txn_id,
            TransactionVoidRequest.status == "PENDING",
        ).first()
        if existing:
            return existing
        request = TransactionVoidRequest(
            transaction_id=txn_id,
            requested_by=current_user.email,
            reason=(payload.reason or "Staff requested transaction void").strip(),
            owner_admin_id=data_owner_id(current_user),
        )
        db.add(request)
        db.flush()
        db.execute(text("INSERT INTO audit_logs (event_type, detail, created_at) VALUES (:et, :dt, NOW())"), {
            "et": "TRANSACTION_VOID_REQUESTED",
            "dt": f"Transaction {txn_row.transaction_number} void requested by {current_user.email}",
        })
        db.commit()
        db.refresh(request)
        return request
    finally:
        db.close()


@router.get("/transactions/void-requests", response_model=List[VoidRequestOut])
def list_transaction_void_requests(current_user=Depends(get_transaction_user)):
    db = SessionLocal()
    try:
        from main import data_owner_id
        query = db.query(TransactionVoidRequest).order_by(TransactionVoidRequest.created_at.desc())
        owner_id = data_owner_id(current_user)
        if owner_id is not None:
            query = query.filter(TransactionVoidRequest.owner_admin_id == owner_id)
        if (getattr(current_user, "role", "staff") or "staff").lower() != "admin":
            query = query.filter(TransactionVoidRequest.requested_by == current_user.email)
        return query.limit(100).all()
    finally:
        db.close()


@router.post("/transactions/void-requests/{request_id}/review", response_model=VoidRequestOut)
def review_transaction_void_request(request_id: int, action: str = Query(..., pattern="^(approve|reject)$"), current_user=Depends(get_transaction_user)):
    db = SessionLocal()
    try:
        if (getattr(current_user, "role", "staff") or "staff").lower() != "admin":
            raise HTTPException(status_code=403, detail="Only administrators can review void requests")
        from main import data_owner_id
        owner_id = data_owner_id(current_user)
        request_query = db.query(TransactionVoidRequest).filter(TransactionVoidRequest.id == request_id)
        if owner_id is not None:
            request_query = request_query.filter(TransactionVoidRequest.owner_admin_id == owner_id)
        request = request_query.first()
        if not request:
            raise HTTPException(status_code=404, detail="Void request not found")
        if request.status != "PENDING":
            raise HTTPException(status_code=400, detail="Void request has already been reviewed")
        txn_query = db.query(Transaction).filter(Transaction.id == request.transaction_id)
        if owner_id is not None:
            txn_query = txn_query.filter(Transaction.owner_admin_id == owner_id)
        txn_row = txn_query.first()
        if action == "approve":
            if not txn_row:
                raise HTTPException(status_code=404, detail="Transaction no longer exists")
            _void_transaction(db, txn_row, current_user.email)
            request.status = "APPROVED"
        else:
            request.status = "REJECTED"
        request.reviewed_by = current_user.email
        request.reviewed_at = datetime.now()
        db.commit()
        db.refresh(request)
        return request
    finally:
        db.close()


@router.delete("/transactions/{txn_id}")
def delete_transaction(txn_id: int, current_user=Depends(get_transaction_user)):
    db = SessionLocal()
    try:
        from main import data_owner_id
        owner_id = data_owner_id(current_user)
        txn_query = db.query(Transaction).filter(Transaction.id == txn_id)
        if owner_id is not None:
            txn_query = txn_query.filter(Transaction.owner_admin_id == owner_id)
        txn_row = txn_query.first()
        if not txn_row:
            raise HTTPException(status_code=404, detail="Transaction not found")

        is_admin = (getattr(current_user, "role", "staff") or "staff").lower() == "admin"
        if not is_admin:
            raise HTTPException(status_code=403, detail="Only administrators can void transactions")

        _void_transaction(db, txn_row, current_user.email)
        db.commit()
        return {"status": "ok"}
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
