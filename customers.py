import os
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker


def build_mysql_url() -> str:
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    user = quote_plus(os.getenv("DB_USER", "root") or "root")
    password = os.getenv("DB_PASSWORD", "")
    auth = f"{user}:{quote_plus(password)}" if password else user
    return f"mysql+pymysql://{auth}@{host}:{port}/{os.getenv('DB_NAME', 'five-l')}"


engine = create_engine(build_mysql_url(), pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
Base = declarative_base()


class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    customer_type = Column(String(32), nullable=False, index=True)
    id_number = Column(String(100), nullable=False)
    full_name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="active", server_default="active")
    registered_by = Column(String(255), nullable=True)
    date_registered = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    updated_at = Column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), onupdate=datetime.utcnow)


Base.metadata.create_all(bind=engine)
with engine.begin() as conn:
    try:
        conn.execute(text("CREATE UNIQUE INDEX uq_customers_type_id ON customers (customer_type, id_number)"))
    except Exception:
        pass


router = APIRouter(prefix="/customers", tags=["customers"])


async def get_customer_user(authorization: str = Header(None)):
    from main import get_current_user
    db = SessionLocal()
    try:
        return await get_current_user(authorization=authorization, db=db)
    finally:
        db.close()


class CustomerCreate(BaseModel):
    customer_type: str = Field(pattern="^(senior_citizen|pwd)$")
    id_number: str = Field(min_length=1, max_length=100)
    full_name: str = Field(min_length=2, max_length=255)


class CustomerUpdate(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=255)
    status: Optional[str] = Field(default=None, pattern="^(active|inactive)$")


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    customer_type: str
    id_number: str
    full_name: str
    status: str
    registered_by: Optional[str]
    date_registered: datetime
    updated_at: datetime


def normalize_id(value: str) -> str:
    return " ".join(value.strip().split())


@router.get("", response_model=List[CustomerOut])
def list_customers(
    customer_type: Optional[str] = Query(None, pattern="^(senior_citizen|pwd)$"),
    search: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status", pattern="^(active|inactive)$"),
    current_user=Depends(get_customer_user),
):
    db = SessionLocal()
    try:
        query = db.query(Customer).order_by(Customer.date_registered.desc())
        if customer_type:
            query = query.filter(Customer.customer_type == customer_type)
        if status_filter:
            query = query.filter(Customer.status == status_filter)
        if search:
            term = f"%{search.strip()}%"
            query = query.filter((Customer.id_number.like(term)) | (Customer.full_name.like(term)))
        return query.limit(500).all()
    finally:
        db.close()


@router.get("/{customer_type}/{id_number}", response_model=CustomerOut)
def get_customer(customer_type: str, id_number: str, current_user=Depends(get_customer_user)):
    if customer_type not in ("senior_citizen", "pwd"):
        raise HTTPException(status_code=400, detail="Unsupported customer type")
    db = SessionLocal()
    try:
        customer = db.query(Customer).filter(
            Customer.customer_type == customer_type,
            Customer.id_number == normalize_id(id_number),
        ).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer record not found")
        return customer
    finally:
        db.close()


@router.post("", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(payload: CustomerCreate, current_user=Depends(get_customer_user)):
    db = SessionLocal()
    try:
        id_number = normalize_id(payload.id_number)
        full_name = " ".join(payload.full_name.strip().split())
        existing = db.query(Customer).filter(
            Customer.customer_type == payload.customer_type,
            Customer.id_number == id_number,
        ).first()
        if existing:
            raise HTTPException(status_code=409, detail="A customer with this ID already exists")
        customer = Customer(
            customer_type=payload.customer_type,
            id_number=id_number,
            full_name=full_name,
            registered_by=getattr(current_user, "email", None),
        )
        db.add(customer)
        db.commit()
        db.refresh(customer)
        return customer
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(customer_id: int, payload: CustomerUpdate, current_user=Depends(get_customer_user)):
    if (getattr(current_user, "role", "staff") or "staff").lower() != "admin":
        raise HTTPException(status_code=403, detail="Only administrators can edit customer records")
    db = SessionLocal()
    try:
        customer = db.query(Customer).filter(Customer.id == customer_id).first()
        if not customer:
            raise HTTPException(status_code=404, detail="Customer record not found")
        if payload.full_name is not None:
            customer.full_name = " ".join(payload.full_name.strip().split())
        if payload.status is not None:
            customer.status = payload.status
        db.commit()
        db.refresh(customer)
        return customer
    finally:
        db.close()
