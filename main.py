import calendar
import asyncio
import csv
import io
import os
import random
import re
import smtplib
import uuid
from datetime import date, datetime, timedelta, timezone
from contextlib import asynccontextmanager
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional, Union
from urllib.parse import quote_plus, urlparse

from sqlalchemy import create_engine, Column, Integer, String, Float, Date, DateTime, Text, Boolean, ForeignKey, func, Index, UniqueConstraint, text
# pyrefly: ignore [missing-import]
from sqlalchemy.orm import sessionmaker, declarative_base, Session, relationship
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import func, and_

from fastapi import Depends, FastAPI, Header, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, Text, Enum
from sqlalchemy.sql import func

try:
    from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
except Exception:  # pragma: no cover - optional dependency guard
    ConnectionConfig = None
    FastMail = None
    MessageSchema = None
    MessageType = None

from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field, ConfigDict
from email_validator import EmailNotValidError, validate_email

# Import ML routes
try:
    from ml_routes import router as ml_router
    ML_ENABLED = True
except ImportError:
    ML_ENABLED = False
    print("[WARNING] ML module not available, disabling ML endpoints")

# -------------------
# Application lifecycle
# -------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Run the existing database initialization during application startup.
    on_startup()

    async def keep_alive():
        while True:
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                print("Database keep-alive: OK")
            except Exception as exc:
                print(f"Database keep-alive error: {exc}")
            await asyncio.sleep(240)  # Every 4 minutes

    task = asyncio.create_task(keep_alive())
    try:
        yield
    finally:
        # Shutdown: cancel the keep-alive task.
        task.cancel()


# -------------------
# Base setup
# -------------------
Base = declarative_base()

app = FastAPI(title="Five L Pharmacy API - MySQL", version="0.5.0", lifespan=lifespan)

app.state.db_ready = False
app.state.startup_error = "Database not initialized."
app.state.engine = None

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
    allow_origin_regex=r"https?://.*",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=3600,
)

# -------------------
# Environment helpers
# -------------------
def read_bool_env(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}

def read_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        return int(raw_value.strip())
    except ValueError:
        return default

def normalize_smtp_password(host: str, password: str) -> str:
    password = (password or "").strip()
    if host.strip().lower() in {"smtp.gmail.com", "smtp-relay.gmail.com"}:
        # Google app passwords are often copied as grouped text with spaces.
        return "".join(password.split())
    return password

def load_local_env() -> None:
    env_candidates = [
        Path(__file__).resolve().with_name(".env"),
        Path(__file__).resolve().parents[1] / ".env",
    ]
    for env_path in env_candidates:
        if not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ[key] = value

load_local_env()

import pymysql
pymysql.install_as_MySQLdb()

def get_mysql_db_name() -> str:
    explicit_url = os.getenv("MYSQL_URL")
    if explicit_url:
        parsed = urlparse(explicit_url)
        db_name = parsed.path.lstrip("/") if parsed.path else ""
        return db_name or os.getenv("DB_NAME", "five-l")
    return os.getenv("DB_NAME", "five-l")


def build_mysql_url() -> str:
    explicit_url = os.getenv("MYSQL_URL")
    if explicit_url:
        return explicit_url

    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")
    db_name = get_mysql_db_name()

    user_part = quote_plus(user or "root")
    password_part = quote_plus(password) if password else ""
    auth = f"{user_part}:{password_part}" if password_part else user_part
    return f"mysql://{auth}@{host}:{port}/{db_name}"


def build_mysql_server_url() -> str:
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    user = os.getenv("DB_USER", "root")
    password = os.getenv("DB_PASSWORD", "")

    user_part = quote_plus(user or "root")
    password_part = quote_plus(password) if password else ""
    auth = f"{user_part}:{password_part}" if password_part else user_part
    return f"mysql://{auth}@{host}:{port}/"


def create_database_if_missing() -> None:
    db_name = get_mysql_db_name()
    if not db_name:
        return

    server_url = build_mysql_server_url()
    temp_engine = create_engine(server_url, pool_pre_ping=True, pool_recycle=3600)
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

# JWT Config
JWT_SECRET = os.getenv("JWT_SECRET", "five-l-dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_HOURS = read_int_env("JWT_EXPIRES_HOURS", 2)

# DB Reset on Startup
RESET_DB_ON_STARTUP = read_bool_env("FIVE_L_RESET_DB", False)
RESET_CODE_EXPIRY_MINUTES = read_int_env("RESET_CODE_EXPIRY_MINUTES", 15)

# Security — allowed email allowlist
_raw_allowed = os.getenv("ALLOWED_EMAILS", "delasalvadorjames@gmail.com,jeremiassalvador@five-l")
ALLOWED_EMAILS: set = {e.strip().lower() for e in _raw_allowed.split(",") if e.strip()}

# Brute-force protection config
MAX_LOGIN_ATTEMPTS = read_int_env("MAX_LOGIN_ATTEMPTS", 3)
LOCKOUT_MINUTES = read_int_env("LOCKOUT_MINUTES", 5)
MAX_ADMIN_ACCOUNTS = 2

# SMTP / Email reset configuration
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = read_int_env("SMTP_PORT", 587)
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = normalize_smtp_password(SMTP_HOST, os.getenv("SMTP_PASSWORD", ""))
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "no-reply@five-l.local").strip()
SMTP_USE_TLS = read_bool_env("SMTP_USE_TLS", True)
SMTP_USE_SSL = read_bool_env("SMTP_USE_SSL", False)
SMTP_TIMEOUT = read_int_env("SMTP_TIMEOUT", 20)
EMAIL_DEV_FALLBACK = read_bool_env("EMAIL_DEV_FALLBACK", False)

MAIL_CONFIG = None
MAIL_CLIENT = None
if SMTP_HOST and ConnectionConfig is not None:
    MAIL_CONFIG = ConnectionConfig(
        MAIL_USERNAME=SMTP_USER,
        MAIL_PASSWORD=SMTP_PASSWORD,
        MAIL_FROM=SMTP_FROM or SMTP_USER or "no-reply@five-l.local",
        MAIL_PORT=SMTP_PORT,
        MAIL_SERVER=SMTP_HOST,
        MAIL_STARTTLS=SMTP_USE_TLS,
        MAIL_SSL_TLS=SMTP_USE_SSL,
        USE_CREDENTIALS=bool(SMTP_USER and SMTP_PASSWORD),
        VALIDATE_CERTS=True,
    )
    MAIL_CLIENT = FastMail(MAIL_CONFIG)

# Password hashing
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# -------------------
# SQLAlchemy Models
# -------------------
class User(Base):
    __tablename__ = 'users'
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

class PasswordResetToken(Base):
    __tablename__ = 'password_reset_tokens'
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), index=True)
    reset_code = Column(String(32))
    used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime)

class LoginOTPToken(Base):
    __tablename__ = 'login_otp_tokens'
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), index=True)
    otp_code = Column(String(32))
    used_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime)

class Medicine(Base):
    __tablename__ = 'medicines'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), index=True)
    category = Column(String(100))
    stock = Column(Integer, nullable=True)
    reorder_level = Column(Integer, nullable=True)
    expiry = Column(Date, nullable=True)
    avg_daily_sales = Column(Float, nullable=True)
    unit_price = Column(Float, nullable=True)
    supplier = Column(String(200), nullable=True)
    added_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    assigned_staff = Column(Integer, ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_restocked_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    is_new_arrival = Column(Boolean, default=False)

    # New fields
    product_code = Column(String(50), unique=True, index=True, nullable=True)
    medicine_name = Column(String(200), index=True, nullable=True)
    classification = Column(String(50), nullable=False, server_default='Generic')
    dosage_form = Column(String(100), nullable=False, server_default='Tablet')
    strength = Column(String(100), nullable=True)
    volume = Column(String(100), nullable=True)
    manufacturer = Column(String(200), nullable=True)
    unit_type = Column(String(50), nullable=False, server_default='pcs')
    base_unit = Column(String(20), nullable=False, server_default='pcs')
    purchase_unit = Column(String(50), nullable=False, server_default='pcs')
    conversion_factor = Column(Integer, nullable=False, server_default='1')
    is_archived = Column(Boolean, nullable=False, server_default='0')

    supplies = relationship("MedicineSupply", back_populates="medicine", cascade="all, delete-orphan")

class InventoryAlertConfig(Base):
    __tablename__ = 'inventory_alert_configs'
    __table_args__ = (
        UniqueConstraint('dosage_form', name='uq_inventory_alert_configs_dosage_form'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    dosage_form = Column(String(100), nullable=False)
    low_stock_threshold = Column(Integer, nullable=False, default=0)
    expiry_alert_days = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True, server_default='1')
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class MedicineSupply(Base):
    __tablename__ = 'medicine_supplies'
    id = Column(Integer, primary_key=True, autoincrement=True)
    medicine_id = Column(Integer, ForeignKey('medicines.id', ondelete='CASCADE'), nullable=False)
    batch_number = Column(String(100), nullable=False)
    quantity = Column(Integer, nullable=False, default=0)
    supplier = Column(String(200), nullable=True)
    expiry_date = Column(Date, nullable=False)
    received_date = Column(Date, nullable=True)
    unit_cost = Column(Float, nullable=False, default=0.0)
    selling_price = Column(Float, nullable=False, default=0.0)
    is_archived = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, server_default=func.now())

    medicine = relationship("Medicine", back_populates="supplies")

class PriceHistory(Base):

    __tablename__ = 'price_history'

    id = Column(Integer, primary_key=True, autoincrement=True)

    inventory_id = Column(

        Integer,

        ForeignKey('medicine_supplies.id', ondelete='CASCADE'),

        nullable=False

    )

    old_price = Column(Float, nullable=False)

    new_price = Column(Float, nullable=False)

    updated_by = Column(String(100), nullable=True)

    # NEW FIELDS

    status = Column(

        Enum('PENDING', 'APPROVED', 'REJECTED'),

        default='PENDING',

        nullable=False

    )

    approved_by = Column(String(100), nullable=True)

    approved_at = Column(DateTime, nullable=True)

    reason = Column(Text, nullable=True)

    adjustment_type = Column(String(32), nullable=True)
    old_quantity = Column(Integer, nullable=True)
    new_quantity = Column(Integer, nullable=True)

    remarks = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())

class SalesTransaction(Base):
    __tablename__ = 'sales_transactions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    medicine_id = Column(Integer, ForeignKey('medicines.id'))
    quantity = Column(Integer)
    unit_price = Column(Float)
    total = Column(Float)
    transaction_date = Column(DateTime, server_default=func.now())

class StockMovement(Base):
    __tablename__ = 'stock_movements'
    id = Column(Integer, primary_key=True, autoincrement=True)
    medicine_id = Column(Integer, ForeignKey('medicines.id', ondelete='CASCADE'))
    type = Column(String(50))
    quantity = Column(Integer)
    created_at = Column(DateTime, server_default=func.now())

class ActiveAdminSession(Base):
    """Stores the single active JWT token per admin. Only one session allowed at a time."""
    __tablename__ = 'active_admin_sessions'
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, index=True)
    token = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    expires_at = Column(DateTime)

class LoginAttempt(Base):
    """Tracks failed login attempts for brute-force protection."""
    __tablename__ = 'login_attempts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), index=True)
    ip_address = Column(String(64))
    attempt_count = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    last_attempt_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class AuditLog(Base):
    """Records all significant security events."""
    __tablename__ = 'audit_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String(64))
    email = Column(String(255), nullable=True)
    ip_address = Column(String(64), nullable=True)
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

class MedicineAuditLog(Base):
    __tablename__ = 'medicine_audit_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    action_type = Column(String(64), index=True) # medicine added, medicine updated, medicine deleted, stock changes, expired medicines, low stock alerts
    medicine_name = Column(String(200), index=True)
    performed_by = Column(String(255))
    role = Column(String(20))
    timestamp = Column(DateTime, server_default=func.now())
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)

class AdminAlert(Base):
    __tablename__ = 'admin_alerts'
    id = Column(Integer, primary_key=True, autoincrement=True)
    alert_type = Column(String(64), index=True) # EXPIRING, LOW_STOCK, SUSPICIOUS_STOCK, MEDICINE_DELETED, FAILED_LOGIN
    message = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    is_read = Column(Boolean, default=False)



# -------------------
# SQLAlchemy Engine
# -------------------
engine = create_engine(
    MYSQL_URL,
    pool_pre_ping=True,
    pool_recycle=280,
    pool_size=5,
    max_overflow=10,
    pool_timeout=30,
    connect_args={
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    },
)       # Extra connections kung kailangan

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# -------------------
# Pydantic Models
# -------------------
class UserCreate(BaseModel):
    full_name: str
    email: str
    password: str
    confirm_password: Optional[str] = None

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    full_name: str
    email: str
    role: str
    assigned_category: Optional[str] = None
    created_at: datetime

class MedicineSupplyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    medicine_id: int
    batch_number: str
    quantity: int
    supplier: Optional[str] = None
    expiry_date: date
    received_date: Optional[date] = None
    unit_cost: float
    selling_price: float
    created_at: Optional[datetime] = None
    is_archived: bool = False

class PriceHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    inventory_id: int
    old_price: float
    new_price: float
    updated_by: Optional[str] = None
    status: str
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    reason: Optional[str] = None
    remarks: Optional[str] = None
    created_at: Optional[datetime] = None
    medicine_name: Optional[str] = None
    requested_by: Optional[str] = None
    current_price: Optional[float] = None
    requested_price: Optional[float] = None
    adjustment_type: Optional[str] = None
    old_quantity: Optional[int] = None
    new_quantity: Optional[int] = None

class MedicineSupplyCreate(BaseModel):
    batch_number: Optional[str] = None
    quantity: int
    supplier: Optional[str] = None
    expiry_date: str # YYYY-MM-DD
    received_date: Optional[str] = None
    unit_cost: Optional[float] = 0.0
    selling_price: Optional[float] = 0.0

class MedicineSupplyUpdate(BaseModel):
    quantity: Optional[int] = None
    selling_price: Optional[float] = None
    expiry_date: Optional[str] = None
    received_date: Optional[str] = None
    supplier: Optional[str] = None
    batch_number: Optional[str] = None
    reason: Optional[str] = None
    adjustment_type: Optional[str] = None
    is_archived: Optional[bool] = None

class MedicineCreate(BaseModel):
    name: Optional[str] = None
    stock: Optional[int] = 0
    reorder_level: Optional[int] = 10
    expiry: Optional[str] = None
    avg_daily_sales: Optional[float] = 1.0
    unit_price: Optional[float] = 0.0
    supplier: Optional[str] = None
    assigned_staff: Optional[int] = None

    # New fields
    medicine_name: Optional[str] = None
    classification: Optional[str] = "Generic"
    dosage_form: Optional[str] = "Tablet"
    strength: Optional[str] = None
    volume: Optional[str] = None
    manufacturer: Optional[str] = None
    unit_type: Optional[str] = "pcs"
    base_unit: Optional[str] = "pcs"
    purchase_unit: Optional[str] = None
    conversion_factor: Optional[int] = 1
    category: Optional[str] = None
    
    # Opening batch / supply fields
    batch_number: Optional[str] = "BATCH-INIT"
    quantity: Optional[int] = 0
    expiry_date: Optional[str] = None
    received_date: Optional[str] = None
    unit_cost: Optional[float] = 0.0
    selling_price: Optional[float] = 0.0

class MedicineUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    stock: Optional[int] = None
    reorder_level: Optional[int] = None
    expiry: Optional[str] = None
    avg_daily_sales: Optional[float] = None
    unit_price: Optional[float] = None
    supplier: Optional[str] = None
    is_new_arrival: Optional[bool] = False
    assigned_staff: Optional[int] = None
    
    # New fields
    medicine_name: Optional[str] = None
    classification: Optional[str] = None
    dosage_form: Optional[str] = None
    strength: Optional[str] = None
    volume: Optional[str] = None
    manufacturer: Optional[str] = None
    unit_type: Optional[str] = None
    base_unit: Optional[str] = None
    purchase_unit: Optional[str] = None
    conversion_factor: Optional[int] = None

class MedicineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    name: str
    category: str
    stock: int
    reorder_level: Optional[int] = 0
    expiry: Optional[date] = None
    avg_daily_sales: Optional[float] = 0.0
    unit_price: float
    supplier: Optional[str] = None
    added_by: Optional[int] = None
    updated_by: Optional[int] = None
    assigned_staff: Optional[int] = None
    created_at: Optional[datetime] = None
    last_restocked_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    is_new_arrival: bool = False
    
    # New fields
    product_code: str
    medicine_name: str
    classification: str
    dosage_form: str
    low_stock_threshold: Optional[int] = None
    expiry_alert_days: Optional[int] = None
    strength: Optional[str] = None
    volume: Optional[str] = None
    manufacturer: Optional[str] = None
    unit_type: str = 'pcs'
    base_unit: str = 'pcs'
    purchase_unit: str = 'pcs'
    conversion_factor: int = 1
    is_archived: bool = False
    supplies: Optional[List[MedicineSupplyResponse]] = []

class SalesTransactionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    medicine_id: int
    quantity: int
    unit_price: float
    total: float
    transaction_date: datetime

class LoginRequest(BaseModel):
    email: Optional[str] = None
    userstaff: Optional[str] = None
    password: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    email: str
    full_name: str
    role: str
    assigned_category: Optional[str] = None
    can_process_pos: bool = True
    can_view_reports: bool = True
    can_adjust_inventory: bool = True
    can_approve_voids: bool = True

class LoginOTPRequiredResponse(BaseModel):
    requires_otp: bool
    email: str
    message: str

class LoginOTPVerifyRequest(BaseModel):
    email: str
    code: str

class StaffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    identifier: str
    full_name: str
    role: str
    assigned_category: Optional[str] = None
    account_status: str
    last_login: Optional[datetime] = None
    created_at: datetime
    can_process_pos: bool = True
    can_view_reports: bool = True
    can_adjust_inventory: bool = True
    can_approve_voids: bool = True

class StaffPermissionsUpdate(BaseModel):
    can_process_pos: bool
    can_view_reports: bool
    can_adjust_inventory: bool
    can_approve_voids: bool

class StaffAssignmentUpdate(BaseModel):
    assigned_category: str

class StaffStatusUpdate(BaseModel):
    account_status: str

class StaffCreate(BaseModel):
    full_name: str
    identifier: str
    password: Optional[str] = None
    assigned_category: str

class GoogleAuthRequest(BaseModel):
    code: str
    email: Optional[str] = None
    full_name: Optional[str] = None

class PasswordResetRequest(BaseModel):
    email: str

class PasswordResetRequestResponse(BaseModel):
    message: str

class PasswordResetConfirmRequest(BaseModel):
    email: str
    code: str
    new_password: str

class SetupStatusResponse(BaseModel):
    has_admin: bool
    setup_required: bool
    admin_count: int = 0
    max_admins: int = MAX_ADMIN_ACCOUNTS
    allow_signup: bool = False

class PredictRequest(BaseModel):
    label_text: str = Field(..., min_length=3)

class PredictResponse(BaseModel):
    predicted_category: str
    confidence: float
    explanations: List[str]
    detected_keywords: List[str]

# -------------------
# Utility functions
# -------------------
def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def has_admin_account(db: Session) -> bool:
    """Return whether the database already contains an administrator."""
    return db.query(User).filter(func.lower(User.role) == "admin").first() is not None


def count_admin_accounts(db: Session) -> int:
    """Count administrators so the two-account limit is enforced server-side."""
    return db.query(User).filter(func.lower(User.role) == "admin").count()


def is_valid_email_address(email: str) -> bool:
    normalized = normalize_email(email)
    if not normalized:
        return False
    try:
        validated = validate_email(normalized, check_deliverability=False)
        return bool(validated.normalized)
    except EmailNotValidError:
        return False


def validate_signup_payload(full_name: str, email: str, password: str, confirm_password: Optional[str]) -> None:
    if not (full_name or "").strip():
        raise HTTPException(status_code=400, detail="Full name is required")
    if not is_valid_email_address(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address")
    if len((password or "").strip()) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters long")
    if (confirm_password or password) != password:
        raise HTTPException(status_code=400, detail="Passwords do not match")


def normalize_user_role(role: Optional[str]) -> str:
    normalized = (role or "staff").strip().lower()
    return "admin" if normalized == "admin" else "staff"

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now() + (expires_delta or timedelta(hours=JWT_EXPIRES_HOURS))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)

def build_auth_response(user: User) -> LoginResponse:
    role = normalize_user_role(user.role)
    assigned_category = (user.assigned_category or "").strip() or None
    access_token = create_access_token(data={"sub": user.email, "role": role, "assigned_category": assigned_category})
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        email=user.email,
        full_name=user.full_name or user.email,
        role=role,
        assigned_category=assigned_category,
        can_process_pos=bool(user.can_process_pos),
        can_view_reports=bool(user.can_view_reports),
        can_adjust_inventory=bool(user.can_adjust_inventory),
        can_approve_voids=bool(user.can_approve_voids),
    )

def build_auth_response_with_session(user: User, db: Session) -> LoginResponse:
    """Builds auth response AND persists a single active session for admins and staff."""
    role = normalize_user_role(user.role)
    assigned_category = (user.assigned_category or "").strip() or None
    access_token = create_access_token(data={"sub": user.email, "role": role, "assigned_category": assigned_category})
    
    if role == "admin":
        store_active_session(user.email, access_token, db)
        write_audit_log(db, "SESSION_CREATED", user.email, detail=f"New admin session issued")
    else:
        user.active_token = access_token
        db.commit()
        write_audit_log(db, "SESSION_CREATED", user.email, detail=f"New staff session issued")

    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        email=user.email,
        full_name=user.full_name or user.email,
        role=role,
        assigned_category=assigned_category,
    )

def build_staff_response(user: User) -> StaffResponse:
    return StaffResponse(
        id=user.id,
        identifier=user.email,
        full_name=user.full_name or user.email,
        role=normalize_user_role(user.role),
        assigned_category=(user.assigned_category or "").strip() or None,
        account_status=user.account_status or "active",
        last_login=user.last_login,
        created_at=user.created_at,
        can_process_pos=bool(user.can_process_pos),
        can_view_reports=bool(user.can_view_reports),
        can_adjust_inventory=bool(user.can_adjust_inventory),
        can_approve_voids=bool(user.can_approve_voids),
    )

def generate_reset_code() -> str:
    return f"{random.randint(0, 999999):06d}"

def build_email_error_detail(exc: Exception) -> str:
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        if SMTP_HOST.lower() in {"smtp.gmail.com", "smtp-relay.gmail.com"}:
            return (
                "Unable to send reset code: Gmail rejected the SMTP login. "
                "Use a 16-character Google App Password for SMTP_PASSWORD, "
                "not your regular Gmail password."
            )
        return "Unable to send reset code: SMTP username or password was rejected."
    return f"Unable to send reset code: {exc}"

def send_mail_message(recipient_email: str, subject: str, body: str) -> None:
    if not SMTP_HOST:
        if EMAIL_DEV_FALLBACK:
            print(f"[DEV] Email to {recipient_email}:\n{body}")
            return
        raise HTTPException(status_code=500, detail="SMTP host is not configured.")
    if SMTP_HOST and (not SMTP_USER or not SMTP_PASSWORD):
        raise HTTPException(status_code=500, detail="SMTP username or password is not configured.")

    if MAIL_CLIENT is not None and MessageSchema is not None and MessageType is not None:
        message = MessageSchema(
            subject=subject,
            recipients=[recipient_email],
            body=body,
            subtype=MessageType.plain,
        )
        try:
            # FastMail.send_message is async, while this endpoint is sync.
            # Run the coroutine explicitly so the message is actually sent.
            asyncio.run(MAIL_CLIENT.send_message(message))
            return
        except Exception as exc:
            if EMAIL_DEV_FALLBACK:
                print(f"[DEV] Email to {recipient_email} failed via fastapi-mail: {exc}")
                print(f"[DEV] Email content:\n{body}")
                return
            raise HTTPException(status_code=500, detail=f"Unable to send email: {exc}")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_FROM or SMTP_USER or "no-reply@five-l.local"
    message["To"] = recipient_email
    message.set_content(body)

    try:
        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
                if SMTP_USER and SMTP_PASSWORD:
                    smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=SMTP_TIMEOUT) as smtp:
                if SMTP_USE_TLS:
                    smtp.starttls()
                if SMTP_USER and SMTP_PASSWORD:
                    smtp.login(SMTP_USER, SMTP_PASSWORD)
                smtp.send_message(message)
    except Exception as exc:
        if EMAIL_DEV_FALLBACK:
            print(f"[DEV] Email to {recipient_email} failed: {exc}")
            print(f"[DEV] Email content:\n{body}")
            return
        raise HTTPException(status_code=500, detail=build_email_error_detail(exc))


def send_reset_code_email(recipient_email: str, full_name: str, reset_code: str) -> None:
    body = (
        f"Hello {full_name or recipient_email},\n\n"
        f"Your Five L Pharmacy password reset code is: {reset_code}\n\n"
        f"This code expires in {RESET_CODE_EXPIRY_MINUTES} minutes.\n"
        "If you did not request this reset, you can ignore this email.\n"
    )
    send_mail_message(recipient_email, "Five L Pharmacy password reset code", body)


def send_login_otp_email(recipient_email: str, full_name: str, otp_code: str) -> None:
    body = (
        f"Hello {full_name or recipient_email},\n\n"
        f"Your Five L Pharmacy login verification code is: {otp_code}\n\n"
        "This code expires in 5 minutes.\n"
        "If you did not attempt to log in, you can ignore this email.\n"
    )
    send_mail_message(recipient_email, "Five L Pharmacy Login Verification Code", body)

# --------------------------------
# Security utilities
# --------------------------------

UNAUTHORIZED_DETAIL = "Unauthorized account access"

def check_allowed_email(email: str) -> None:
    """Raises 403 immediately if email is not in the allowlist."""
    if email.strip().lower() not in ALLOWED_EMAILS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=UNAUTHORIZED_DETAIL,
        )

def write_audit_log(db: Session, event_type: str, email: Optional[str] = None,
                    ip_address: Optional[str] = None, detail: Optional[str] = None) -> None:
    """Writes a row to audit_logs. Never raises — logging failures are silent."""
    try:
        log = AuditLog(event_type=event_type, email=email, ip_address=ip_address, detail=detail)
        db.add(log)
        db.commit()
    except Exception as exc:
        print(f"[AUDIT LOG ERROR] {exc}")
        db.rollback()

def write_medicine_audit_log(db: Session, action_type: str, medicine_name: str, performed_by: str, role: str, old_value: Optional[str] = None, new_value: Optional[str] = None) -> None:
    try:
        log = MedicineAuditLog(
            action_type=action_type,
            medicine_name=medicine_name,
            performed_by=performed_by,
            role=role,
            old_value=old_value,
            new_value=new_value
        )
        db.add(log)
        db.commit()
    except Exception as exc:
        print(f"[MEDICINE AUDIT LOG ERROR] {exc}")
        db.rollback()

def create_admin_alert(db: Session, alert_type: str, message: str) -> None:
    try:
        alert = AdminAlert(
            alert_type=alert_type,
            message=message,
            is_read=False
        )
        db.add(alert)
        db.commit()
    except Exception as exc:
        print(f"[ADMIN ALERT ERROR] {exc}")
        db.rollback()


def check_rate_limit(email: str, ip: str, db: Session) -> None:
    """Raises 429 if the email/IP is locked out due to too many failed attempts."""
    now = datetime.now()
    record = db.query(LoginAttempt).filter(LoginAttempt.email == email).first()
    if record and record.locked_until and record.locked_until > now:
        remaining = int((record.locked_until - now).total_seconds() // 60) + 1
        write_audit_log(db, "RATE_LIMITED", email, ip, f"Locked out for {remaining} more minutes")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {remaining} minute(s).",
        )

def record_attempt(email: str, ip: str, success: bool, db: Session) -> None:
    """Records a login attempt and applies lockout if threshold exceeded."""
    now = datetime.now()
    record = db.query(LoginAttempt).filter(LoginAttempt.email == email).first()

    if success:
        if record:
            record.attempt_count = 0
            record.locked_until = None
            db.commit()
        write_audit_log(db, "LOGIN_SUCCESS", email, ip)
        return

    if not record:
        record = LoginAttempt(email=email, ip_address=ip, attempt_count=1, locked_until=None)
        db.add(record)
    else:
        record.attempt_count += 1
        record.ip_address = ip

    if record.attempt_count >= MAX_LOGIN_ATTEMPTS:
        record.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
        db.commit()
        write_audit_log(db, "ACCOUNT_LOCKED", email, ip,
                        f"Locked after {record.attempt_count} failed attempts")
        create_admin_alert(db, "FAILED_LOGIN", f"Brute-force alert: Account {email} has been LOCKED after {record.attempt_count} failed login attempts from IP {ip}.")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts. Try again in {LOCKOUT_MINUTES} minute(s).",
        )
    db.commit()
    write_audit_log(db, "LOGIN_FAILED", email, ip,
                    f"Failed attempt {record.attempt_count}/{MAX_LOGIN_ATTEMPTS}")
    create_admin_alert(db, "FAILED_LOGIN", f"Failed login attempt for {email} from IP {ip} ({record.attempt_count}/{MAX_LOGIN_ATTEMPTS})")


def store_active_session(email: str, token: str, db: Session) -> None:
    """Persists the single valid JWT for an admin, invalidating any previous session."""
    expires_at = datetime.now() + timedelta(hours=JWT_EXPIRES_HOURS)
    existing = db.query(ActiveAdminSession).filter(ActiveAdminSession.email == email).first()
    if existing:
        write_audit_log(db, "SESSION_INVALIDATED", email, detail="Previous session replaced by new login")
        existing.token = token
        existing.created_at = datetime.now()
        existing.expires_at = expires_at
    else:
        db.add(ActiveAdminSession(email=email, token=token, expires_at=expires_at))
    db.commit()

def invalidate_admin_session(email: str, db: Session) -> None:
    """Removes the active admin session (logout or forced)."""
    db.query(ActiveAdminSession).filter(ActiveAdminSession.email == email).delete(synchronize_session=False)
    db.commit()
    write_audit_log(db, "SESSION_INVALIDATED", email, detail="Admin session invalidated")

# -------------------
# Auth dependency
# -------------------
async def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # 1. Parse the Bearer token
    try:
        scheme, token = (authorization or "").split()
        if scheme.lower() != "bearer":
            raise credentials_exception
    except Exception:
        raise credentials_exception

    # 2. Verify JWT signature and expiration
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        email: str = payload.get("sub")
        if not email:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # 3. Load user from DB
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise credentials_exception

    # 4. Check role-based constraints
    if normalize_user_role(user.role) == "admin":
        # Verify active session for admin
        active = db.query(ActiveAdminSession).filter(ActiveAdminSession.email == email).first()
        if not active or active.token != token:
            write_audit_log(db, "STALE_SESSION", email, detail="Admin token no longer active — newer session exists")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session invalidated. Please log in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if active.expires_at < datetime.now():
            invalidate_admin_session(email, db)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired. Please log in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )
    else:
        # Verify active session for staff
        if getattr(user, "active_token", None) and user.active_token != token:
            write_audit_log(db, "STALE_SESSION", email, detail="Staff token no longer active — newer session exists")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session invalidated. Please log in again.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 5. Check if account status is active
    status_val = getattr(user, "account_status", "active") or "active"
    if status_val != "active":
        write_audit_log(db, "BLOCKED_ACCESS", email, detail=f"Access blocked. Account is {status_val}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is {status_val}"
        )

    return user

def require_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if normalize_user_role(current_user.role) != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")
    return current_user

@app.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    """Returns the authenticated user's profile including their real role."""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name or "",
        "role": normalize_user_role(current_user.role),
        "assigned_category": current_user.assigned_category or "",
        "can_process_pos": bool(current_user.can_process_pos),
        "can_view_reports": bool(current_user.can_view_reports),
        "can_adjust_inventory": bool(current_user.can_adjust_inventory),
        "can_approve_voids": bool(current_user.can_approve_voids),
    }

# -------------------
# Database init & seed
# -------------------
def init_database():
    if RESET_DB_ON_STARTUP:
        with engine.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            Base.metadata.drop_all(bind=conn)
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    Base.metadata.create_all(bind=engine)
    ensure_inventory_schema_columns()
    ensure_user_role_column()
    ensure_transaction_bir_columns()
    seed_inventory_alert_configs()


DEFAULT_INVENTORY_ALERT_CONFIGS = (
    ('Tablet', 20, 30),
    ('Capsule', 20, 30),
    ('Syrup', 10, 30),
    ('Suspension', 10, 30),
    ('Injectable', 5, 60),
    ('Cream', 10, 30),
    ('Ointment', 10, 30),
    ('Drops', 10, 30),
    ('Other', 5, 30),
)


def seed_inventory_alert_configs():
    """Add default dosage-form rules without overwriting existing admin values."""
    with engine.begin() as conn:
        for dosage_form, low_stock_threshold, expiry_alert_days in DEFAULT_INVENTORY_ALERT_CONFIGS:
            conn.execute(text("""
                INSERT INTO inventory_alert_configs
                    (dosage_form, low_stock_threshold, expiry_alert_days, is_active)
                VALUES (:dosage_form, :low_stock_threshold, :expiry_alert_days, TRUE)
                ON DUPLICATE KEY UPDATE dosage_form = VALUES(dosage_form)
            """), {
                'dosage_form': dosage_form,
                'low_stock_threshold': low_stock_threshold,
                'expiry_alert_days': expiry_alert_days,
            })


def ensure_inventory_schema_columns():
    """Apply additive inventory migrations before any ORM query runs.

    ``create_all`` does not alter tables that already exist, so older
    installations can be missing columns added to the ORM models later.
    These migrations are intentionally additive and safe to run repeatedly.
    """
    migrations = {
        "medicines": {
            "is_archived": "BOOLEAN NOT NULL DEFAULT FALSE",
        },
        "medicine_supplies": {
            "received_date": "DATE NULL",
            "is_archived": "BOOLEAN NOT NULL DEFAULT FALSE",
        },
        # Older installations created this table from schema_fifo.sql with
        # ``batch_reference`` instead of ``batch_number``.  ``CREATE TABLE
        # IF NOT EXISTS`` cannot add the fields introduced by the current FIFO
        # implementation, so migrate them before the medicine sync below.
        "inventory_batches": {
            "batch_number": "VARCHAR(100) NULL",
            "cost_per_unit": "DECIMAL(10, 2) NULL DEFAULT 0.00",
            "notes": "VARCHAR(500) NULL",
            "is_expired": "BOOLEAN NOT NULL DEFAULT FALSE",
            "is_removed": "BOOLEAN NOT NULL DEFAULT FALSE",
            "removed_reason": "VARCHAR(120) NULL",
        },
    }

    with engine.begin() as conn:
        for table, columns in migrations.items():
            for column, definition in columns.items():
                exists = conn.execute(text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :table_name "
                    "AND column_name = :column_name"
                ), {"table_name": table, "column_name": column}).scalar_one()
                if not exists:
                    print(f"[INFO] Adding missing column {table}.{column}")
                    conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}"))

        # Some older databases already have is_removed, but without a default.
        # The startup sync inserts batches using raw SQL, so repair that schema
        # drift instead of relying only on the ORM default.
        is_removed_exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'inventory_batches' "
            "AND column_name = 'is_removed'"
        )).scalar_one()
        if is_removed_exists:
            conn.execute(text(
                "ALTER TABLE inventory_batches "
                "MODIFY COLUMN is_removed BOOLEAN NOT NULL DEFAULT FALSE"
            ))

        # Preserve batch identifiers from the legacy FIFO schema.  The fallback
        # includes the row id so every existing batch gets a stable identifier.
        batch_number_exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'inventory_batches' "
            "AND column_name = 'batch_number'"
        )).scalar_one()
        batch_reference_exists = conn.execute(text(
            "SELECT COUNT(*) FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'inventory_batches' "
            "AND column_name = 'batch_reference'"
        )).scalar_one()
        if batch_number_exists:
            source = "COALESCE(NULLIF(batch_reference, ''), CONCAT('LEGACY-BATCH-', id))" if batch_reference_exists else "CONCAT('LEGACY-BATCH-', id)"
            conn.execute(text(
                f"UPDATE inventory_batches SET batch_number = {source} "
                "WHERE batch_number IS NULL OR batch_number = ''"
            ))

def ensure_transaction_bir_columns():
    """Add transaction fields needed by POS, BIR reporting, and history."""
    columns = {
        # These fields were added to the ORM for POS cash/change tracking, but
        # older databases may have been created before they existed.
        "cash_received": "DECIMAL(12,2) NULL",
        "change_amount": "DECIMAL(12,2) NULL",
        "customer_id": "INT NULL",
        "customer_id_number": "VARCHAR(100) NULL",
        "discount_eligible": "BOOLEAN NULL",
        "eligible_subtotal": "DECIMAL(12,2) NULL",
        "non_eligible_subtotal": "DECIMAL(12,2) NULL",
        "sale_unit": "VARCHAR(32) NULL",
        "unit_factor": "DECIMAL(12,4) NULL",
        "category": "VARCHAR(255) NULL",
        "classification": "VARCHAR(64) NULL",
        "dosage_form": "VARCHAR(100) NULL",
        "customer_type": "VARCHAR(32) NULL",
        "gross_amount": "DECIMAL(12,2) NULL",
        "vatable_sales": "DECIMAL(12,2) NULL",
        "vat_amount": "DECIMAL(12,2) NULL",
        "vat_exempt_sales": "DECIMAL(12,2) NULL",
        "discount_amount": "DECIMAL(12,2) NULL",
        "senior_citizen_discount": "DECIMAL(12,2) NULL",
        "pwd_discount": "DECIMAL(12,2) NULL",
        "other_discount": "DECIMAL(12,2) NULL",
        "net_sales": "DECIMAL(12,2) NULL",
    }
    with engine.begin() as conn:
        for name, definition in columns.items():
            if not conn.execute(text(f"SHOW COLUMNS FROM transactions LIKE '{name}'")).first():
                conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {name} {definition}"))
        request_columns = {
            "adjustment_type": "VARCHAR(32) NULL",
            "old_quantity": "INT NULL",
            "new_quantity": "INT NULL",
        }
        for name, definition in request_columns.items():
            if not conn.execute(text(f"SHOW COLUMNS FROM price_history LIKE '{name}'")).first():
                conn.execute(text(f"ALTER TABLE price_history ADD COLUMN {name} {definition}"))

def ensure_user_role_column():
    with engine.begin() as conn:
        role_column = conn.execute(text("SHOW COLUMNS FROM users LIKE 'role'")).first()
        if not role_column:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'staff'"))
        assigned_column = conn.execute(text("SHOW COLUMNS FROM users LIKE 'assigned_category'")).first()
        if not assigned_column:
            conn.execute(text("ALTER TABLE users ADD COLUMN assigned_category VARCHAR(255) NULL"))
        else:
            conn.execute(text("ALTER TABLE users MODIFY COLUMN assigned_category VARCHAR(255) NULL"))

        account_status_column = conn.execute(text("SHOW COLUMNS FROM users LIKE 'account_status'")).first()
        if not account_status_column:
            conn.execute(text("ALTER TABLE users ADD COLUMN account_status VARCHAR(20) NOT NULL DEFAULT 'active'"))

        for column in ("can_process_pos", "can_view_reports", "can_adjust_inventory", "can_approve_voids"):
            if not conn.execute(text(f"SHOW COLUMNS FROM users LIKE '{column}'")).first():
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {column} TINYINT(1) NOT NULL DEFAULT 1"))

        created_by_admin_column = conn.execute(text("SHOW COLUMNS FROM users LIKE 'created_by_admin'")).first()
        if not created_by_admin_column:
            conn.execute(text("ALTER TABLE users ADD COLUMN created_by_admin INT NULL"))

        last_login_column = conn.execute(text("SHOW COLUMNS FROM users LIKE 'last_login'")).first()
        if not last_login_column:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_login DATETIME NULL"))

        active_token_column = conn.execute(text("SHOW COLUMNS FROM users LIKE 'active_token'")).first()
        if not active_token_column:
            conn.execute(text("ALTER TABLE users ADD COLUMN active_token TEXT NULL"))

        supplier_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'supplier'")).first()
        if not supplier_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN supplier VARCHAR(200) NULL"))

        added_by_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'added_by'")).first()
        if not added_by_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN added_by INT NULL, ADD CONSTRAINT fk_added_by FOREIGN KEY (added_by) REFERENCES users(id)"))

        reorder_level_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'reorder_level'")).first()
        if not reorder_level_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN reorder_level INT NULL"))

        expiry_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'expiry'")).first()
        if not expiry_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN expiry DATE NULL"))

        avg_daily_sales_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'avg_daily_sales'")).first()
        if not avg_daily_sales_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN avg_daily_sales FLOAT NULL"))

        unit_price_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'unit_price'")).first()
        if not unit_price_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN unit_price FLOAT NULL"))

        category_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'category'")).first()
        if not category_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN category VARCHAR(100) NULL"))

        created_at_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'created_at'")).first()
        if not created_at_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP"))

        last_restocked_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'last_restocked_at'")).first()
        if not last_restocked_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN last_restocked_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP"))

        new_arrival_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'is_new_arrival'")).first()
        if not new_arrival_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN is_new_arrival BOOLEAN NOT NULL DEFAULT FALSE"))

        product_code_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'product_code'")).first()
        if not product_code_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN product_code VARCHAR(50) NULL UNIQUE"))

        medicine_name_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'medicine_name'")).first()
        if not medicine_name_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN medicine_name VARCHAR(200) NULL"))

        classification_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'classification'")).first()
        if not classification_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN classification VARCHAR(50) NOT NULL DEFAULT 'Generic'"))

        dosage_form_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'dosage_form'")).first()
        if not dosage_form_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN dosage_form VARCHAR(100) NOT NULL DEFAULT 'Tablet'"))

        strength_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'strength'")).first()
        if not strength_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN strength VARCHAR(100) NULL"))

        volume_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'volume'")).first()
        if not volume_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN volume VARCHAR(100) NULL"))

        manufacturer_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'manufacturer'")).first()
        if not manufacturer_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN manufacturer VARCHAR(200) NULL"))

        unit_type_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'unit_type'")).first()
        if not unit_type_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN unit_type VARCHAR(50) NOT NULL DEFAULT 'pcs'"))

        base_unit_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'base_unit'")).first()
        if not base_unit_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN base_unit VARCHAR(20) NOT NULL DEFAULT 'pcs'"))

        purchase_unit_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'purchase_unit'")).first()
        if not purchase_unit_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN purchase_unit VARCHAR(50) NOT NULL DEFAULT 'pcs'"))

        conversion_factor_col = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'conversion_factor'")).first()
        if not conversion_factor_col:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN conversion_factor INT NOT NULL DEFAULT 1"))
        conn.execute(text("UPDATE medicines SET purchase_unit = COALESCE(NULLIF(purchase_unit, ''), unit_type, 'pcs'), conversion_factor = GREATEST(COALESCE(conversion_factor, 1), 1)"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS categories (
                category_id INT AUTO_INCREMENT PRIMARY KEY,
                category_name VARCHAR(150) NOT NULL,
                description TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS suppliers (
                supplier_id INT AUTO_INCREMENT PRIMARY KEY,
                supplier_name VARCHAR(200) NOT NULL,
                contact_person VARCHAR(200) NULL,
                phone VARCHAR(60) NULL,
                email VARCHAR(255) NULL,
                address TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS medicine_supplies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                medicine_id INT NOT NULL,
                batch_number VARCHAR(100) NOT NULL,
                quantity INT NOT NULL DEFAULT 0,
                supplier VARCHAR(200) NULL,
                expiry_date DATE NOT NULL,
                unit_cost FLOAT NOT NULL DEFAULT 0.0,
                selling_price FLOAT NOT NULL DEFAULT 0.0,
                is_archived BOOLEAN NOT NULL DEFAULT FALSE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (medicine_id) REFERENCES medicines(id) ON DELETE CASCADE
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS products (
                id INT AUTO_INCREMENT PRIMARY KEY,
                sku VARCHAR(64) NOT NULL UNIQUE,
                name VARCHAR(255) NOT NULL,
                category VARCHAR(120) NULL,
                unit_price DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
                reorder_level INT NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_products_name (name),
                INDEX idx_products_category (category)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS inventory_batches (
                id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT NOT NULL,
                batch_number VARCHAR(100) NOT NULL,
                quantity INT NOT NULL,
                remaining_quantity INT NOT NULL,
                received_date DATE NOT NULL,
                expiry_date DATE NOT NULL,
                supplier VARCHAR(255) NULL,
                cost_per_unit DECIMAL(10, 2) NULL DEFAULT 0.00,
                notes VARCHAR(500) NULL,
                is_expired BOOLEAN NOT NULL DEFAULT FALSE,
                is_removed BOOLEAN NOT NULL DEFAULT FALSE,
                removed_reason VARCHAR(120) NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_batches_fifo (product_id, expiry_date, received_date, id),
                INDEX idx_batches_available (product_id, remaining_quantity, expiry_date)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS stock_movements (
                id INT AUTO_INCREMENT PRIMARY KEY,
                medicine_id INT NULL,
                type VARCHAR(50) NULL,
                quantity INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS forecasts (
                forecast_id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT NOT NULL,
                forecast_month DATE NULL,
                predicted_demand FLOAT NULL,
                algorithm VARCHAR(80) NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_forecasts_product (product_id)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sales (
                id INT AUTO_INCREMENT PRIMARY KEY,
                sale_number VARCHAR(80) NOT NULL UNIQUE,
                cashier_name VARCHAR(255) NULL,
                customer_name VARCHAR(255) NULL,
                total_amount DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_sales_created_at (created_at)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS sale_items (
                id INT AUTO_INCREMENT PRIMARY KEY,
                sale_id INT NOT NULL,
                product_id INT NOT NULL,
                batch_id INT NOT NULL,
                quantity INT NOT NULL,
                unit_price DECIMAL(12, 2) NOT NULL,
                line_total DECIMAL(12, 2) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_sale_items_sale (sale_id),
                INDEX idx_sale_items_product (product_id),
                INDEX idx_sale_items_batch (batch_id)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS expired_stock_log (
                id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT NOT NULL,
                batch_id INT NOT NULL,
                batch_number VARCHAR(100) NULL,
                quantity_expired INT NOT NULL DEFAULT 0,
                expiry_date DATE NOT NULL,
                detected_date DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                action_taken VARCHAR(255) NULL,
                notes TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_expired_stock_product
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                CONSTRAINT fk_expired_stock_batch
                    FOREIGN KEY (batch_id) REFERENCES inventory_batches(id) ON DELETE CASCADE,
                INDEX idx_expired_stock_product (product_id),
                INDEX idx_expired_stock_expiry_date (expiry_date),
                INDEX idx_expired_stock_detected (detected_date)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS inventory_movements (
                id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT NOT NULL,
                batch_id INT NULL,
                movement_type VARCHAR(50) NOT NULL,
                quantity INT NOT NULL,
                reference_id INT NULL,
                reference_type VARCHAR(50) NULL,
                notes TEXT NULL,
                created_by VARCHAR(255) NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_inventory_movements_product
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                CONSTRAINT fk_inventory_movements_batch
                    FOREIGN KEY (batch_id) REFERENCES inventory_batches(id) ON DELETE SET NULL,
                INDEX idx_inventory_movements_product (product_id),
                INDEX idx_inventory_movements_batch (batch_id),
                INDEX idx_inventory_movements_type (movement_type),
                INDEX idx_inventory_movements_created_at (created_at)
            )
        """))

        inventory_product_rows = conn.execute(text("""
            SELECT id, COALESCE(medicine_name, name) AS product_name, category, unit_price, COALESCE(product_code, CONCAT('MED-', id)) AS sku
            FROM medicines
        """)).all()

        for med_id, product_name, category, unit_price, sku in inventory_product_rows:
            existing_product = conn.execute(text("SELECT id FROM products WHERE sku = :sku OR name = :name LIMIT 1"), {"sku": sku, "name": product_name}).first()
            if not existing_product:
                conn.execute(text("""
                    INSERT INTO products (sku, name, category, unit_price, reorder_level, is_active, created_at, updated_at)
                    VALUES (:sku, :name, :category, :unit_price, 0, TRUE, NOW(), NOW())
                """), {"sku": sku, "name": product_name, "category": category, "unit_price": float(unit_price or 0.0)})

        for med_id, product_name, category, unit_price, sku in inventory_product_rows:
            product_id = conn.execute(text("SELECT id FROM products WHERE sku = :sku OR name = :name LIMIT 1"), {"sku": sku, "name": product_name}).scalar()
            if not product_id:
                continue

            supply_rows = conn.execute(text("SELECT id, batch_number, quantity, supplier, expiry_date, received_date, unit_cost, selling_price FROM medicine_supplies WHERE medicine_id = :medicine_id"), {"medicine_id": med_id}).all()
            for supply_id, batch_number, quantity, supplier, expiry_date, received_date, unit_cost, selling_price in supply_rows:
                batch_name = batch_number or f"BATCH-{med_id}-{supply_id}"
                if conn.execute(text("SELECT id FROM inventory_batches WHERE product_id = :product_id AND batch_number = :batch_number LIMIT 1"), {"product_id": product_id, "batch_number": batch_name}).first():
                    continue
                expiry_value = expiry_date or date(2027, 12, 31)
                received_value = received_date or date.today()
                conn.execute(text("""
                    INSERT INTO inventory_batches (
                        product_id, batch_number, quantity, remaining_quantity, received_date,
                        expiry_date, supplier, cost_per_unit, notes, is_expired, is_removed, created_at, updated_at
                    )
                    VALUES (:product_id, :batch_number, :quantity, :remaining_quantity, :received_date, :expiry_date, :supplier, :cost_per_unit, :notes, FALSE, FALSE, NOW(), NOW())
                """), {
                    "product_id": product_id,
                    "batch_number": batch_name,
                    "quantity": int(quantity or 0),
                    "remaining_quantity": int(quantity or 0),
                    "received_date": received_value,
                    "expiry_date": expiry_value,
                    "supplier": supplier,
                    "cost_per_unit": float(unit_cost or 0.0),
                    "notes": f"Synced from medicine_supplies::{supply_id}",
                })

        # Ensure the actual user schema tables are present even if the DB was created earlier with a different structure.
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS categories (
                category_id INT AUTO_INCREMENT PRIMARY KEY,
                category_name VARCHAR(150) NOT NULL,
                description TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS suppliers (
                supplier_id INT AUTO_INCREMENT PRIMARY KEY,
                supplier_name VARCHAR(200) NOT NULL,
                contact_person VARCHAR(200) NULL,
                phone VARCHAR(60) NULL,
                email VARCHAR(255) NULL,
                address TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS forecasts (
                forecast_id INT AUTO_INCREMENT PRIMARY KEY,
                product_id INT NOT NULL,
                forecast_month DATE NULL,
                predicted_demand FLOAT NULL,
                algorithm VARCHAR(80) NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """))

        # Ensure price_history schema matches the app model.
        price_history_exists = conn.execute(text("SHOW TABLES LIKE 'price_history'")).first()
        if not price_history_exists:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS price_history (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    inventory_id INT NOT NULL,
                    old_price FLOAT NOT NULL,
                    new_price FLOAT NOT NULL,
                    updated_by VARCHAR(100) NULL,
                    status ENUM('PENDING', 'APPROVED', 'REJECTED') NOT NULL DEFAULT 'PENDING',
                    approved_by VARCHAR(255) NULL,
                    approved_at DATETIME NULL,
                    reason TEXT NULL,
                    remarks TEXT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (inventory_id) REFERENCES medicine_supplies(id) ON DELETE CASCADE
                )
            """))
        else:
            approved_by_col = conn.execute(text("SHOW COLUMNS FROM price_history LIKE 'approved_by'")) .first()
            if approved_by_col is None:
                conn.execute(text("ALTER TABLE price_history ADD COLUMN approved_by VARCHAR(255) NULL"))
            else:
                column_type = str(approved_by_col[1]).upper()
                if "INT" in column_type:
                    conn.execute(text("ALTER TABLE price_history MODIFY COLUMN approved_by VARCHAR(255) NULL"))

        # Migrate existing medicines data to supplies and generate codes
        medicines = conn.execute(text("SELECT id, name, stock, expiry, unit_price, supplier, product_code, medicine_name FROM medicines")).all()
        for idx, med in enumerate(medicines, start=1):
            med_id = med[0]
            name = med[1]
            stock = med[2] if med[2] is not None else 0
            expiry = med[3] if med[3] is not None else date(2027, 12, 31)
            unit_price = med[4] if med[4] is not None else 0.0
            supplier = med[5]
            product_code = med[6]
            med_name = med[7]
            
            if not product_code:
                current_year = datetime.now().year
                prefix = f"PRD-{current_year}-"
                formatted_code = f"{prefix}{idx:04d}"
                conn.execute(
                    text("UPDATE medicines SET product_code = :pc WHERE id = :id"),
                    {"pc": formatted_code, "id": med_id}
                )
                
            if not med_name:
                conn.execute(
                    text("UPDATE medicines SET medicine_name = :mn WHERE id = :id"),
                    {"mn": name, "id": med_id}
                )
                
            supplies_count = conn.execute(
                text("SELECT COUNT(*) FROM medicine_supplies WHERE medicine_id = :med_id"),
                {"med_id": med_id}
            ).scalar()
            
            if supplies_count == 0:
                conn.execute(
                    text("""
                        INSERT INTO medicine_supplies 
                        (medicine_id, batch_number, quantity, supplier, expiry_date, unit_cost, selling_price)
                        VALUES (:med_id, 'BATCH-INIT', :qty, :supplier, :expiry, :cost, :price)
                    """),
                    {
                        "med_id": med_id,
                        "qty": stock,
                        "supplier": supplier,
                        "expiry": expiry,
                        "cost": unit_price * 0.7,
                        "price": unit_price
                    }
                )
        
        conn.execute(text("UPDATE users SET role = 'staff' WHERE role IS NULL OR role = ''"))

        # Ensure new security tables exist (created by Base.metadata.create_all but added here as safety)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS active_admin_sessions (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) UNIQUE NOT NULL,
                token TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NOT NULL,
                INDEX idx_admin_session_email (email)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS login_attempts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                email VARCHAR(255) NOT NULL,
                ip_address VARCHAR(64),
                attempt_count INT DEFAULT 0,
                locked_until DATETIME NULL,
                last_attempt_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_login_attempts_email (email)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                event_type VARCHAR(64) NOT NULL,
                email VARCHAR(255) NULL,
                ip_address VARCHAR(64) NULL,
                detail TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_audit_email (email),
                INDEX idx_audit_event (event_type)
            )
        """))

        # -------------------------------------------------------------
        # Five L pharmacy system monitoring, tracking, & audit updates
        # -------------------------------------------------------------
        updated_by_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'updated_by'")).first()
        if not updated_by_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN updated_by INT NULL, ADD CONSTRAINT fk_updated_by FOREIGN KEY (updated_by) REFERENCES users(id)"))

        assigned_staff_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'assigned_staff'")).first()
        if not assigned_staff_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN assigned_staff INT NULL, ADD CONSTRAINT fk_assigned_staff FOREIGN KEY (assigned_staff) REFERENCES users(id)"))

        updated_at_column = conn.execute(text("SHOW COLUMNS FROM medicines LIKE 'updated_at'")).first()
        if not updated_at_column:
            conn.execute(text("ALTER TABLE medicines ADD COLUMN updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS medicine_audit_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                action_type VARCHAR(64) NOT NULL,
                medicine_name VARCHAR(200) NOT NULL,
                performed_by VARCHAR(255) NOT NULL,
                role VARCHAR(20) NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                old_value TEXT NULL,
                new_value TEXT NULL,
                INDEX idx_med_audit_action (action_type),
                INDEX idx_med_audit_name (medicine_name)
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS admin_alerts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                alert_type VARCHAR(64) NOT NULL,
                message TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                is_read BOOLEAN NOT NULL DEFAULT FALSE,
                INDEX idx_alert_type (alert_type)
            )
        """))


def seed_data():
    db = SessionLocal()
    try:
        # No default admin account is seeded anymore. The first admin account is created through signup.
        # Medicines
        if db.query(Medicine).count() == 0:
            medicines = [
                Medicine(name="Amoxicillin 500mg", category="Antibiotic", stock=44, reorder_level=50, expiry=datetime.strptime("2026-04-10","%Y-%m-%d").date(), avg_daily_sales=6, unit_price=18.0),
                Medicine(name="Paracetamol 500mg", category="Pain Relief", stock=120, reorder_level=30, expiry=datetime.strptime("2025-12-01","%Y-%m-%d").date(), avg_daily_sales=12, unit_price=2.5),
                Medicine(name="Ibuprofen 400mg", category="Anti-Inflammatory", stock=80, reorder_level=40, expiry=datetime.strptime("2026-02-15","%Y-%m-%d").date(), avg_daily_sales=8, unit_price=4.0),
                Medicine(name="Lisinopril 10mg", category="Blood Pressure", stock=60, reorder_level=25, expiry=datetime.strptime("2025-11-20","%Y-%m-%d").date(), avg_daily_sales=3, unit_price=8.5),
                Medicine(name="Metformin 500mg", category="Diabetes", stock=90, reorder_level=35, expiry=datetime.strptime("2026-01-10","%Y-%m-%d").date(), avg_daily_sales=5, unit_price=3.2),
            ]
            db.add_all(medicines)
        db.commit()
    finally:
        db.close()

def on_startup():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print(f"[INFO] Connected to DB: {MYSQL_URL}")
        init_database()
        seed_data()
        # Ensure 'received_date' column exists on medicine_supplies
        try:
            with engine.connect() as conn:
                check_sql = text("SELECT COUNT(*) as cnt FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'medicine_supplies' AND column_name = 'received_date'")
                res = conn.execute(check_sql).fetchone()
                cnt = res['cnt'] if res is not None and 'cnt' in res else (res[0] if res is not None else 0)
                if cnt == 0:
                    print('[INFO] Adding received_date column to medicine_supplies')
                    conn.execute(text("ALTER TABLE medicine_supplies ADD COLUMN received_date DATE NULL"))
        except Exception as exc:
            print(f"[WARN] Could not ensure received_date column: {exc}")
        try:
            with engine.connect() as conn:
                archived_col = conn.execute(text("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'medicine_supplies' AND column_name = 'is_archived'")).scalar() or 0
                if archived_col == 0:
                    print('[INFO] Adding is_archived column to medicine_supplies')
                    conn.execute(text("ALTER TABLE medicine_supplies ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE"))
                    conn.commit()
        except Exception as exc:
            print(f"[WARN] Could not ensure is_archived column: {exc}")
        try:
            with engine.connect() as conn:
                medicine_archived_col = conn.execute(text("SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'medicines' AND column_name = 'is_archived'")).scalar() or 0
                if medicine_archived_col == 0:
                    print('[INFO] Adding is_archived column to medicines')
                    conn.execute(text("ALTER TABLE medicines ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE"))
                    conn.commit()
        except Exception as exc:
            print(f"[WARN] Could not ensure medicine is_archived column: {exc}")
        app.state.db_ready = True
    except SQLAlchemyError as exc:
        app.state.db_ready = False
        app.state.startup_error = f"MySQL connection failed: {exc}"
        print(f"[ERROR] {app.state.startup_error}")

# -------------------
# Routes
# -------------------
@app.get("/health")
def health_check():
    # Mabilis na response — hindi na kailangan ng database query
    # Ang /health ay para lang i-check kung buhay ang app
    return {"status": "ok"}

def format_medicine_name(base_name: str, dosage_form: str = "Tablet", strength: Optional[str] = None, volume: Optional[str] = None) -> str:
    """Build a variant label without repeating a suffix already in the name."""
    base = re.sub(r"\s+", " ", (base_name or "").strip()).strip(" -/")
    if not base:
        return "Untitled Medicine"

    if dosage_form in ("Tablet", "Capsule"):
        dosage_part = strength
    elif dosage_form in ("Syrup", "Suspension"):
        dosage_part = volume
    else:
        dosage_part = strength or volume

    parts = [base]
    for suffix in (dosage_part, dosage_form or "Tablet"):
        suffix = str(suffix or "").strip()
        if suffix and not re.search(rf"(?:^|[\\s/-]){re.escape(suffix)}(?:$|[\\s/-])", base, re.IGNORECASE):
            parts.append(suffix)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def get_inventory_alert_config(db: Session, dosage_form: Optional[str]):
    """Return an active dosage-form rule, or None so callers can use globals."""
    normalized = (dosage_form or '').strip()
    if not normalized:
        return None
    return db.query(InventoryAlertConfig).filter(
        InventoryAlertConfig.dosage_form.ilike(normalized),
        InventoryAlertConfig.is_active.is_(True),
    ).first()


def get_inventory_alert_config_map(db: Session):
    return {
        row.dosage_form.strip().casefold(): row
        for row in db.query(InventoryAlertConfig).filter(InventoryAlertConfig.is_active.is_(True)).all()
        if row.dosage_form and row.dosage_form.strip()
    }


def populate_medicine_computed_fields(med, new_arrival_ids):
    # Supplies list
    supplies = med.supplies or []
    today = date.today()
    # Available stock excludes expired batches and batches within 30 days of expiry.
    # These batches remain visible in supply history and still drive expiry warnings.
    med.stock = sum(s.quantity for s in supplies if not s.is_archived and s.quantity > 0 and (not s.expiry_date or s.expiry_date > today + timedelta(days=30)))
    
    # Expiry: nearest expiry date (earliest)
    active_expiry_dates = [s.expiry_date for s in supplies if not s.is_archived and s.quantity > 0]
    if active_expiry_dates:
        med.expiry = min(active_expiry_dates)
    elif supplies:
        med.expiry = min(s.expiry_date for s in supplies)
    else:
        med.expiry = date(2027, 12, 31) # sensible default
        
    # Latest added supply for pricing and supplier
    if supplies:
        latest_supply = sorted(supplies, key=lambda s: s.id, reverse=True)[0]
        med.unit_price = latest_supply.selling_price
        med.supplier = latest_supply.supplier
    else:
        med.unit_price = med.unit_price or 0.0
        med.supplier = med.supplier or None
        
    # Format name in one place only. medicine_name remains the canonical base.
    med.name = format_medicine_name(med.medicine_name or med.name or "", med.dosage_form or "Tablet", med.strength, med.volume)
    
    med.is_new_arrival = med.id in new_arrival_ids
    # Ensure fields required by response model are non-null strings
    med.product_code = med.product_code or ""
    med.medicine_name = med.medicine_name or med.name or ""
    med.classification = med.classification or ""
    med.dosage_form = med.dosage_form or ""

    return med

@app.get("/inventory", response_model=List[MedicineResponse])
@app.get("/medicines", response_model=List[MedicineResponse])
def list_inventory(db: Session = Depends(get_db)):
    seven_days_ago = datetime.now() - timedelta(days=7)
    medicines = db.query(Medicine).all()
    new_arrival_ids = {
        med.id
        for med in medicines
        if med.created_at and med.created_at >= seven_days_ago
    }

    config_map = get_inventory_alert_config_map(db)
    for med in medicines:
        populate_medicine_computed_fields(med, new_arrival_ids)
        config = config_map.get((med.dosage_form or '').strip().casefold())
        # These response-only values let the existing frontend alert logic use
        # dosage-form rules while retaining global settings as its fallback.
        med.low_stock_threshold = config.low_stock_threshold if config else None
        med.expiry_alert_days = config.expiry_alert_days if config else None
    return medicines

@app.get("/sales/overview")
def sales_overview(db: Session = Depends(get_db)):
    now = datetime.now()
    today = now.date()
    week_start = today - timedelta(days=now.weekday())
    month_start = now.replace(day=1)

    today_sales = db.query(func.sum(SalesTransaction.total)).filter(
        func.date(SalesTransaction.transaction_date) == today
    ).scalar() or 0

    week_sales = db.query(func.sum(SalesTransaction.total)).filter(
        func.date(SalesTransaction.transaction_date) >= week_start
    ).scalar() or 0

    month_sales = db.query(func.sum(SalesTransaction.total)).filter(
        func.date(SalesTransaction.transaction_date) >= month_start
    ).scalar() or 0

    avg_basket = db.query(func.avg(SalesTransaction.total)).filter(
        func.date(SalesTransaction.transaction_date) >= month_start
    ).scalar() or 0

    month_target = 180000.0
    month_over_month = 0.05  # 5% growth demo
    monthly_forecast = month_sales * 1.1

    series = [
        {"label": "Week 1", "value": week_sales * 0.25},
        {"label": "Week 2", "value": week_sales * 0.3},
        {"label": "Week 3", "value": week_sales * 0.25},
        {"label": "Week 4", "value": week_sales * 0.2},
    ]

    return {
        "today_sales": today_sales,
        "week_sales": week_sales,
        "month_sales": month_sales,
        "average_basket": float(avg_basket),
        "month_target": month_target,
        "month_over_month": month_over_month * 100,
        "monthly_forecast": monthly_forecast,
        "series": series,
        "demand_spotlight": {
            "year": now.year,
            "current_leader": {
                "medicine_id": 1,
                "name": "Paracetamol 500mg",
                "category": "Pain Relief",
                "units_sold_year": 1200,
                "revenue_year": 3000,
                "projected_units_year_end": 1500,
                "projected_revenue_year_end": 3750,
                "daily_run_rate": 12,
                "prediction_basis": "historical avg",
                "confidence": "high",
                "source": "DB analytics"
            },
            "predicted_leader": {
                "medicine_id": 1,
                "name": "Paracetamol 500mg",
                "category": "Pain Relief",
                "units_sold_year": 1200,
                "revenue_year": 3000,
                "projected_units_year_end": 1600,
                "projected_revenue_year_end": 4000,
                "daily_run_rate": 14,
                "prediction_basis": "trend + seasonality",
                "confidence": "medium",
                "source": "ML model"
            },
            "source": "internal analytics"
        }
    }

def generate_product_code(db: Session) -> str:
    current_year = datetime.now().year
    prefix = f"PRD-{current_year}-"
    max_code = db.query(Medicine.product_code).filter(
        Medicine.product_code.like(f"{prefix}%")
    ).order_by(Medicine.product_code.desc()).first()
    
    if max_code and max_code[0]:
        try:
            last_seq = int(max_code[0].split("-")[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = 1
    else:
        new_seq = 1
        
    return f"{prefix}{new_seq:04d}"


def sync_inventory_batch_for_supply(db: Session, med: Medicine, supply: MedicineSupply):
    if med is None or supply is None:
        return

    sku = (med.product_code or f"MED-{med.id}").strip() or f"MED-{med.id}"
    product_name = (med.name or med.medicine_name or "Untitled Medicine").strip() or "Untitled Medicine"
    product_row = db.execute(
        text("SELECT id FROM products WHERE sku = :sku OR name = :name LIMIT 1"),
        {"sku": sku, "name": product_name},
    ).first()

    if product_row:
        product_id = product_row[0]
        db.execute(
            text("""
                UPDATE products
                SET name = :name,
                    category = :category,
                    unit_price = :unit_price,
                    updated_at = NOW()
                WHERE id = :product_id
            """),
            {
                "name": product_name,
                "category": med.category or "General",
                "unit_price": float(med.unit_price or supply.selling_price or 0.0),
                "product_id": product_id,
            },
        )
    else:
        result = db.execute(
            text("""
                INSERT INTO products (sku, name, category, unit_price, reorder_level, is_active, created_at, updated_at)
                VALUES (:sku, :name, :category, :unit_price, :reorder_level, TRUE, NOW(), NOW())
            """),
            {
                "sku": sku,
                "name": product_name,
                "category": med.category or "General",
                "unit_price": float(med.unit_price or supply.selling_price or 0.0),
                "reorder_level": med.reorder_level or 0,
            },
        )
        product_id = result.lastrowid

    batch_number = (supply.batch_number or f"BATCH-{med.id}-{supply.id}").strip() or f"BATCH-{med.id}-{supply.id}"
    existing_batch = db.execute(
        text("SELECT id FROM inventory_batches WHERE product_id = :product_id AND batch_number = :batch_number LIMIT 1"),
        {"product_id": product_id, "batch_number": batch_number},
    ).first()

    expiry_value = supply.expiry_date or date(2027, 12, 31)
    received_value = supply.received_date or date.today()
    notes = f"Synced from medicine_supplies::{supply.id}"

    if existing_batch:
        db.execute(
            text("""
                UPDATE inventory_batches
                SET quantity = :quantity,
                    remaining_quantity = :quantity,
                    supplier = :supplier,
                    expiry_date = :expiry_date,
                    received_date = :received_date,
                    cost_per_unit = :cost_per_unit,
                    notes = :notes,
                    is_expired = :is_expired,
                    updated_at = NOW()
                WHERE id = :batch_id
            """),
            {
                "quantity": int(supply.quantity or 0),
                "supplier": supply.supplier,
                "expiry_date": expiry_value,
                "received_date": received_value,
                "cost_per_unit": float(supply.unit_cost or 0.0),
                "notes": notes,
                "is_expired": bool(expiry_value <= date.today()),
                "batch_id": existing_batch[0],
            },
        )
    else:
        db.execute(
            text("""
                INSERT INTO inventory_batches (
                    product_id, batch_number, quantity, remaining_quantity, received_date,
                    expiry_date, supplier, cost_per_unit, notes, is_expired, is_removed, created_at, updated_at
                )
                VALUES (:product_id, :batch_number, :quantity, :remaining_quantity, :received_date, :expiry_date, :supplier, :cost_per_unit, :notes, :is_expired, FALSE, NOW(), NOW())
            """),
            {
                "product_id": product_id,
                "batch_number": batch_number,
                "quantity": int(supply.quantity or 0),
                "remaining_quantity": int(supply.quantity or 0),
                "received_date": received_value,
                "expiry_date": expiry_value,
                "supplier": supply.supplier,
                "cost_per_unit": float(supply.unit_cost or 0.0),
                "notes": notes,
                "is_expired": bool(expiry_value <= date.today()),
            },
        )

    db.execute(
        text("""
            INSERT INTO stock_movements (medicine_id, type, quantity, created_at)
            VALUES (:medicine_id, 'STOCK_IN', :quantity, NOW())
        """),
        {"medicine_id": med.id, "quantity": int(supply.quantity or 0)},
    )


@app.post("/medicines", response_model=MedicineResponse)
def create_medicine(medicine: MedicineCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    med_name = medicine.medicine_name or medicine.name
    if not med_name:
        raise HTTPException(status_code=422, detail="Medicine name is required")
        
    category = "Others" if (medicine.dosage_form or "").strip().lower() in {"other", "medical supply", "medical device"} else (medicine.category or "Pain Relief")
    classification = medicine.classification or "Generic"
    dosage_form = medicine.dosage_form or "Tablet"
    strength = medicine.strength
    volume = medicine.volume
    manufacturer = medicine.manufacturer
    base_unit = medicine.base_unit or "pcs"
    purchase_unit = medicine.purchase_unit or medicine.unit_type or "pcs"
    conversion_factor = max(1, int(medicine.conversion_factor or 1))
    
    prod_code = generate_product_code(db)
    
    formatted_name = format_medicine_name(med_name, dosage_form, strength, volume)
    
    new_med = Medicine(
        name=formatted_name,
        category=category,
        reorder_level=medicine.reorder_level or 10,
        avg_daily_sales=medicine.avg_daily_sales or 1.0,
        added_by=current_user.id,
        assigned_staff=medicine.assigned_staff,
        is_new_arrival=False,
        product_code=prod_code,
        medicine_name=med_name,
        classification=classification,
        dosage_form=dosage_form,
        strength=strength,
        volume=volume,
        manufacturer=manufacturer,
        unit_type=medicine.unit_type or purchase_unit,
        base_unit=base_unit,
        purchase_unit=purchase_unit,
        conversion_factor=conversion_factor
    )
    
    db.add(new_med)
    db.commit()
    db.refresh(new_med)
    
    qty = medicine.quantity if medicine.quantity is not None else medicine.stock
    exp_str = medicine.expiry_date or medicine.expiry
    
    expiry_date = date(2027, 12, 31)
    if exp_str:
        try:
            expiry_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            pass
            
    selling_price = medicine.selling_price if medicine.selling_price > 0 else (medicine.unit_price or 0.0)
    unit_cost = medicine.unit_cost if medicine.unit_cost > 0 else (selling_price * 0.7)
    supplier = medicine.supplier
    batch_num = medicine.batch_number or "BATCH-INIT"
    received_str = medicine.received_date or None
    received_date = None
    if received_str:
        try:
            received_date = datetime.strptime(received_str, "%Y-%m-%d").date()
        except ValueError:
            received_date = None
    
    new_supply = MedicineSupply(
        medicine_id=new_med.id,
        batch_number=batch_num,
        quantity=qty,
        supplier=supplier,
        expiry_date=expiry_date,
        received_date=received_date,
        unit_cost=unit_cost,
        selling_price=selling_price
    )
    db.add(new_supply)
    db.commit()

    # Keep the FIFO/POS tables in sync with every medicine created from the
    # Inventory screen. Previously only restocks were synced, so newly added
    # medicines existed in `medicines` but were missing from `products`.
    sync_inventory_batch_for_supply(db, new_med, new_supply)
    db.commit()
    
    new_val_str = f"Product Code: {prod_code}, Name: {formatted_name}, Category: {category}, Initial Stock: {qty}, Expiry: {expiry_date}"
    write_medicine_audit_log(
        db, 
        "medicine added", 
        formatted_name, 
        current_user.email, 
        current_user.role, 
        old_value=None, 
        new_value=new_val_str
    )
    
    if qty > 0:
        # sync_inventory_batch_for_supply already records this stock movement.
        new_med.is_new_arrival = True
        
    alert_config = get_inventory_alert_config(db, new_med.dosage_form)
    low_stock_threshold = alert_config.low_stock_threshold if alert_config else (new_med.reorder_level or 10)
    expiry_alert_days = alert_config.expiry_alert_days if alert_config else 60

    if qty <= low_stock_threshold:
        msg = f"Low stock alert: {new_med.name} was added with stock {qty}"
        create_admin_alert(db, "LOW_STOCK", msg)
        write_medicine_audit_log(db, "low stock alerts", new_med.name, current_user.email, current_user.role, old_value=None, new_value=f"Stock: {qty}")
        
    today = datetime.now().date()
    if expiry_date < today:
        msg = f"Expired medicine added: {new_med.name} (Expired on {expiry_date})"
        create_admin_alert(db, "EXPIRING", msg)
        write_medicine_audit_log(db, "expired medicines", new_med.name, current_user.email, current_user.role, old_value=None, new_value=f"Expiry: {expiry_date}")
    elif expiry_date <= today + timedelta(days=expiry_alert_days):
        msg = f"Medicine nearing expiration: {new_med.name} (Expires on {expiry_date})"
        create_admin_alert(db, "EXPIRING", msg)
        
    db.refresh(new_med)
    populate_medicine_computed_fields(new_med, {new_med.id} if new_med.is_new_arrival else set())
    return new_med

@app.put("/medicines/{medicine_id}", response_model=MedicineResponse)
def update_medicine(medicine_id: int, updates: MedicineUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    med = db.query(Medicine).filter(Medicine.id == medicine_id).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medicine not found")
    
    # 10. Prevent staff from editing records that they are not assigned or didn't add
    if current_user.role != "admin":
        assigned_categories = (current_user.assigned_category or "").split(",")
        assigned_categories = [c.strip().lower() for c in assigned_categories if c.strip()]
        med_category_lower = (med.category or "").strip().lower()
        if med.added_by != current_user.id and med_category_lower not in assigned_categories:
            raise HTTPException(status_code=403, detail="Staff is not allowed to edit this medicine record (unassigned category)")

    populate_medicine_computed_fields(med, set())
    old_stock = med.stock
    old_price = med.unit_price
    old_expiry = med.expiry
    old_reorder = med.reorder_level
    old_category = med.category
    old_supplier = med.supplier
    old_assigned_staff = med.assigned_staff

    changes_old = []
    changes_new = []

    med.updated_by = current_user.id
    
    diff = 0
    
    latest_supply = sorted(med.supplies, key=lambda s: s.id, reverse=True)[0] if med.supplies else None

    pending_stock_request = False
    pending_stock_target = None

    # Staff may edit permitted medicine metadata, but stock changes must go
    # through the same pending request/review flow as price updates.
    if (
        normalize_user_role(getattr(current_user, 'role', None)) != 'admin'
        and updates.stock is not None
        and updates.stock != old_stock
    ):
        if not latest_supply:
            raise HTTPException(status_code=422, detail='This medicine has no supply batch available for approval.')
        pending_stock_request = True
        pending_stock_target = max(0, int(updates.stock))
        db.add(PriceHistory(
            inventory_id=latest_supply.id,
            old_price=float(latest_supply.selling_price or 0),
            new_price=float(latest_supply.selling_price or 0),
            updated_by=current_user.email,
            status='PENDING',
            reason='Count correction submitted from medicine edit',
            adjustment_type='count_correction',
            old_quantity=old_stock,
            new_quantity=pending_stock_target,
        ))

    for field, value in updates.dict(exclude_unset=True).items():
        if field == "expiry" and value:
            new_expiry_date = datetime.strptime(value, "%Y-%m-%d").date()
            if new_expiry_date != old_expiry:
                changes_old.append(f"Expiry: {old_expiry}")
                changes_new.append(f"Expiry: {new_expiry_date}")
                if latest_supply:
                    latest_supply.expiry_date = new_expiry_date
        elif field == "stock" and value is not None:
            if value != old_stock:
                if pending_stock_request:
                    # Keep the live stock unchanged until admin approval.
                    continue
                changes_old.append(f"Stock: {old_stock}")
                changes_new.append(f"Stock: {value}")
                diff = value - old_stock
                if latest_supply:
                    latest_supply.quantity = max(0, latest_supply.quantity + diff)
        elif field == "unit_price" and value is not None:
            if value != old_price:
                changes_old.append(f"Price: ₱{old_price}")
                changes_new.append(f"Price: ₱{value}")
                if latest_supply:
                    # If staff submits this change, create a PENDING price history request
                    if normalize_user_role(getattr(current_user, 'role', None)) != 'admin':
                        ph = PriceHistory(
                            inventory_id=latest_supply.id,
                            old_price=float(latest_supply.selling_price) if latest_supply.selling_price is not None else float(old_price),
                            new_price=float(value),
                            updated_by=current_user.email if getattr(current_user, 'email', None) else None,
                            status='PENDING'
                        )
                        db.add(ph)
                        write_medicine_audit_log(
                            db,
                            "price update request",
                            med.name,
                            current_user.email,
                            current_user.role,
                            old_value=f"Price: {old_price:.2f}",
                            new_value=f"Requested Price: {float(value):.2f}",
                        )
                    else:
                        latest_supply.selling_price = value
                        ph = PriceHistory(
                            inventory_id=latest_supply.id,
                            old_price=float(old_price),
                            new_price=float(value),
                            updated_by=current_user.email if getattr(current_user, 'email', None) else None,
                            status='APPROVED',
                            approved_by=current_user.email if getattr(current_user, 'email', None) else None,
                            approved_at=datetime.now()
                        )
                        db.add(ph)
        elif field == "reorder_level" and value is not None:
            if value != old_reorder:
                changes_old.append(f"Reorder Level: {old_reorder}")
                changes_new.append(f"Reorder Level: {value}")
                setattr(med, field, value)
        elif field == "category" and value:
            if value != old_category:
                changes_old.append(f"Category: {old_category}")
                changes_new.append(f"Category: {value}")
                setattr(med, field, value)
        elif field == "supplier" and value is not None:
            if value != old_supplier:
                changes_old.append(f"Supplier: {old_supplier}")
                changes_new.append(f"Supplier: {value}")
                if latest_supply:
                    latest_supply.supplier = value
        elif field == "assigned_staff" and value is not None:
            if value != old_assigned_staff:
                changes_old.append(f"Assigned Staff ID: {old_assigned_staff}")
                changes_new.append(f"Assigned Staff ID: {value}")
                setattr(med, field, value)
        elif field == "is_new_arrival" and value is not None:
            setattr(med, field, value)
        elif field in ("medicine_name", "classification", "dosage_form", "strength", "volume", "manufacturer", "unit_type", "base_unit", "purchase_unit") and value is not None:
            setattr(med, field, value)
        elif field == "conversion_factor" and value is not None:
            med.conversion_factor = max(1, int(value))

    if any(k in updates.dict(exclude_unset=True) for k in ("medicine_name", "dosage_form", "strength", "volume")):
        med.name = format_medicine_name(med.medicine_name or med.name or "", med.dosage_form or "Tablet", med.strength, med.volume)

    db.commit()
    db.refresh(med)

    populate_medicine_computed_fields(med, set())

    if updates.stock is not None and updates.stock != old_stock and not pending_stock_request:
        if diff > 0:
            sm = StockMovement(medicine_id=med.id, type='STOCK_IN', quantity=diff)
            db.add(sm)
            med.last_restocked_at = datetime.now()
        elif diff < 0:
            sm = StockMovement(medicine_id=med.id, type='STOCK_OUT', quantity=abs(diff))
            db.add(sm)
        db.commit()

    if pending_stock_request:
        write_medicine_audit_log(
            db,
            "inventory adjustment request",
            med.name,
            current_user.email,
            current_user.role,
            old_value=f"Quantity: {old_stock}",
            new_value=f"Requested quantity: {pending_stock_target}\nType: count_correction\nAwaiting admin approval",
        )
        db.commit()

    if updates.stock is not None and updates.stock != old_stock and not pending_stock_request:
        write_medicine_audit_log(
            db, 
            "stock changes", 
            med.name, 
            current_user.email, 
            current_user.role, 
            old_value=f"Stock: {old_stock}", 
            new_value=f"Stock: {med.stock}"
        )

        if abs(diff) > 50:
            msg = f"Suspicious stock change for {med.name} by {current_user.email} ({current_user.role}): stock changed from {old_stock} to {med.stock} (diff: {diff})"
            create_admin_alert(db, "SUSPICIOUS_STOCK", msg)
        
        if med.stock < 0:
            msg = f"Suspicious stock edit for {med.name}: Stock set to negative count ({med.stock}) by {current_user.email}"
            create_admin_alert(db, "SUSPICIOUS_STOCK", msg)

        if current_user.role == "staff" and diff < 0:
            msg = f"Manual stock reduction for {med.name} by staff {current_user.email}: decreased by {abs(diff)} units (from {old_stock} to {med.stock})"
            create_admin_alert(db, "SUSPICIOUS_STOCK", msg)

    if changes_old:
        write_medicine_audit_log(
            db, 
            "medicine updated", 
            med.name, 
            current_user.email, 
            current_user.role, 
            old_value=", ".join(changes_old), 
            new_value=", ".join(changes_new)
        )

    alert_config = get_inventory_alert_config(db, med.dosage_form)
    low_stock_threshold = alert_config.low_stock_threshold if alert_config else (med.reorder_level or 10)
    expiry_alert_days = alert_config.expiry_alert_days if alert_config else 60

    if med.stock <= low_stock_threshold and old_stock > low_stock_threshold:
        msg = f"Low stock alert: {med.name} stock has fallen to {med.stock}"
        create_admin_alert(db, "LOW_STOCK", msg)
        write_medicine_audit_log(db, "low stock alerts", med.name, current_user.email, current_user.role, old_value=f"Stock: {old_stock}", new_value=f"Stock: {med.stock}")

    today = datetime.now().date()
    if med.expiry and med.expiry < today and (old_expiry is None or old_expiry >= today):
        msg = f"Medicine expired: {med.name} expired on {med.expiry}"
        create_admin_alert(db, "EXPIRING", msg)
        write_medicine_audit_log(db, "expired medicines", med.name, current_user.email, current_user.role, old_value=f"Expiry: {old_expiry}", new_value=f"Expiry: {med.expiry}")
    elif med.expiry and med.expiry >= today and med.expiry <= today + timedelta(days=expiry_alert_days) and (old_expiry is None or old_expiry > today + timedelta(days=expiry_alert_days)):
        msg = f"Medicine nearing expiration: {med.name} (Expires on {med.expiry})"
        create_admin_alert(db, "EXPIRING", msg)

    seven_days_ago = datetime.now() - timedelta(days=7)
    med.is_new_arrival = bool(med.created_at and med.created_at >= seven_days_ago)
    
    return med

@app.get("/medicines/{medicine_id}", response_model=MedicineResponse)
def get_medicine(medicine_id: int, db: Session = Depends(get_db)):
    med = db.query(Medicine).filter(Medicine.id == medicine_id).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medicine not found")
        
    seven_days_ago = datetime.now() - timedelta(days=7)
    new_arrival_ids = {med.id} if med.created_at and med.created_at >= seven_days_ago else set()
    populate_medicine_computed_fields(med, new_arrival_ids)
    
    return med

@app.delete("/medicines/{medicine_id}")
def delete_medicine(medicine_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    med = db.query(Medicine).filter(Medicine.id == medicine_id).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medicine not found")

    # 10. Prevent staff from deleting medicine records (Only Admin can delete)
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Staff members are not allowed to delete medicines")

    populate_medicine_computed_fields(med, set())
    # Audit log before deletion
    old_val_str = f"Category: {med.category}, Stock: {med.stock}, Price: ₱{med.unit_price}"
    write_medicine_audit_log(
        db, 
        "medicine deleted", 
        med.name, 
        current_user.email, 
        current_user.role, 
        old_value=old_val_str, 
        new_value=None
    )

    # Admin Alert
    create_admin_alert(
        db, 
        "MEDICINE_DELETED", 
        f"Medicine '{med.name}' was deleted by {current_user.email} (role: {current_user.role})"
    )

    db.delete(med)
    db.commit()
    return {"message": "Medicine deleted successfully", "id": medicine_id}

@app.patch("/medicines/{medicine_id}/archive", response_model=MedicineResponse)
def archive_medicine(
    medicine_id: int,
    is_archived: bool = True,
    reason: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    med = db.query(Medicine).filter(Medicine.id == medicine_id).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medicine not found")

    med.is_archived = is_archived
    for supply in med.supplies:
        supply.is_archived = is_archived
    populate_medicine_computed_fields(med, set())
    write_medicine_audit_log(
        db,
        "medicine archive" if is_archived else "medicine unarchive",
        med.name,
        current_user.email,
        current_user.role,
        old_value=f"Archived: {not is_archived}",
        new_value=f"Archived: {is_archived}\nReason: {reason or 'No reason provided'}",
    )
    db.commit()
    db.refresh(med)
    populate_medicine_computed_fields(med, set())
    return med

@app.post("/medicines/{medicine_id}/supplies", response_model=MedicineResponse)
def add_supply_batch(
    medicine_id: int,
    supply: MedicineSupplyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    med = db.query(Medicine).filter(Medicine.id == medicine_id).first()
    if not med:
        raise HTTPException(status_code=404, detail="Medicine not found")

    try:
        expiry_date = datetime.strptime(supply.expiry_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid expiry_date format. Use YYYY-MM-DD.")

    # Auto-generate batch number if not provided
    supply_count = db.query(MedicineSupply).filter(MedicineSupply.medicine_id == medicine_id).count()
    batch_num = supply.batch_number or f"BATCH-{supply_count + 1:03d}"

    selling_price = supply.selling_price if supply.selling_price and supply.selling_price > 0 else 0.0
    unit_cost = supply.unit_cost if supply.unit_cost and supply.unit_cost > 0 else (selling_price * 0.7)

    received_str = supply.received_date or None
    received_date = None
    if received_str:
        try:
            received_date = datetime.strptime(received_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid received_date format. Use YYYY-MM-DD.")

    if received_date and expiry_date <= received_date:
        raise HTTPException(status_code=422, detail="Expiry date must be after the received date.")

    new_supply = MedicineSupply(
        medicine_id=medicine_id,
        batch_number=batch_num,
        quantity=supply.quantity,
        supplier=supply.supplier,
        expiry_date=expiry_date,
        received_date=received_date,
        unit_cost=unit_cost,
        selling_price=selling_price
    )
    db.add(new_supply)
    db.commit()
    db.refresh(new_supply)

    sync_inventory_batch_for_supply(db, med, new_supply)
    db.commit()

    # Log stock movement
    sm = StockMovement(medicine_id=medicine_id, type='STOCK_IN', quantity=supply.quantity)
    db.add(sm)
    med.last_restocked_at = datetime.now()
    db.commit()

    # Populate computed fields
    db.refresh(med)
    populate_medicine_computed_fields(med, {medicine_id})

    write_medicine_audit_log(
        db,
        "stock changes",
        med.name,
        current_user.email,
        current_user.role,
        old_value=None,
        new_value=f"Added batch '{batch_num}' with qty {supply.quantity}, expiry {expiry_date}"
    )

    today = datetime.now().date()
    if expiry_date < today:
        create_admin_alert(db, "EXPIRING", f"Expired batch added for {med.name}: {batch_num} (expired {expiry_date})")
    elif expiry_date <= today + timedelta(days=60):
        create_admin_alert(db, "EXPIRING", f"Batch nearing expiry for {med.name}: {batch_num} (expires {expiry_date})")

    return med


@app.patch("/supplies/{supply_id}", response_model=MedicineResponse)
def update_supply_batch(
    supply_id: int,
    updates: MedicineSupplyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    supply = db.query(MedicineSupply).filter(MedicineSupply.id == supply_id).first()
    if not supply:
        raise HTTPException(status_code=404, detail="Supply not found")

    med = supply.medicine

    # Archive/unarchive is a state-only operation. Handle it before the
    # quantity, price, and date update paths so legacy batches with historical
    # date inconsistencies can still be archived safely.
    archive_only = (
        updates.is_archived is not None
        and updates.quantity is None
        and updates.selling_price is None
        and updates.expiry_date is None
        and updates.received_date is None
        and updates.supplier is None
        and updates.batch_number is None
        and updates.adjustment_type is None
    )
    if archive_only:
        supply.is_archived = updates.is_archived
        med.is_archived = bool(med.supplies) and all(batch.is_archived for batch in med.supplies)
        write_medicine_audit_log(
            db,
            "inventory archive" if updates.is_archived else "inventory unarchive",
            med.name,
            current_user.email,
            current_user.role,
            old_value=f"Batch: {supply.batch_number}",
            new_value=f"Archived: {updates.is_archived}\nReason: {updates.reason or 'No reason provided'}",
        )
        db.commit()
        db.refresh(med)
        populate_medicine_computed_fields(med, set())
        return med

    if updates.quantity is not None and (
        updates.quantity != supply.quantity
        or (updates.adjustment_type or '').strip().lower() in {'damages', 'expired'}
    ):
        old_qty = supply.quantity
        requested_qty = max(0, updates.quantity)
        adjustment_type = (updates.adjustment_type or 'count_correction').strip().lower()
        if adjustment_type not in {'damages', 'count_correction', 'expired'}:
            raise HTTPException(status_code=422, detail='Invalid adjustment type')

        # Damages and expired quantities are deductions. Count correction is
        # the actual final count entered by the user.
        if adjustment_type in {'damages', 'expired'}:
            if requested_qty > old_qty:
                raise HTTPException(
                    status_code=422,
                    detail=f'Cannot deduct {requested_qty} units. Only {old_qty} units are available in this batch.'
                )
            target_qty = old_qty - requested_qty
        else:
            target_qty = requested_qty

        if normalize_user_role(getattr(current_user, 'role', None)) != 'admin':
            db.add(PriceHistory(
                inventory_id=supply.id,
                old_price=float(supply.selling_price or 0),
                new_price=float(supply.selling_price or 0),
                updated_by=current_user.email,
                status='PENDING',
                reason=updates.reason,
                adjustment_type=adjustment_type,
                old_quantity=old_qty,
                new_quantity=target_qty,
            ))
            db.commit()
            write_medicine_audit_log(
                db,
                'inventory adjustment request',
                med.name,
                current_user.email,
                current_user.role,
                old_value=f'Quantity: {old_qty}',
                new_value=f'Requested quantity: {requested_qty}\nResulting quantity: {target_qty}\nType: {adjustment_type}\nReason: {updates.reason or ""}',
            )
            db.refresh(med)
            populate_medicine_computed_fields(med, set())
            return med

        supply.quantity = target_qty

        audit_details = "\n".join([
            f"Batch: {supply.batch_number}",
            f"Expiry: {supply.expiry_date}",
            f"Price: ₱{supply.selling_price:.2f}",
        ])

        adjustment_suffix = f"\nAdjustment Type: {updates.adjustment_type}" if updates.adjustment_type else ""
        reason_suffix = f"\nReason: {updates.reason}" if updates.reason else ""
        write_medicine_audit_log(
            db,
            "stock changes",
            med.name,
            current_user.email,
            current_user.role,
            old_value=f"Stock: {old_qty}\n{audit_details}",
            new_value=f"Stock: {supply.quantity}\n{audit_details}{adjustment_suffix}{reason_suffix}",
        )

    if updates.selling_price is not None and float(updates.selling_price) != float(supply.selling_price):
        old_price = float(supply.selling_price)
        new_price_val = float(updates.selling_price)

        # If a staff member submits a price change, create a PENDING PriceHistory request
        if normalize_user_role(getattr(current_user, 'role', None)) != 'admin':
            price_history = PriceHistory(
                inventory_id=supply.id,
                old_price=old_price,
                new_price=new_price_val,
                updated_by=current_user.email if getattr(current_user, 'email', None) else None,
                status='PENDING',
                reason=updates.reason,
            )
            db.add(price_history)

            write_medicine_audit_log(
                db,
                "price update request",
                med.name,
                current_user.email,
                current_user.role,
                old_value=f"Price: {old_price:.2f}",
                new_value=f"Requested Price: {new_price_val:.2f}\nReason: {updates.reason or ''}",
            )
        else:
            # Admins apply price changes immediately and record an APPROVED PriceHistory
            supply.selling_price = new_price_val
            price_history = PriceHistory(
                inventory_id=supply.id,
                old_price=old_price,
                new_price=new_price_val,
                updated_by=current_user.email if getattr(current_user, 'email', None) else None,
                status='APPROVED',
                approved_by=current_user.email if getattr(current_user, 'email', None) else None,
                approved_at=datetime.now(),
                reason=updates.reason,
            )
            db.add(price_history)

            audit_details = "\n".join([
                f"Batch: {supply.batch_number}",
                f"Expiry: {supply.expiry_date}",
                f"Stock: {supply.quantity}",
            ])

            price_reason_suffix = f"\nReason: {updates.reason}" if updates.reason else ""
            write_medicine_audit_log(
                db,
                "price update",
                med.name,
                current_user.email,
                current_user.role,
                old_value=f"Price: {old_price:.2f}\n{audit_details}",
                new_value=f"Price: {new_price_val:.2f}\n{audit_details}{price_reason_suffix}",
            )

    if updates.expiry_date is not None:
        try:
            supply.expiry_date = datetime.strptime(updates.expiry_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid expiry_date format. Use YYYY-MM-DD.")

    if updates.received_date is not None:
        if updates.received_date == "":
            supply.received_date = None
        else:
            try:
                supply.received_date = datetime.strptime(updates.received_date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(status_code=422, detail="Invalid received_date format. Use YYYY-MM-DD.")

    # Validate the date pair only when this request changes one of the dates.
    # Archive/unarchive-only requests must still work for legacy batches whose
    # historical received/expiry dates were saved in an invalid order.
    if (updates.expiry_date is not None or updates.received_date is not None) and supply.received_date and supply.expiry_date <= supply.received_date:
        raise HTTPException(status_code=422, detail="Expiry date must be after the received date.")

    if updates.supplier is not None:
        supply.supplier = updates.supplier

    if updates.batch_number is not None:
        supply.batch_number = updates.batch_number

    if updates.is_archived is not None:
        supply.is_archived = updates.is_archived
        # Keep the medicine out of the active list only when every batch is archived.
        # A medicine with at least one valid batch must remain active.
        med.is_archived = bool(med.supplies) and all(batch.is_archived for batch in med.supplies)
        write_medicine_audit_log(
            db,
            "inventory archive" if updates.is_archived else "inventory unarchive",
            med.name,
            current_user.email,
            current_user.role,
            old_value=f"Batch: {supply.batch_number}",
            new_value=f"Archived: {updates.is_archived}\nReason: {updates.reason or 'No reason provided'}",
        )

    # Keep the secondary FIFO batch record aligned with the authoritative
    # medicine_supplies expiry_date. Shelf Life is only used by the client to
    # suggest a default; it is never consulted here.
    if updates.expiry_date is not None or updates.received_date is not None:
        db.execute(
            text("""
                UPDATE inventory_batches
                SET expiry_date = :expiry_date,
                    received_date = :received_date,
                    is_expired = :is_expired,
                    updated_at = NOW()
                WHERE product_id = (
                    SELECT p.id FROM products p
                    WHERE p.sku = :sku OR p.name = :name
                    LIMIT 1
                )
                AND batch_number = :batch_number
            """),
            {
                "expiry_date": supply.expiry_date,
                "received_date": supply.received_date or date.today(),
                "is_expired": supply.expiry_date <= date.today(),
                "sku": (med.product_code or f"MED-{med.id}").strip() or f"MED-{med.id}",
                "name": (med.name or med.medicine_name or "Untitled Medicine").strip() or "Untitled Medicine",
                "batch_number": supply.batch_number,
            },
        )

    db.commit()
    db.refresh(med)
    populate_medicine_computed_fields(med, set())

    return med


class ReviewAction(BaseModel):
    action: str
    remarks: Optional[str] = None


def serialize_price_history_request(db: Session, price_history: PriceHistory) -> dict:
    supply = db.query(MedicineSupply).filter(MedicineSupply.id == price_history.inventory_id).first()
    medicine_name = supply.medicine.name if supply and supply.medicine else f"Supply #{price_history.inventory_id}"
    requested_by = price_history.updated_by or "Staff"

    if price_history.updated_by:
        requester = db.query(User).filter(User.email == price_history.updated_by).first()
        requested_by = requester.full_name if requester and requester.full_name else price_history.updated_by

    adjustment_type = (price_history.adjustment_type or '').strip().lower() or None
    adjustment_aliases = {
        'count correction': 'count_correction',
        'count-correction': 'count_correction',
        'countcorrection': 'count_correction',
        'damage': 'damages',
        'expire': 'expired',
    }
    adjustment_type = adjustment_aliases.get(adjustment_type, adjustment_type)
    # Backward compatibility for pending count edits created before the
    # adjustment_type column was populated.
    if not adjustment_type and (price_history.old_quantity is not None or price_history.new_quantity is not None):
        adjustment_type = 'count_correction'

    return {
        "id": price_history.id,
        "inventory_id": price_history.inventory_id,
        "old_price": price_history.old_price,
        "new_price": price_history.new_price,
        "updated_by": price_history.updated_by,
        "status": price_history.status,
        "approved_by": price_history.approved_by,
        "approved_at": price_history.approved_at,
        "reason": price_history.reason,
        "remarks": price_history.remarks,
        "created_at": price_history.created_at,
        "medicine_name": medicine_name,
        "requested_by": requested_by,
        "current_price": price_history.old_price,
        "requested_price": price_history.new_price,
        "adjustment_type": adjustment_type,
        "old_quantity": price_history.old_quantity,
        "new_quantity": price_history.new_quantity,
    }


@app.get("/admin/price-update-requests", response_model=List[PriceHistoryResponse])
def list_price_update_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user)
):
    requests = db.query(PriceHistory).filter(PriceHistory.status == 'PENDING').order_by(PriceHistory.created_at.desc()).all()
    return [serialize_price_history_request(db, request) for request in requests]


@app.patch("/admin/price-update-requests/{request_id}", response_model=PriceHistoryResponse)
def review_price_update_request(
    request_id: int,
    payload: ReviewAction,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user)
):
    try:
        ph = db.query(PriceHistory).filter(PriceHistory.id == request_id).first()
        if not ph:
            raise HTTPException(status_code=404, detail=f"Price update request #{request_id} not found")
        
        current_status = (ph.status or "").strip().upper()
        if current_status and current_status not in ('PENDING', ''):
            raise HTTPException(
                status_code=400, 
                detail=f"Request is already {current_status}. Cannot review a {current_status} request."
            )

        action = (payload.action or "").strip().lower()
        
        if not action:
            raise HTTPException(status_code=400, detail="Action (approve/reject) is required")
        
        supply = db.query(MedicineSupply).filter(MedicineSupply.id == ph.inventory_id).first()

        if ph.adjustment_type:
            if not supply:
                raise HTTPException(status_code=404, detail=f"Associated supply #{ph.inventory_id} not found")
            if action in ('approve', 'approved'):
                supply.quantity = max(0, int(ph.new_quantity if ph.new_quantity is not None else supply.quantity))
                ph.status = 'APPROVED'
                ph.approved_by = current_user.email
                ph.approved_at = datetime.now()
                ph.remarks = payload.remarks or ''
                db.commit()
                write_medicine_audit_log(
                    db,
                    'inventory adjustment approved',
                    supply.medicine.name,
                    current_user.email,
                    current_user.role,
                    old_value=f'Quantity: {ph.old_quantity}',
                    new_value=f'Quantity: {supply.quantity}\nType: {ph.adjustment_type}\nRemarks: {ph.remarks}',
                )
            elif action in ('reject', 'decline', 'rejected'):
                ph.status = 'REJECTED'
                ph.approved_by = current_user.email
                ph.approved_at = datetime.now()
                ph.remarks = payload.remarks or ''
                db.commit()
            else:
                raise HTTPException(status_code=400, detail=f"Invalid action '{action}'. Use 'approve' or 'reject'")
            db.refresh(ph)
            return serialize_price_history_request(db, ph)

        if action in ('approve', 'approved'):
            if not supply:
                raise HTTPException(status_code=404, detail=f"Associated supply #{ph.inventory_id} not found")
            old_price = float(supply.selling_price)
            new_price = float(ph.new_price)
            supply.selling_price = new_price
            ph.status = 'APPROVED'
            ph.approved_by = current_user.email
            ph.approved_at = datetime.now()
            ph.remarks = payload.remarks or ""
            db.add(supply)
            db.add(ph)
            db.commit()

            medicine_name = supply.medicine.name if supply and supply.medicine else f"Supply #{ph.inventory_id}"
            write_medicine_audit_log(
                db,
                "price update",
                medicine_name,
                current_user.email,
                current_user.role,
                old_value=f"Price: {old_price:.2f}",
                new_value=f"Price: {new_price:.2f}\nRequest ID: {ph.id}",
            )

        elif action in ('reject', 'decline', 'rejected'):
            ph.status = 'REJECTED'
            ph.approved_by = current_user.email
            ph.approved_at = datetime.now()
            ph.remarks = payload.remarks or ""
            db.add(ph)
            db.commit()

            medicine_name = supply.medicine.name if supply and supply.medicine else f"Supply #{ph.inventory_id}"
            write_medicine_audit_log(
                db,
                "price update rejected",
                medicine_name,
                current_user.email,
                current_user.role,
                old_value=f"Requested Price: {ph.new_price:.2f}",
                new_value=f"Status: REJECTED\nRemarks: {ph.remarks or ''}",
            )
        else:
            raise HTTPException(status_code=400, detail=f"Invalid action '{action}'. Use 'approve' or 'reject'")

        db.refresh(ph)
        return serialize_price_history_request(db, ph)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        import traceback
        print(f"[ERROR] Price request review failed: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")


@app.get("/staff", response_model=List[StaffResponse])
def list_staff(db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    users = (
        db.query(User)
        .filter(User.role == "staff")
        .order_by(User.full_name.asc())
        .all()
    )
    return [build_staff_response(user) for user in users]

@app.post("/staff", response_model=StaffResponse)
def create_staff(
    payload: StaffCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    identifier = normalize_email(payload.identifier)
    full_name = payload.full_name.strip()
    assigned_category = payload.assigned_category.strip()
    password = (payload.password or "staff123").strip()

    if not full_name:
        raise HTTPException(status_code=400, detail="Staff name is required")
    if not identifier:
        raise HTTPException(status_code=400, detail="Staff username is required")
    if not assigned_category:
        raise HTTPException(status_code=400, detail="Assigned category is required")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="Staff password must be at least 6 characters long")
    if db.query(User).filter(User.email == identifier).first():
        raise HTTPException(status_code=400, detail="Staff username already exists")

    staff_user = User(
        full_name=full_name,
        email=identifier,
        password_hash=get_password_hash(password),
        role="staff",
        assigned_category=assigned_category,
        account_status="active",
        created_by_admin=current_user.id
    )
    db.add(staff_user)
    db.commit()
    db.refresh(staff_user)
    write_audit_log(db, "STAFF_CREATED", identifier, detail=f"Created by admin {current_user.email}")
    return build_staff_response(staff_user)

@app.put("/staff/{staff_id}/assignment", response_model=StaffResponse)
def update_staff_assignment(
    staff_id: int,
    payload: StaffAssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    staff_user = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not staff_user:
        raise HTTPException(status_code=404, detail="Staff not found")

    assigned_category = payload.assigned_category.strip()
    if not assigned_category:
        raise HTTPException(status_code=400, detail="Assigned category is required")

    staff_user.assigned_category = assigned_category
    db.commit()
    db.refresh(staff_user)
    write_audit_log(db, "STAFF_MODIFICATION", staff_user.email, detail=f"Assigned category updated to {assigned_category} by admin {current_user.email}")
    return build_staff_response(staff_user)

@app.put("/staff/{staff_id}/status", response_model=StaffResponse)
def update_staff_status(
    staff_id: int,
    payload: StaffStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    staff_user = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not staff_user:
        raise HTTPException(status_code=404, detail="Staff not found")

    new_status = payload.account_status.strip().lower()
    if new_status not in {"active", "disabled", "suspended"}:
        raise HTTPException(status_code=400, detail="Invalid status value")

    staff_user.account_status = new_status
    if new_status != "active":
        staff_user.active_token = None
        write_audit_log(db, "SESSION_INVALIDATED", staff_user.email, detail=f"Session terminated because account status changed to {new_status}")

    db.commit()
    db.refresh(staff_user)
    write_audit_log(db, "ACCOUNT_STATUS_CHANGED", staff_user.email, detail=f"Account status changed to {new_status} by admin {current_user.email}")
    return build_staff_response(staff_user)

@app.put("/staff/{staff_id}/permissions", response_model=StaffResponse)
def update_staff_permissions(
    staff_id: int,
    payload: StaffPermissionsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    staff_user = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not staff_user:
        raise HTTPException(status_code=404, detail="Staff not found")
    for field in ("can_process_pos", "can_view_reports", "can_adjust_inventory", "can_approve_voids"):
        setattr(staff_user, field, bool(getattr(payload, field)))
    db.commit()
    db.refresh(staff_user)
    write_audit_log(db, "STAFF_PERMISSIONS_CHANGED", staff_user.email, detail=f"Permissions updated by admin {current_user.email}")
    return build_staff_response(staff_user)

@app.delete("/staff/{staff_id}")
def delete_staff(
    staff_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_user),
):
    staff_user = db.query(User).filter(User.id == staff_id, User.role == "staff").first()
    if not staff_user:
        raise HTTPException(status_code=404, detail="Staff not found")

    # Clear every staff reference before deleting the account. These columns
    # are foreign keys, so clearing only added_by can still block deletion
    # when the staff member edited or was assigned to a medicine.
    db.query(Medicine).filter(Medicine.added_by == staff_id).update({Medicine.added_by: None})
    db.query(Medicine).filter(Medicine.updated_by == staff_id).update({Medicine.updated_by: None})
    db.query(Medicine).filter(Medicine.assigned_staff == staff_id).update({Medicine.assigned_staff: None})

    db.delete(staff_user)
    db.commit()
    return {"message": "Staff member deleted successfully"}

# -------------------
# Auth endpoints
# -------------------
@app.get("/auth/setup-status", response_model=SetupStatusResponse)
def get_setup_status(db: Session = Depends(get_db)):
    admin_count = count_admin_accounts(db)
    admin_exists = admin_count > 0
    return SetupStatusResponse(
        has_admin=admin_exists,
        setup_required=not admin_exists,
        admin_count=admin_count,
        max_admins=MAX_ADMIN_ACCOUNTS,
        allow_signup=admin_count < MAX_ADMIN_ACCOUNTS,
    )

@app.post("/signup", response_model=LoginResponse)
def signup(payload: UserCreate):
    normalized_email = normalize_email(payload.email)
    full_name = (payload.full_name or "").strip()
    password = (payload.password or "").strip()
    confirm_password = (payload.confirm_password or "").strip()

    db = SessionLocal()
    try:
        if count_admin_accounts(db) >= MAX_ADMIN_ACCOUNTS:
            raise HTTPException(
                status_code=403,
                detail="The maximum of 2 administrator accounts has been reached.",
            )

        validate_signup_payload(full_name, normalized_email, password, confirm_password)

        if db.query(User).filter(User.email == normalized_email).first():
            raise HTTPException(status_code=409, detail="This email is already registered")

        user = User(
            full_name=full_name,
            email=normalized_email,
            password_hash=get_password_hash(password),
            role="admin",
            account_status="active",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        write_audit_log(db, "SIGNUP_CREATED", normalized_email, detail="Initial account created via signup")
        return build_auth_response_with_session(user, db)
    finally:
        db.close()

@app.post("/login", response_model=Union[LoginResponse, LoginOTPRequiredResponse])
def login(form_data: LoginRequest, request_obj: Request = None):
    db = SessionLocal()
    try:
        login_identifier = form_data.userstaff or form_data.email or ""
        normalized_email = normalize_email(login_identifier)
        ip = "unknown"

        if not normalized_email:
            raise HTTPException(status_code=400, detail="Username is required")

        # 1. Brute-force check
        check_rate_limit(normalized_email, ip, db)

        # 2. Check if user exists in DB first
        user = db.query(User).filter(User.email == normalized_email).first()
        if not user:
            write_audit_log(db, "UNAUTHORIZED_LOGIN", normalized_email, ip,
                            "Login attempted for non-existent account")
            raise HTTPException(status_code=401, detail="Unauthorized account")

        # 3. Role and Allowlist/Status checks
        if normalize_user_role(user.role) != "admin":
            status_val = getattr(user, "account_status", "active") or "active"
            if status_val != "active":
                write_audit_log(db, "DISABLED_LOGIN", normalized_email, ip,
                                f"Login attempted on a {status_val} account")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Account is {status_val}"
                )

        if verify_password(form_data.password, user.password_hash):
            # Correct credentials
            record_attempt(normalized_email, ip, success=True, db=db)
            
            # If admin, enforce OTP (unless in skip list)
            skip_otp_emails = {'jeremiassalvador@five-l'}
            if normalize_user_role(user.role) == "admin" and normalized_email not in skip_otp_emails:
                otp_code = generate_reset_code()
                expires_at = datetime.now() + timedelta(minutes=5)

                db.query(LoginOTPToken).filter(
                    LoginOTPToken.email == normalized_email,
                    LoginOTPToken.used_at.is_(None),
                ).delete(synchronize_session=False)

                db.add(LoginOTPToken(
                    email=normalized_email,
                    otp_code=otp_code,
                    used_at=None,
                    expires_at=expires_at,
                ))
                db.commit()

                send_login_otp_email(
                    recipient_email=normalized_email,
                    full_name=user.full_name or user.email,
                    otp_code=otp_code,
                )

                return LoginOTPRequiredResponse(
                    requires_otp=True,
                    email=normalized_email,
                    message="OTP sent to your email."
                )
            else:
                # Staff or skip-OTP admin: skip OTP, login immediately, update last_login
                user.last_login = datetime.now()
                db.commit()
                return build_auth_response_with_session(user, db)
        else:
            # Wrong password
            record_attempt(normalized_email, ip, success=False, db=db)
    finally:
        db.close()
    raise HTTPException(status_code=401, detail="Incorrect email or password")

@app.post("/login/verify", response_model=LoginResponse)
def verify_login_otp(payload: LoginOTPVerifyRequest):
    normalized_email = normalize_email(payload.email)
    otp_code = payload.code.strip()

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == normalized_email).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Allowlist gate for admin, status gate for staff
        if normalize_user_role(user.role) != "admin":
            status_val = getattr(user, "account_status", "active") or "active"
            if status_val != "active":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Account is {status_val}")

        otp_record = (
            db.query(LoginOTPToken)
            .filter(
                LoginOTPToken.email == normalized_email,
                LoginOTPToken.used_at.is_(None),
            )
            .order_by(LoginOTPToken.expires_at.desc())
            .first()
        )

        if not otp_record:
            raise HTTPException(status_code=400, detail="No OTP found or already used")
        if otp_record.expires_at < datetime.now():
            raise HTTPException(status_code=400, detail="OTP has expired")
        if otp_record.otp_code != otp_code:
            write_audit_log(db, "OTP_FAILED", normalized_email, detail="Wrong OTP entered")
            raise HTTPException(status_code=400, detail="Invalid OTP")

        otp_record.used_at = datetime.now()
        db.commit()

        # Issue session (single-session enforced inside build_auth_response_with_session)
        return build_auth_response_with_session(user, db)
    finally:
        db.close()

@app.post("/auth/password-reset/request", response_model=PasswordResetRequestResponse)
def request_password_reset(payload: PasswordResetRequest):
    normalized_email = normalize_email(payload.email)
    if not normalized_email:
        raise HTTPException(status_code=400, detail="Email is required")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == normalized_email).first()
        if not user:
            raise HTTPException(status_code=404, detail="Email is not registered")

        reset_code = generate_reset_code()
        expires_at = datetime.now() + timedelta(minutes=RESET_CODE_EXPIRY_MINUTES)

        db.query(PasswordResetToken).filter(
            PasswordResetToken.email == normalized_email,
            PasswordResetToken.used_at.is_(None),
        ).delete(synchronize_session=False)

        db.add(PasswordResetToken(
            email=normalized_email,
            reset_code=reset_code,
            used_at=None,
            expires_at=expires_at,
        ))
        db.commit()

        try:
            send_reset_code_email(
                recipient_email=normalized_email,
                full_name=user.full_name or user.email,
                reset_code=reset_code,
            )
        except HTTPException:
            db.query(PasswordResetToken).filter(
                PasswordResetToken.email == normalized_email,
                PasswordResetToken.reset_code == reset_code,
                PasswordResetToken.used_at.is_(None),
            ).delete(synchronize_session=False)
            db.commit()
            raise

        return PasswordResetRequestResponse(message="Reset code sent to your email.")
    finally:
        db.close()

@app.post("/auth/password-reset/confirm", response_model=LoginResponse)
def confirm_password_reset(payload: PasswordResetConfirmRequest):
    normalized_email = normalize_email(payload.email)
    reset_code = payload.code.strip()
    new_password = payload.new_password.strip()

    if not normalized_email:
        raise HTTPException(status_code=400, detail="Email is required")
    if not reset_code:
        raise HTTPException(status_code=400, detail="Reset code is required")
    if len(new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters long")

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == normalized_email).first()
        if not user:
            raise HTTPException(status_code=404, detail="Email is not registered")

        token = (
            db.query(PasswordResetToken)
            .filter(
                PasswordResetToken.email == normalized_email,
                PasswordResetToken.used_at.is_(None),
            )
            .order_by(PasswordResetToken.expires_at.desc())
            .first()
        )

        if not token:
            raise HTTPException(status_code=400, detail="No reset code found for this email")
        if token.expires_at < datetime.now():
            raise HTTPException(status_code=400, detail="Reset code has expired")
        if token.reset_code != reset_code:
            raise HTTPException(status_code=400, detail="Invalid reset code")

        user.password_hash = get_password_hash(new_password)
        token.used_at = datetime.now()
        db.commit()

        write_audit_log(db, "PASSWORD_RESET", normalized_email)
        return build_auth_response_with_session(user, db)
    finally:
        db.close()

@app.post("/auth/google", response_model=LoginResponse)
def google_auth(payload: GoogleAuthRequest):
    """Handle Google OAuth callback for an existing database account."""
    db = SessionLocal()
    try:
        if payload.email:
            normalized_email = normalize_email(payload.email)
            user_full_name = payload.full_name or payload.email.split('@')[0]
        else:
            # No email from Google — reject
            raise HTTPException(status_code=403, detail=UNAUTHORIZED_DETAIL)

        user = db.query(User).filter(User.email == normalized_email).first()
        if not user:
            write_audit_log(db, "GOOGLE_AUTH_DENIED", normalized_email,
                            detail="Google login attempted for non-existent account")
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Google account is not registered in the system")

        if normalize_user_role(user.role) != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only administrator accounts can use Google login")

        write_audit_log(db, "GOOGLE_AUTH_SUCCESS", normalized_email)
        return build_auth_response_with_session(user, db)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Google auth failed: {str(e)}")
    finally:
        db.close()

# -------------------------------------------------------------
# Five L Pharmacy - Admin Monitoring & Auditing Endpoints
# -------------------------------------------------------------
@app.get("/admin/dashboard-stats")
def get_admin_dashboard_stats(db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    try:
        today = datetime.now().date()
        thirty_days_ago = datetime.now() - timedelta(days=30)

        # Derive medicine-level expiry from the actual per-batch expiry dates
        # before calculating dashboard counts and panels.
        dashboard_medicines = db.query(Medicine).all()
        dashboard_arrivals = {
            med.id for med in dashboard_medicines
            if med.created_at and med.created_at >= datetime.now() - timedelta(days=7)
        }
        for med in dashboard_medicines:
            populate_medicine_computed_fields(med, dashboard_arrivals)

        # 1. Base counts. Dosage-form rules override globals when present.
        total_meds = len(dashboard_medicines)
        low_stock_meds = []
        expired_meds = []
        nearing_expiry_meds = []
        for med in dashboard_medicines:
            config = get_inventory_alert_config(db, med.dosage_form)
            low_threshold = config.low_stock_threshold if config else (med.reorder_level or 10)
            expiry_days = config.expiry_alert_days if config else 60
            if (med.stock or 0) <= low_threshold:
                low_stock_meds.append(med)
            if med.expiry and med.expiry < today:
                expired_meds.append(med)
            elif med.expiry and today <= med.expiry <= today + timedelta(days=expiry_days):
                nearing_expiry_meds.append(med)

        # 2. Staff contributions
        staff_users = db.query(User).filter(User.role == "staff").all()
        total_medicines_by_staff = []
        for s in staff_users:
            cnt = db.query(Medicine).filter(Medicine.added_by == s.id).count()
            total_medicines_by_staff.append({"staff_name": s.full_name, "email": s.email, "count": cnt})

        # 3. Most active staff
        active_logs = db.query(MedicineAuditLog).filter(MedicineAuditLog.timestamp >= thirty_days_ago).all()
        activity_count = {}
        for log in active_logs:
            if log.role == "staff":
                activity_count[log.performed_by] = activity_count.get(log.performed_by, 0) + 1
        
        most_active_staff = "None"
        max_act = 0
        for email, cnt in activity_count.items():
            if cnt > max_act:
                max_act = cnt
                u = db.query(User).filter(User.email == email).first()
                most_active_staff = u.full_name if u else email

        # 4. Recent activities panel (Requirement 5)
        recent_activities = []
        activities = db.query(MedicineAuditLog).order_by(MedicineAuditLog.timestamp.desc()).limit(15).all()
        for act in activities:
            recent_activities.append({
                "id": act.id,
                "action_type": act.action_type,
                "medicine_name": act.medicine_name,
                "performed_by": act.performed_by,
                "role": act.role,
                "timestamp": act.timestamp.isoformat(),
                "old_value": act.old_value,
                "new_value": act.new_value
            })

        # 5. Inventory Timeline (Requirement 11)
        timeline = []
        movements = db.query(StockMovement).order_by(StockMovement.created_at.desc()).limit(20).all()
        for mv in movements:
            med = db.query(Medicine).filter(Medicine.id == mv.medicine_id).first()
            med_name = med.name if med else f"Medicine #{mv.medicine_id}"
            timeline.append({
                "type": "movement",
                "action": mv.type,
                "medicine_name": med_name,
                "quantity": mv.quantity,
                "timestamp": mv.created_at.isoformat()
            })

        sales = db.query(SalesTransaction).order_by(SalesTransaction.transaction_date.desc()).limit(20).all()
        for s in sales:
            med = db.query(Medicine).filter(Medicine.id == s.medicine_id).first()
            med_name = med.name if med else f"Medicine #{s.medicine_id}"
            timeline.append({
                "type": "sale",
                "action": "SALE",
                "medicine_name": med_name,
                "quantity": s.quantity,
                "timestamp": s.transaction_date.isoformat(),
                "total": s.total
            })
        
        timeline = sorted(timeline, key=lambda x: x["timestamp"], reverse=True)[:30]

        # 6. Expiration panel detail
        expiration_panel = []
        for m in db.query(Medicine).order_by(Medicine.expiry.asc()).all():
            if m.expiry:
                days_left = (m.expiry - today).days
                config = get_inventory_alert_config(db, m.dosage_form)
                expiry_days = config.expiry_alert_days if config else 60
                status = "expired" if days_left < 0 else "critical" if days_left <= expiry_days else "healthy"
                expiration_panel.append({
                    "id": m.id,
                    "name": m.name,
                    "expiry": m.expiry.isoformat(),
                    "days_left": days_left,
                    "status": status,
                    "stock": m.stock,
                    "category": m.category
                })

        # 7. Low stock panel detail
        low_stock_panel = []
        for m in dashboard_medicines:
            config = get_inventory_alert_config(db, m.dosage_form)
            low_threshold = config.low_stock_threshold if config else (m.reorder_level or 10)
            if m.stock > low_threshold + 10:
                continue
            low_stock_panel.append({
                "id": m.id,
                "name": m.name,
                "stock": m.stock,
                "status": "critical" if m.stock <= low_threshold else "warning",
                "category": m.category
            })

        # 8. All medicines detail for admin
        all_medicines_with_meta = []
        for m in db.query(Medicine).all():
            added_user = db.query(User).filter(User.id == m.added_by).first()
            updated_user = db.query(User).filter(User.id == m.updated_by).first()
            assigned_user = db.query(User).filter(User.id == m.assigned_staff).first()

            all_medicines_with_meta.append({
                "id": m.id,
                "name": m.name,
                "category": m.category,
                "stock": m.stock,
                "expiry": m.expiry.isoformat() if m.expiry else None,
                "avg_daily_sales": m.avg_daily_sales,
                "unit_price": m.unit_price,
                "supplier": m.supplier,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "updated_at": m.updated_at.isoformat() if m.updated_at else None,
                "last_restocked_at": m.last_restocked_at.isoformat() if m.last_restocked_at else None,
                "added_by_name": added_user.full_name if added_user else "System",
                "added_by_email": added_user.email if added_user else "system@five-l",
                "updated_by_name": updated_user.full_name if updated_user else None,
                "assigned_staff_name": assigned_user.full_name if assigned_user else None,
                "assigned_staff_email": assigned_user.email if assigned_user else None,
            })

        return {
            "total_medicines": total_meds,
            "low_stock_count": len(low_stock_meds),
            "expired_count": len(expired_meds),
            "nearing_expiry_count": len(nearing_expiry_meds),
            "total_medicines_by_staff": total_medicines_by_staff,
            "most_active_staff": most_active_staff,
            "recent_activities": recent_activities,
            "timeline": timeline,
            "expiration_panel": expiration_panel,
            "low_stock_panel": low_stock_panel,
            "all_medicines_with_meta": all_medicines_with_meta
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch admin stats: {str(exc)}")

@app.get("/admin/alerts")
def get_admin_alerts(db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    alerts = db.query(AdminAlert).order_by(AdminAlert.created_at.desc()).limit(50).all()
    return [{
        "id": a.id,
        "alert_type": a.alert_type,
        "message": a.message,
        "created_at": a.created_at.isoformat(),
        "is_read": a.is_read
    } for a in alerts]

@app.put("/admin/alerts/{alert_id}/read")
def mark_alert_as_read(alert_id: int, db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    alert = db.query(AdminAlert).filter(AdminAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.is_read = True
    db.commit()
    return {"message": "Alert marked as read"}

@app.get("/medicine-audit-logs")
def get_medicine_audit_logs(db: Session = Depends(get_db), current_user: User = Depends(require_admin_user)):
    logs = db.query(MedicineAuditLog).order_by(MedicineAuditLog.timestamp.desc()).all()
    return [{
        "id": l.id,
        "action_type": l.action_type,
        "medicine_name": l.medicine_name,
        "performed_by": l.performed_by,
        "role": l.role,
        "timestamp": l.timestamp.isoformat(),
        "old_value": l.old_value,
        "new_value": l.new_value
    } for l in logs]

@app.delete("/audit-logs")
def delete_audit_logs(current_user: User = Depends(get_current_user)):
    raise HTTPException(
        status_code=403,
        detail="Compliance Violation: System security audit logs cannot be deleted under any circumstances."
    )

@app.delete("/medicine-audit-logs")
def delete_medicine_audit_logs(current_user: User = Depends(get_current_user)):
    raise HTTPException(
        status_code=403,
        detail="Compliance Violation: Medicine lifecycle audit logs cannot be deleted under any circumstances."
    )

# -------------------
# Prediction demo
# -------------------
@app.post("/predict", response_model=PredictResponse)
def predict(request: PredictRequest):
    keywords = {
        "antibiotic": ["amox", "penicillin", "cepha"],
        "pain": ["paracetamol", "ibuprofen", "aspirin"]
    }
    detected = [k for k, words in keywords.items() if any(w in request.label_text.lower() for w in words)]
    category = detected[0] if detected else "Unknown"
    return PredictResponse(
        predicted_category=category,
        confidence=0.85,
        explanations=["Keyword match"],
        detected_keywords=detected
    )

# -------------------
# ML Routes Integration
# -------------------
if ML_ENABLED:
    app.include_router(ml_router)

# Transactions router (optional)
try:
    from transactions import router as transactions_router
    app.include_router(transactions_router)
except Exception:
    print("[INFO] transactions router not available or failed to load")

# FIFO inventory router
try:
    from inventory_fifo import router as inventory_fifo_router
    app.include_router(inventory_fifo_router)
except Exception:
    print("[INFO] FIFO inventory router not available or failed to load")

# Reports router
try:
    from reports_routes import router as reports_router
    app.include_router(reports_router)
    print("[INFO] Reports router loaded successfully")
except Exception as e:
    print(f"[INFO] Reports router not available or failed to load: {e}")

# Centralized Senior Citizen/PWD customer records.
try:
    from customers import router as customers_router
    app.include_router(customers_router)
    print("[INFO] Customer records router loaded successfully")
except Exception as e:
    print(f"[INFO] Customer records router not available or failed to load: {e}")

# Settings router
try:
    from settings_routes import router as settings_router
    app.include_router(settings_router)
    print("[INFO] Settings router loaded successfully")
except Exception as e:
    print(f"[INFO] Settings router not available or failed to load: {e}")

# -------------------
# Run server
# -------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
