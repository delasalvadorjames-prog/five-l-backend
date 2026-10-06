"""
reports_routes.py — Five L Pharmacy Reports API
Provides 7 analytics endpoints backed by optimised MySQL queries.
"""
import os
from datetime import date, datetime, timedelta
from typing import List, Optional
from urllib.parse import quote_plus

from fastapi import APIRouter, Query
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# DB Setup (mirrors main.py pattern)
# ---------------------------------------------------------------------------

def _build_url() -> str:
    explicit = os.getenv("MYSQL_URL")
    if explicit:
        return explicit
    host     = os.getenv("DB_HOST", "127.0.0.1")
    port     = os.getenv("DB_PORT", "3306")
    user     = os.getenv("DB_USER", "root")
    pwd      = os.getenv("DB_PASSWORD", "")
    db_name  = os.getenv("DB_NAME", "five-l")
    auth     = f"{quote_plus(user)}:{quote_plus(pwd)}" if pwd else quote_plus(user)
    # PyMySQL is the MySQL driver installed by backend/requirements.txt.
    return f"mysql+pymysql://{auth}@{host}:{port}/{db_name}"


_engine       = create_engine(_build_url(), pool_pre_ping=True, pool_recycle=3600)
_SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

def _db():
    s = _SessionLocal()
    try:
        yield s
    finally:
        s.close()

# ---------------------------------------------------------------------------
router = APIRouter(prefix="/reports", tags=["reports"])
# ---------------------------------------------------------------------------

# ── helpers ──────────────────────────────────────────────────────────────────
def _build_txn_filters(
    date_from: Optional[str],
    date_to: Optional[str],
    month: Optional[int],
    year: Optional[int],
    category: Optional[str],
    payment_method: Optional[str],
    supplier: Optional[str],
    search: Optional[str],
    alias_t: str = "t",
    alias_m: str = "m",
    alias_ms: str = "ms"
) -> tuple[str, dict]:
    parts, params = [], {}
    if date_from:
        parts.append(f"DATE({alias_t}.created_at) >= :df")
        params["df"] = date_from
    if date_to:
        parts.append(f"DATE({alias_t}.created_at) <= :dt")
        params["dt"] = date_to
    if month:
        parts.append(f"MONTH({alias_t}.created_at) = :mo")
        params["mo"] = month
    if year:
        parts.append(f"YEAR({alias_t}.created_at) = :yr")
        params["yr"] = year
    if category:
        parts.append(f"{alias_m}.category = :cat")
        params["cat"] = category
    if payment_method:
        parts.append(f"{alias_t}.payment_method = :pm")
        params["pm"] = payment_method
    if supplier:
        parts.append(f"({alias_m}.supplier LIKE :sup OR {alias_ms}.supplier LIKE :sup)")
        params["sup"] = f"%{supplier}%"
    if search:
        parts.append(f"({alias_t}.medicine_name LIKE :search OR {alias_m}.medicine_name LIKE :search OR {alias_m}.name LIKE :search)")
        params["search"] = f"%{search}%"
        
    where = ("WHERE " + " AND ".join(parts)) if parts else ""
    return where, params


def _build_inv_filters(
    category: Optional[str],
    supplier: Optional[str],
    search: Optional[str],
    alias_m: str = "m",
    alias_ms: str = "ms"
) -> tuple[str, dict]:
    parts, params = [], {}
    if category:
        parts.append(f"{alias_m}.category = :cat")
        params["cat"] = category
    if supplier:
        parts.append(f"({alias_m}.supplier LIKE :sup OR {alias_ms}.supplier LIKE :sup)")
        params["sup"] = f"%{supplier}%"
    if search:
        parts.append(f"({alias_m}.name LIKE :search OR {alias_m}.medicine_name LIKE :search)")
        params["search"] = f"%{search}%"
    where = ("WHERE " + " AND ".join(parts)) if parts else ""
    return where, params


def _build_purchase_filters(
    date_from: Optional[str],
    date_to: Optional[str],
    month: Optional[int],
    year: Optional[int],
    category: Optional[str],
    supplier: Optional[str],
    alias_ms: str = "ms",
    alias_m: str = "m"
) -> tuple[str, dict]:
    parts, params = [], {}
    if date_from:
        parts.append(f"DATE({alias_ms}.created_at) >= :df")
        params["df"] = date_from
    if date_to:
        parts.append(f"DATE({alias_ms}.created_at) <= :dt")
        params["dt"] = date_to
    if month:
        parts.append(f"MONTH({alias_ms}.created_at) = :mo")
        params["mo"] = month
    if year:
        parts.append(f"YEAR({alias_ms}.created_at) = :yr")
        params["yr"] = year
    if category:
        parts.append(f"{alias_m}.category = :cat")
        params["cat"] = category
    if supplier:
        parts.append(f"{alias_ms}.supplier LIKE :sup")
        params["sup"] = f"%{supplier}%"
    where = ("WHERE " + " AND ".join(parts)) if parts else ""
    return where, params


@router.get("/bir")
def get_bir_report(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    supplier: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    customer_type: Optional[str] = Query(None),
    vat_type: Optional[str] = Query(None),
):
    """Return BIR figures from the transaction-level tax fields saved by POS."""
    db = _SessionLocal()
    try:
        where, params = _build_txn_filters(date_from, date_to, month, year, category, payment_method, supplier, search)
        extra = []
        if customer_type:
            extra.append("t.customer_type = :customer_type")
            params["customer_type"] = customer_type
        if vat_type == "vatable":
            extra.append("CASE WHEN t.customer_type IN ('senior_citizen', 'pwd') THEN COALESCE(t.vatable_sales, 0) ELSE COALESCE(t.vatable_sales, t.total_amount / 1.12) END > 0")
        elif vat_type == "vat_exempt":
            extra.append("CASE WHEN t.customer_type IN ('senior_citizen', 'pwd') THEN COALESCE(t.vat_exempt_sales, 0) ELSE COALESCE(t.vat_exempt_sales, 0) END > 0")
        if extra:
            where = f"{where} {'AND' if where else 'WHERE'} {' AND '.join(extra)}"

        rows = db.execute(text(f"""
              SELECT t.transaction_number, t.created_at, COALESCE(t.customer_type, 'unknown'),
                    COALESCE(NULLIF(t.gross_amount, 0), t.total_amount),
                    CASE WHEN t.customer_type IN ('senior_citizen', 'pwd') THEN COALESCE(t.vatable_sales, 0)
                        ELSE COALESCE(t.vatable_sales, t.total_amount / 1.12) END,
                    CASE WHEN t.customer_type IN ('senior_citizen', 'pwd') THEN COALESCE(t.vat_amount, 0)
                        ELSE COALESCE(t.vat_amount, t.total_amount - (t.total_amount / 1.12)) END,
                    CASE WHEN t.customer_type IN ('senior_citizen', 'pwd')
                        THEN COALESCE(t.vat_exempt_sales, t.total_amount / 1.12)
                        ELSE COALESCE(t.vat_exempt_sales, 0) END,
                    COALESCE(t.discount_amount, 0), COALESCE(NULLIF(t.net_sales, 0), t.total_amount),
                   COALESCE(t.payment_method, 'unknown')
            FROM transactions t
            LEFT JOIN medicines m ON t.medicine_id = m.id
            LEFT JOIN (SELECT medicine_id, MAX(supplier) AS supplier FROM medicine_supplies GROUP BY medicine_id) ms
              ON ms.medicine_id = m.id
            {where}
            ORDER BY t.created_at DESC, t.id DESC
        """), params).fetchall()

        def amount(value):
            return float(value) if value is not None else None

        transactions = [{
            "receipt_invoice_no": row[0], "date": row[1].isoformat() if row[1] else None,
            "customer_type": row[2], "gross_amount": amount(row[3]), "vatable_sales": amount(row[4]),
            "vat_amount": amount(row[5]), "vat_exempt_sales": amount(row[6]), "discount": amount(row[7]),
            "net_sales": amount(row[8]), "payment_method": row[9],
        } for row in rows]

        def total(index):
            return sum((row[index] or 0) for row in rows if row[index] is not None)

        return {
            "gross_sales": total(3), "vatable_sales": total(4), "vat_amount": total(5),
            "vat_exempt_sales": total(6),
            "senior_citizen_discount": sum((row[7] or 0) for row in rows if row[2] == 'senior_citizen'),
            "pwd_discount": sum((row[7] or 0) for row in rows if row[2] == 'pwd'),
            "other_discounts": sum((row[7] or 0) for row in rows if row[2] not in ('senior_citizen', 'pwd')),
            "net_sales": total(8), "total_transactions": len(transactions), "transactions": transactions,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 1. Dashboard summary
# ---------------------------------------------------------------------------
@router.get("/dashboard")
def get_dashboard(
    date_from: Optional[str]  = Query(None),
    date_to:   Optional[str]  = Query(None),
    month:     Optional[int]  = Query(None),
    year:      Optional[int]  = Query(None),
    category:  Optional[str]  = Query(None),
    payment_method: Optional[str] = Query(None),
    supplier:  Optional[str]  = Query(None),
    search:    Optional[str]  = Query(None),
):
    db = _SessionLocal()
    try:
        where_txn, params = _build_txn_filters(date_from, date_to, month, year, category, payment_method, supplier, search)

        # revenue & transactions
        txn_sql = f"""
            SELECT
                COALESCE(SUM(t.total_amount), 0) AS total_revenue,
                COUNT(*)                          AS total_transactions,
                COALESCE(SUM(t.quantity), 0)      AS total_products_sold
            FROM transactions t
            LEFT JOIN medicines m ON t.medicine_id = m.id
            LEFT JOIN (
                SELECT medicine_id, MAX(supplier) AS supplier
                FROM medicine_supplies
                GROUP BY medicine_id
            ) ms ON ms.medicine_id = m.id
            {where_txn}
        """
        row = db.execute(text(txn_sql), params).fetchone()
        total_revenue      = float(row[0] or 0)
        total_transactions = int(row[1] or 0)
        total_products     = int(row[2] or 0)

        # inventory counts
        today = date.today().isoformat()
        sixty_days = (date.today() + timedelta(days=60)).isoformat()

        where_inv, inv_params = _build_inv_filters(category, supplier, search)
        inv_params.update({"today": today, "sixty": sixty_days})

        inv_sql = f"""
            SELECT
                SUM(CASE WHEN m.stock <= 0 THEN 1 ELSE 0 END)                        AS out_of_stock,
                SUM(CASE WHEN m.stock > 0 AND m.stock <= m.reorder_level THEN 1 ELSE 0 END) AS low_stock,
                SUM(CASE WHEN me.expiry IS NOT NULL AND me.expiry <= :sixty AND me.expiry >= :today THEN 1 ELSE 0 END) AS expiring
            FROM medicines m
            LEFT JOIN (
                SELECT medicine_id, MIN(expiry_date) AS expiry
                FROM medicine_supplies
                WHERE is_archived = 0 AND quantity > 0
                GROUP BY medicine_id
            ) me ON me.medicine_id = m.id
            LEFT JOIN (
                SELECT medicine_id, MAX(supplier) AS supplier
                FROM medicine_supplies
                GROUP BY medicine_id
            ) ms ON ms.medicine_id = m.id
            {where_inv}
        """
        inv = db.execute(text(inv_sql), inv_params).fetchone()

        return {
            "total_revenue":      total_revenue,
            "total_transactions": total_transactions,
            "total_products_sold": total_products,
            "out_of_stock":       int(inv[0] or 0),
            "low_stock":          int(inv[1] or 0),
            "expiring_medicines": int(inv[2] or 0),
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. Sales report (monthly grouping by default)
# ---------------------------------------------------------------------------
@router.get("/sales")
def get_sales(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    month:     Optional[int] = Query(None),
    year:      Optional[int] = Query(None),
    category:  Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    supplier:  Optional[str] = Query(None),
    search:    Optional[str] = Query(None),
    group_by:  Optional[str] = Query(None),
):
    db = _SessionLocal()
    try:
        where, params = _build_txn_filters(date_from, date_to, month, year, category, payment_method, supplier, search)

        period_expression = (
            "DATE_FORMAT(t.created_at, '%Y-%m-%d')"
            if group_by == "day"
            else "DATE_FORMAT(t.created_at, '%Y-%m')"
        )

        rows = db.execute(text(f"""
            SELECT
                {period_expression} AS period,
                COALESCE(SUM(t.total_amount), 0)   AS revenue,
                COUNT(*)                            AS transactions,
                COALESCE(SUM(t.quantity), 0)        AS units
            FROM transactions t
            LEFT JOIN medicines m ON t.medicine_id = m.id
            LEFT JOIN (
                SELECT medicine_id, MAX(supplier) AS supplier
                FROM medicine_supplies
                GROUP BY medicine_id
            ) ms ON ms.medicine_id = m.id
            {where}
            GROUP BY period
            ORDER BY period ASC
        """), params).fetchall()

        # also return category breakdown for the same period
        cat_rows = db.execute(text(f"""
            SELECT
                COALESCE(m.category, 'Other')      AS category,
                COALESCE(SUM(t.total_amount), 0)   AS revenue,
                COALESCE(SUM(t.quantity), 0)        AS units
            FROM transactions t
            LEFT JOIN medicines m ON t.medicine_id = m.id
            LEFT JOIN (
                SELECT medicine_id, MAX(supplier) AS supplier
                FROM medicine_supplies
                GROUP BY medicine_id
            ) ms ON ms.medicine_id = m.id
            {where}
            GROUP BY category
            ORDER BY revenue DESC
        """), params).fetchall()

        return {
            "monthly": [
                {"period": r[0], "revenue": float(r[1]), "transactions": int(r[2]), "units": int(r[3])}
                for r in rows
            ],
            "by_category": [
                {"category": r[0], "revenue": float(r[1]), "units": int(r[2])}
                for r in cat_rows
            ],
        }
    finally:
        db.close()


# ─── Inventory status
# ---------------------------------------------------------------------------
@router.get("/inventory")
def get_inventory(
    category:  Optional[str] = Query(None),
    supplier:  Optional[str] = Query(None),
    search:    Optional[str] = Query(None),
    critical_stock_threshold: int = Query(5, ge=1),
    low_stock_threshold: int = Query(20, ge=1),
    expiry_alert_days: int = Query(30, ge=1),
    non_medicine_critical_stock_threshold: int = Query(5, ge=1),
    non_medicine_low_stock_threshold: int = Query(10, ge=1),
    non_medicine_expiry_alert_days: int = Query(30, ge=1),
    include_expired: bool = Query(False),
):
    db = _SessionLocal()
    try:
        where, params = _build_inv_filters(category, supplier, search)
        today = date.today().isoformat()
        expiry_cutoff = (date.today() + timedelta(days=expiry_alert_days)).isoformat()
        non_medicine_expiry_cutoff = (date.today() + timedelta(days=non_medicine_expiry_alert_days)).isoformat()
        params.update({
            "today": today,
            "expiry_cutoff": expiry_cutoff,
            "critical_stock_threshold": critical_stock_threshold,
            "low_stock_threshold": low_stock_threshold,
            "non_medicine_critical_stock_threshold": non_medicine_critical_stock_threshold,
            "non_medicine_low_stock_threshold": non_medicine_low_stock_threshold,
            "non_medicine_expiry_cutoff": non_medicine_expiry_cutoff,
        })
        # Match the Inventory screen: available stock comes from active supply
        # batches, excluding archived/empty batches and batches within the
        # 30-day not-for-sale window. Do not rely on the legacy medicines.stock
        # column, which can remain zero after stock is moved into supplies.
        stock_scope = (
            "(COALESCE(inv.available_stock, 0) > 0 OR "
            "(me.expiry IS NOT NULL AND DATE(me.expiry) < :today))"
            if include_expired
            else "COALESCE(inv.available_stock, 0) > 0"
        )
        where = f"WHERE {stock_scope}" if not where else where.replace(
            "WHERE ", f"WHERE {stock_scope} AND ", 1
        )

        rows = db.execute(text(f"""
            SELECT
                m.id,
                COALESCE(m.medicine_name, m.name)                                    AS medicine_name,
                m.volume,
                m.strength,
                m.dosage_form,
                m.product_code,
                m.category,
                COALESCE(inv.available_stock, 0)                                  AS stock,
                COALESCE(m.reorder_level, 0)                                         AS reorder_level,
                me.expiry,
                m.unit_price,
                COALESCE(m.supplier, ms.supplier)                                    AS supplier,
                CASE
                    WHEN me.expiry IS NOT NULL AND DATE(me.expiry) < :today    THEN 'Expired'
                    WHEN COALESCE(inv.available_stock, 0) <= CASE WHEN LOWER(COALESCE(m.dosage_form, '')) IN ('other', 'others', 'medical supply', 'medical device') THEN :non_medicine_critical_stock_threshold ELSE :critical_stock_threshold END THEN 'Critical Stock'
                    WHEN COALESCE(inv.available_stock, 0) <= CASE WHEN LOWER(COALESCE(m.dosage_form, '')) IN ('other', 'others', 'medical supply', 'medical device') THEN :non_medicine_low_stock_threshold ELSE :low_stock_threshold END THEN 'Low Stock'
                    WHEN me.expiry IS NOT NULL AND DATE(me.expiry) <= CASE WHEN LOWER(COALESCE(m.dosage_form, '')) IN ('other', 'others', 'medical supply', 'medical device') THEN :non_medicine_expiry_cutoff ELSE :expiry_cutoff END THEN 'Expiring Soon'
                    ELSE 'Healthy'
                END                                                                  AS status,
                DATEDIFF(me.expiry, CURDATE())                                      AS days_left
            FROM medicines m
            LEFT JOIN (
                SELECT medicine_id, MIN(expiry_date) AS expiry
                FROM medicine_supplies
                WHERE quantity > 0
                GROUP BY medicine_id
            ) me ON me.medicine_id = m.id
            LEFT JOIN (
                SELECT
                    medicine_id,
                    SUM(
                        CASE
                            WHEN is_archived = 0
                                AND quantity > 0
                                AND (expiry_date IS NULL OR expiry_date > DATE_ADD(CURDATE(), INTERVAL 30 DAY))
                            THEN quantity
                            ELSE 0
                        END
                    ) AS available_stock
                FROM medicine_supplies
                GROUP BY medicine_id
            ) inv ON inv.medicine_id = m.id
            LEFT JOIN (
                SELECT medicine_id, MAX(supplier) AS supplier
                FROM medicine_supplies
                GROUP BY medicine_id
            ) ms ON ms.medicine_id = m.id
            {where}
            ORDER BY COALESCE(inv.available_stock, 0) ASC, me.expiry ASC
        """), params).fetchall()

        return [
            {
                "id": r[0],
                "medicine_name": r[1],
                "volume": r[2],
                "strength": r[3],
                "dosage_form": r[4],
                "product_code": r[5],
                "category": r[6],
                "stock": int(r[7] or 0),
                "reorder_level": int(r[8] or 0),
                "expiry": str(r[9]) if r[9] else None,
                "unit_price": float(r[10] or 0),
                "supplier": r[11],
                "status": r[12],
                "days_left": int(r[13]) if r[13] is not None else None,
                "stock_value": float(r[7] or 0) * float(r[10] or 0),
            }
            for r in rows
        ]
    finally:
        db.close()


@router.get("/inventory-adjustments")
def get_inventory_adjustments(
    adjustment_type: str = Query(..., pattern="^(damages|expired|count_correction)$"),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    month: Optional[int] = Query(None, ge=1, le=12),
    year: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    supplier: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
):
    """Return inventory adjustment requests/history for the selected adjustment type."""
    db = _SessionLocal()
    try:
        # PriceHistory is the source of truth for staff-submitted adjustments.
        # The old audit-log text search missed pending requests and used different
        # labels for approved adjustments.
        clauses = ["ph.adjustment_type = :adjustment_type"]
        params = {"adjustment_type": adjustment_type}
        if date_from:
            clauses.append("DATE(COALESCE(ph.approved_at, ph.created_at)) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            clauses.append("DATE(COALESCE(ph.approved_at, ph.created_at)) <= :date_to")
            params["date_to"] = date_to
        if month:
            clauses.append("MONTH(COALESCE(ph.approved_at, ph.created_at)) = :month")
            params["month"] = month
        if year:
            clauses.append("YEAR(COALESCE(ph.approved_at, ph.created_at)) = :year")
            params["year"] = year
        if category:
            clauses.append("m.category = :category")
            params["category"] = category
        if supplier:
            clauses.append("s.supplier = :supplier")
            params["supplier"] = supplier
        if search:
            clauses.append("(m.name LIKE :search OR m.medicine_name LIKE :search OR s.batch_number LIKE :search)")
            params["search"] = f"%{search}%"

        rows = db.execute(text(f"""
            SELECT ph.id,
                   COALESCE(m.medicine_name, m.name, CONCAT('Supply #', ph.inventory_id)),
                   COALESCE(u.full_name, ph.updated_by, 'Staff'),
                   COALESCE(ph.approved_at, ph.created_at),
                   ph.old_quantity, ph.new_quantity, ph.status, ph.reason, ph.remarks,
                   COALESCE(m.category, 'Uncategorized'),
                   COALESCE(s.selling_price, 0)
            FROM price_history ph
            LEFT JOIN medicine_supplies s ON s.id = ph.inventory_id
            LEFT JOIN medicines m ON m.id = s.medicine_id
            LEFT JOIN users u ON LOWER(u.email) = LOWER(ph.updated_by)
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(ph.approved_at, ph.created_at) DESC
        """), params).fetchall()

        return [
            {
                "id": row[0],
                "medicine_name": row[1],
                "category": row[9],
                "stock": 0,
                "reorder_level": 0,
                "expiry": str(row[3]) if row[3] else None,
                "unit_price": float(row[10] or 0),
                "supplier": row[2],
                "status": adjustment_type,
                "days_left": None,
                "stock_value": 0,
                "adjustment_type": adjustment_type,
                "adjusted_by": row[2],
                "adjusted_at": str(row[3]) if row[3] else None,
                "details": f"Qty: {row[4] if row[4] is not None else '—'} → {row[5] if row[5] is not None else '—'}\nStatus: {row[6]}\nReason: {row[7] or '—'}"
                    + (f"\nRemarks: {row[8]}" if row[8] else ''),
                "adjustment_status": row[6],
            }
            for row in rows
        ]
    finally:
        db.close()


@router.get("/stock-movements")
@router.get("/inventory-movements")
def get_stock_movements(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    month: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    category: Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    supplier: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    movement_type: Optional[str] = Query(None),
):
    """Return inventory deductions from POS sales and manual stock-out logs."""
    db = _SessionLocal()
    try:
        movement_clauses, transaction_clauses, params = ["sm.type = 'STOCK_OUT'"], [], {}
        if date_from:
            movement_clauses.append("DATE(sm.created_at) >= :date_from")
            transaction_clauses.append("DATE(t.created_at) >= :date_from")
            params["date_from"] = date_from
        if date_to:
            movement_clauses.append("DATE(sm.created_at) <= :date_to")
            transaction_clauses.append("DATE(t.created_at) <= :date_to")
            params["date_to"] = date_to
        if month:
            movement_clauses.append("MONTH(sm.created_at) = :month")
            transaction_clauses.append("MONTH(t.created_at) = :month")
            params["month"] = month
        if year:
            movement_clauses.append("YEAR(sm.created_at) = :year")
            transaction_clauses.append("YEAR(t.created_at) = :year")
            params["year"] = year
        if category:
            movement_clauses.append("m.category = :category")
            transaction_clauses.append("m.category = :category")
            params["category"] = category
        if payment_method:
            # Manual stock adjustments have no payment method; only POS
            # deductions can match this filter.
            movement_clauses.append("1 = 0")
            transaction_clauses.append("t.payment_method = :payment_method")
            params["payment_method"] = payment_method
        if supplier:
            movement_clauses.append("(m.supplier LIKE :supplier OR ms.supplier LIKE :supplier)")
            transaction_clauses.append("(m.supplier LIKE :supplier OR ms.supplier LIKE :supplier)")
            params["supplier"] = f"%{supplier}%"
        if search:
            movement_clauses.append("(m.name LIKE :search OR m.medicine_name LIKE :search)")
            transaction_clauses.append("(m.name LIKE :search OR m.medicine_name LIKE :search OR t.medicine_name LIKE :search)")
            params["search"] = f"%{search}%"
        if movement_type:
            # The report currently requests STOCK_OUT. Keep the parameter
            # supported for future movement-type filters.
            movement_clauses.append("sm.type = :movement_type")
            transaction_clauses.append(":movement_type = 'STOCK_OUT'")
            params["movement_type"] = movement_type
        movement_where = " AND ".join(movement_clauses)
        transaction_where = " AND ".join(transaction_clauses) if transaction_clauses else "1=1"

        rows = db.execute(text(f"""
            SELECT movement_rows.medicine_id, movement_rows.medicine_name,
                   movement_rows.category, COALESCE(current_m.stock, 0) AS remaining_quantity,
                   SUM(movement_rows.quantity) AS deducted_quantity,
                   COUNT(*) AS movement_count, MAX(movement_rows.created_at) AS last_deducted_at
            FROM (
                SELECT sm.medicine_id,
                       COALESCE(m.name, m.medicine_name, 'Unknown Medicine') AS medicine_name,
                       COALESCE(m.category, 'Other') AS category,
                       sm.quantity, sm.created_at
                FROM stock_movements sm
                LEFT JOIN medicines m ON m.id = sm.medicine_id
                LEFT JOIN (
                    SELECT medicine_id, MAX(supplier) AS supplier
                    FROM medicine_supplies GROUP BY medicine_id
                ) ms ON ms.medicine_id = m.id
                WHERE {movement_where}
                UNION ALL
                SELECT t.medicine_id,
                       COALESCE(m.name, m.medicine_name, t.medicine_name, 'Unknown Medicine'),
                       COALESCE(m.category, 'Other'), t.quantity, t.created_at
                FROM transactions t
                LEFT JOIN medicines m ON m.id = t.medicine_id
                LEFT JOIN (
                    SELECT medicine_id, MAX(supplier) AS supplier
                    FROM medicine_supplies GROUP BY medicine_id
                ) ms ON ms.medicine_id = m.id
                WHERE {transaction_where}
            ) AS movement_rows
            LEFT JOIN medicines current_m ON current_m.id = movement_rows.medicine_id
            GROUP BY movement_rows.medicine_id, movement_rows.medicine_name,
                     movement_rows.category, current_m.stock
            ORDER BY last_deducted_at DESC, movement_rows.medicine_name ASC
        """), params).fetchall()

        return [{
            "id": int(row[0]) if row[0] is not None else index,
            "medicine_id": int(row[0]) if row[0] is not None else None,
            "medicine_name": row[1], "category": row[2],
            "remaining_quantity": int(row[3] or 0),
            "deducted_quantity": int(row[4] or 0),
            "movement_count": int(row[5] or 0),
            "last_deducted_at": row[6].isoformat() if row[6] else None,
        } for index, row in enumerate(rows)]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 4. Top-selling products
# ---------------------------------------------------------------------------
@router.get("/top-products")
def get_top_products(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    month:     Optional[int] = Query(None),
    year:      Optional[int] = Query(None),
    category:  Optional[str] = Query(None),
    payment_method: Optional[str] = Query(None),
    supplier:  Optional[str] = Query(None),
    search:    Optional[str] = Query(None),
    limit:     int           = Query(10, ge=1, le=50),
):
    db = _SessionLocal()
    try:
        where, params = _build_txn_filters(date_from, date_to, month, year, category, payment_method, supplier, search)
        params["lim"] = limit

        rows = db.execute(text(f"""
            SELECT
                t.medicine_id,
                COALESCE(m.medicine_name, t.medicine_name, 'Unknown')  AS medicine_name,
                COALESCE(m.category, 'Other')                           AS category,
                COALESCE(SUM(t.quantity), 0)                            AS total_units,
                COALESCE(SUM(t.total_amount), 0)                        AS total_revenue,
                COALESCE(AVG(t.price), 0)                               AS avg_price
            FROM transactions t
            LEFT JOIN medicines m ON t.medicine_id = m.id
            LEFT JOIN (
                SELECT medicine_id, MAX(supplier) AS supplier
                FROM medicine_supplies
                GROUP BY medicine_id
            ) ms ON ms.medicine_id = m.id
            {where}
            GROUP BY t.medicine_id, medicine_name, category
            ORDER BY total_revenue DESC
            LIMIT :lim
        """), params).fetchall()

        return [
            {
                "medicine_id": r[0],
                "medicine_name": r[1],
                "category": r[2],
                "total_units": int(r[3]),
                "total_revenue": float(r[4]),
                "avg_price": float(r[5]),
            }
            for r in rows
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 5. Payment method distribution
# ---------------------------------------------------------------------------
@router.get("/payment-methods")
def get_payment_methods(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    month:     Optional[int] = Query(None),
    year:      Optional[int] = Query(None),
    category:  Optional[str] = Query(None),
    supplier:  Optional[str] = Query(None),
    search:    Optional[str] = Query(None),
):
    db = _SessionLocal()
    try:
        where, params = _build_txn_filters(date_from, date_to, month, year, category, None, supplier, search)

        rows = db.execute(text(f"""
            SELECT
                COALESCE(t.payment_method, 'cash') AS method,
                COUNT(*)                          AS txn_count,
                COALESCE(SUM(t.total_amount), 0)    AS revenue
            FROM transactions t
            LEFT JOIN medicines m ON t.medicine_id = m.id
            LEFT JOIN (
                SELECT medicine_id, MAX(supplier) AS supplier
                FROM medicine_supplies
                GROUP BY medicine_id
            ) ms ON ms.medicine_id = m.id
            {where}
            GROUP BY method
            ORDER BY revenue DESC
        """), params).fetchall()

        return [
            {"method": r[0], "txn_count": int(r[1]), "revenue": float(r[2])}
            for r in rows
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 6. Purchase trend (from medicine_supplies)
# ---------------------------------------------------------------------------
@router.get("/purchases")
def get_purchases(
    date_from: Optional[str] = Query(None),
    date_to:   Optional[str] = Query(None),
    month:     Optional[int] = Query(None),
    year:      Optional[int] = Query(None),
    category:  Optional[str] = Query(None),
    supplier:  Optional[str] = Query(None),
):
    db = _SessionLocal()
    try:
        where, params = _build_purchase_filters(date_from, date_to, month, year, category, supplier)

        rows = db.execute(text(f"""
            SELECT
                DATE_FORMAT(ms.created_at, '%Y-%m')                   AS period,
                COALESCE(SUM(ms.quantity), 0)                          AS total_units,
                COALESCE(SUM(ms.quantity * ms.unit_cost), 0)           AS total_cost,
                COUNT(DISTINCT ms.id)                                  AS batch_count
            FROM medicine_supplies ms
            LEFT JOIN medicines m ON ms.medicine_id = m.id
            {where}
            GROUP BY period
            ORDER BY period ASC
        """), params).fetchall()

        return [
            {"period": r[0], "total_units": int(r[1]), "total_cost": float(r[2]), "batch_count": int(r[3])}
            for r in rows
        ]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 7. Sales forecast (3-month rolling projection)
# ---------------------------------------------------------------------------
@router.get("/forecast")
def get_forecast(
    category: Optional[str] = Query(None),
    months_ahead: int       = Query(3, ge=1, le=12),
):
    db = _SessionLocal()
    try:
        params: dict = {}
        cat_join  = ""
        cat_where = ""
        if category:
            cat_join  = "LEFT JOIN medicines m ON t.medicine_id = m.id"
            cat_where = "AND m.category = :cat"
            params["cat"] = category

        # past 12 months actual data
        rows = db.execute(text(f"""
            SELECT
                DATE_FORMAT(t.created_at, '%Y-%m')  AS period,
                COALESCE(SUM(t.total_amount), 0)     AS revenue
            FROM transactions t
            {cat_join}
            WHERE t.created_at >= DATE_SUB(NOW(), INTERVAL 12 MONTH)
            {cat_where}
            GROUP BY period
            ORDER BY period ASC
        """), params).fetchall()

        actuals = [{"period": r[0], "revenue": float(r[1]), "type": "actual"} for r in rows]

        # simple moving average for forecast
        revenues = [a["revenue"] for a in actuals]
        avg = sum(revenues[-3:]) / max(len(revenues[-3:]), 1) if revenues else 0
        growth = 1.05  # 5% monthly growth assumption

        last_period_str = actuals[-1]["period"] if actuals else datetime.now().strftime("%Y-%m")
        last_period = datetime.strptime(last_period_str, "%Y-%m")

        forecast = []
        for i in range(1, months_ahead + 1):
            future_dt  = (last_period.replace(day=1) + timedelta(days=31 * i)).replace(day=1)
            future_rev = avg * (growth ** i)
            forecast.append({
                "period":  future_dt.strftime("%Y-%m"),
                "revenue": round(future_rev, 2),
                "type":    "forecast",
            })

        return {"actuals": actuals, "forecast": forecast}
    finally:
        db.close()
