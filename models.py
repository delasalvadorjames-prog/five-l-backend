"""
Database Models - Complete schema with FIFO support
"""

from datetime import datetime, date
from enum import Enum
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Enum as SQLEnum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
import os
from urllib.parse import quote_plus

# ==========================================
# ENUMS
# ==========================================

class MovementType(str, Enum):
    """Types of inventory movements"""
    RECEIPT = "receipt"
    SALE = "sale"
    EXPIRED = "expired"
    ADJUSTMENT = "adjustment"
    RETURN = "return"


class PaymentMethod(str, Enum):
    CASH = "cash"
    CARD = "card"
    TRANSFER = "transfer"


class PaymentStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


# ==========================================
# DATABASE SETUP
# ==========================================

def build_mysql_url() -> str:
    """Build MySQL connection URL from environment"""
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
    return f"mysql+pymysql://{auth}@{host}:{port}/{db_name}"


MYSQL_URL = build_mysql_url()
engine = create_engine(MYSQL_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ==========================================
# USER MODEL
# ==========================================

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    full_name = Column(String(100))
    email = Column(String(255), unique=True, index=True)
    password_hash = Column(Text)
    role = Column(String(20), default="staff", server_default="staff")
    assigned_category = Column(String(255), nullable=True)
    account_status = Column(String(20), default="active", server_default="active")
    can_process_pos = Column(Boolean, default=True, server_default="1", nullable=False)
    can_view_reports = Column(Boolean, default=True, server_default="1", nullable=False)
    can_adjust_inventory = Column(Boolean, default=True, server_default="1", nullable=False)
    can_approve_voids = Column(Boolean, default=True, server_default="1", nullable=False)
    created_by_admin = Column(Integer, nullable=True)
    last_login = Column(DateTime, nullable=True)
    active_token = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


# ==========================================
# PRODUCT & INVENTORY MODELS
# ==========================================

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

    # Relationships
    batches = relationship("InventoryBatch", back_populates="product", cascade="all, delete-orphan")
    movements = relationship("InventoryMovement", back_populates="product", cascade="all, delete-orphan")
    expired_logs = relationship("ExpiredStockLog", back_populates="product", cascade="all, delete-orphan")


class InventoryBatch(Base):
    __tablename__ = "inventory_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_number = Column(String(100), unique=True, index=True, nullable=False)
    quantity = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False, index=True)
    received_date = Column(Date, nullable=False, index=True)
    expiry_date = Column(Date, nullable=False, index=True)
    supplier = Column(String(255), nullable=True)
    cost_per_unit = Column(Float, nullable=True, default=0.0)
    
    is_expired = Column(Boolean, nullable=False, default=False, index=True)
    notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # Relationships
    product = relationship("Product", back_populates="batches")
    movements = relationship("InventoryMovement", back_populates="batch", cascade="all, delete-orphan")
    expired_logs = relationship("ExpiredStockLog", back_populates="batch", cascade="all, delete-orphan")
    sale_items = relationship("SaleItem", back_populates="batch")


# ==========================================
# INVENTORY AUDIT MODELS
# ==========================================

class InventoryMovement(Base):
    """Track all inventory movements for audit trail"""
    __tablename__ = "inventory_movements"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("inventory_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    movement_type = Column(SQLEnum(MovementType), nullable=False, index=True)
    quantity_change = Column(Integer, nullable=False)  # + for IN, - for OUT
    
    reference_type = Column(String(50), nullable=True)  # "sale", "batch_receipt", "expiry"
    reference_id = Column(Integer, nullable=True)
    
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    
    batch = relationship("InventoryBatch", back_populates="movements")
    product = relationship("Product", back_populates="movements")


class ExpiredStockLog(Base):
    """Track expired stock for compliance"""
    __tablename__ = "expired_stock_logs"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(Integer, ForeignKey("inventory_batches.id", ondelete="CASCADE"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    
    quantity_expired = Column(Integer, nullable=False)
    expiry_date = Column(Date, nullable=False)
    days_expired = Column(Integer, nullable=True)
    
    disposal_method = Column(String(100), nullable=True)
    disposed_at = Column(DateTime, nullable=True)
    disposed_by = Column(String(255), nullable=True)
    
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now(), index=True)
    
    batch = relationship("InventoryBatch", back_populates="expired_logs")
    product = relationship("Product", back_populates="expired_logs")

    # ==========================================
# ADD THESE MODELS (Insert after existing models)
# ==========================================

class Category(Base):
    """Product categories"""
    __tablename__ = "categories"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, server_default="1", nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationships
    products = relationship("Product", back_populates="category_ref")


class Supplier(Base):
    """Supplier/Vendor information"""
    __tablename__ = "suppliers"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), unique=True, nullable=False, index=True)
    contact_person = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True)
    payment_terms = Column(String(255), nullable=True)  # e.g., "Net 30", "Cash on Delivery"
    is_active = Column(Boolean, default=True, server_default="1", nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationships
    batches = relationship("InventoryBatch", back_populates="supplier_ref")


# ==========================================
# UPDATE EXISTING MODELS
# ==========================================

# UPDATE Product model - Add relationships to Category and Supplier
class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sku = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(255), index=True, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)  # ADD THIS
    unit_price = Column(Float, nullable=False, default=0.0)
    reorder_level = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # ADD THESE RELATIONSHIPS
    category_ref = relationship("Category", back_populates="products")
    batches = relationship("InventoryBatch", back_populates="product")
    movements = relationship("InventoryMovement", back_populates="product")
    expired_logs = relationship("ExpiredStockLog", back_populates="product")


# UPDATE InventoryBatch model - Add Supplier relationship
class InventoryBatch(Base):
    __tablename__ = "inventory_batches"

    id = Column(Integer, primary_key=True, autoincrement=True)
    product_id = Column(Integer, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True)
    batch_number = Column(String(100), unique=True, index=True, nullable=False)
    quantity = Column(Integer, nullable=False)
    remaining_quantity = Column(Integer, nullable=False, index=True)
    received_date = Column(Date, nullable=False, index=True)
    expiry_date = Column(Date, nullable=False, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)  # ADD THIS
    cost_per_unit = Column(Float, nullable=True, default=0.0)
    
    is_expired = Column(Boolean, nullable=False, default=False, index=True)
    notes = Column(Text, nullable=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    # ADD THIS RELATIONSHIP
    supplier_ref = relationship("Supplier", back_populates="batches")
    product = relationship("Product", back_populates="batches")
    movements = relationship("InventoryMovement", back_populates="batch")
    expired_logs = relationship("ExpiredStockLog", back_populates="batch")
    sale_items = relationship("SaleItem", back_populates="batch")


# ==========================================
# SALES MODELS
# ==========================================

class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, autoincrement=True)
    sale_number = Column(String(80), unique=True, index=True, nullable=False)
    cashier_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    cashier_name = Column(String(255), nullable=True)
    customer_name = Column(String(255), nullable=True)
    total_amount = Column(Float, nullable=False, default=0.0)
    
    payment_method = Column(SQLEnum(PaymentMethod), default=PaymentMethod.CASH, nullable=False)
    payment_status = Column(SQLEnum(PaymentStatus), default=PaymentStatus.COMPLETED, nullable=False)
    
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
    batch = relationship("InventoryBatch", back_populates="sale_items")


# ==========================================
# DATABASE FUNCTIONS
# ==========================================

def get_db():
    """Database session dependency"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables"""
    Base.metadata.create_all(bind=engine)