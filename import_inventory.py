"""
import_inventory.py
-------------------
Reads Final_Inventory_with_Categories (2).csv and inserts every valid product
row into the medicines table.

Skips:
  - The header row
  - Rows where Product name is empty
  - The "Assigned Category" legend rows (columns 8-11)

Uses the CSV's own Category column.
Sets sensible defaults for fields the CSV doesn't supply:
  - reorder_level : 10
  - expiry        : 2027-12-31  (placeholder – update per product later)
  - avg_daily_sales: 1.0
  - supplier      : None
"""

import argparse
import csv
import os
import sys
from datetime import date
from pathlib import Path

# ── parse arguments ──────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description='Import inventory data from CSV')
parser.add_argument('--file', default=None, help='Path to dataset.csv or dateset.csv')
parser.add_argument('--email', default='delasalvadorjames@gmail.com', help='Email to set as added_by')
parser.add_argument('--user-id', type=int, default=None, help='User ID to set as added_by')
parser.add_argument('--replace', action='store_true', help='Delete current inventory rows before importing')
args = parser.parse_args()
added_by_email = args.email
added_by_id = args.user_id

# ── locate the CSV ──────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
CSV_DIR = SCRIPT_DIR.parent / "five-l" / "src" / "file "
if args.file:
    CSV_PATH = Path(args.file).expanduser().resolve()
else:
    csv_candidates = [
        CSV_DIR / "dateset.csv",
        CSV_DIR / "dataset.csv",
    ]
    CSV_PATH = next((path for path in csv_candidates if path.exists()), csv_candidates[-1])

if not CSV_PATH.exists():
    print(f"[ERROR] CSV not found at: {CSV_PATH}")
    sys.exit(1)

# ── database connection (reuse env from backend) ────────────────────────────
def load_local_env():
    env_path = SCRIPT_DIR / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip("'\"")
        os.environ[key] = value

load_local_env()

import pymysql
pymysql.install_as_MySQLdb()

from urllib.parse import quote_plus
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

host     = os.getenv("DB_HOST", "127.0.0.1")
port     = os.getenv("DB_PORT", "3306")
user     = os.getenv("DB_USER", "root")
password = os.getenv("DB_PASSWORD", "")
db_name  = os.getenv("DB_NAME", "five-l")

user_part     = quote_plus(user or "root")
password_part = quote_plus(password) if password else ""
auth          = f"{user_part}:{password_part}" if password_part else user_part
MYSQL_URL     = f"mysql://{auth}@{host}:{port}/{db_name}"

engine       = create_engine(MYSQL_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_or_create_user_id(email: str):
    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT id FROM users WHERE email = :email LIMIT 1"),
            {"email": email},
        ).first()
        if result:
            return result[0]

        db.execute(
            text(
                "INSERT INTO users (full_name, email, role) VALUES (:full_name, :email, 'staff')"
            ),
            {
                "full_name": email.split('@')[0],
                "email": email,
            },
        )
        db.commit()
        result = db.execute(
            text("SELECT id FROM users WHERE email = :email LIMIT 1"),
            {"email": email},
        ).first()
        return result[0] if result else None
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

# ── defaults ────────────────────────────────────────────────────────────────
DEFAULT_REORDER_LEVEL  = 10
DEFAULT_EXPIRY         = date(2027, 12, 31)
DEFAULT_AVG_DAILY_SALES = 1.0


def infer_dosage_form(product_name: str) -> str:
    """Map the product label to the POS dosage-form buttons."""
    label = (product_name or '').upper()
    if any(token in label.split() for token in ('TAB', 'TABLET', 'PILL', 'PILLS')):
        return 'Tablet'
    if any(token in label.split() for token in ('CAP', 'CAPS', 'CAPSULE')):
        return 'Capsule'
    if any(token in label.split() for token in ('SYR', 'SYP', 'SYRUP')):
        return 'Syrup'
    if any(token in label.split() for token in ('SUSP', 'SUSPENSION')):
        return 'Suspension'
    if any(token in label.split() for token in ('DROP', 'DROPS')):
        return 'Drops'
    if any(token in label.split() for token in ('AMP', 'VIAL', 'INJECTION', 'INJECTABLE')):
        return 'Injectable'
    if 'CREAM' in label:
        return 'Cream'
    if 'OINTMENT' in label or 'OITMENT' in label:
        return 'Ointment'
    return 'Other'


def infer_unit_type(dosage_form: str, unit: str) -> str:
    value = (unit or '').lower()
    if dosage_form in ('Tablet', 'Capsule'):
        return 'pcs'
    if dosage_form in ('Syrup', 'Suspension', 'Drops'):
        return 'bottle'
    if dosage_form == 'Injectable':
        return 'vial' if 'vial' in value else 'ampule'
    if dosage_form in ('Cream', 'Ointment'):
        return 'tube'
    for marker, normalized in (('box', 'box'), ('pack', 'pack'), ('bottle', 'bottle'), ('tube', 'tube'), ('vial', 'vial')):
        if marker in value:
            return normalized
    if any(marker in value for marker in ('pc', 'tablet', 'cap')):
        return 'pcs'
    return 'unit'

# ── resolve user email/id ────────────────────────────────────────────────────
if added_by_id is None:
    added_by_id = get_or_create_user_id(added_by_email)
    if added_by_id is None:
        print(f"[ERROR] Unable to resolve user ID for email: {added_by_email}")
        sys.exit(1)

# ── parse & insert ──────────────────────────────────────────────────────────
inserted = 0
skipped  = 0
errors   = 0

db = SessionLocal()
try:
    if args.replace:
        print("[INFO] Replacing current medicines inventory before import")
        db.execute(text("DELETE FROM medicine_supplies"))
        db.execute(text("DELETE FROM stock_movements"))
        db.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        db.execute(text("DELETE FROM medicines"))
        db.execute(text("ALTER TABLE medicines AUTO_INCREMENT = 1"))
        db.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    with open(CSV_PATH, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader, [])
        header_map = {name.strip().lower(): index for index, name in enumerate(header)}
        for row_num, row in enumerate(reader, start=1):
            # skip blank rows
            if not any(cell.strip() for cell in row):
                skipped += 1
                continue

            # row[0] = Product, row[1] = Category, row[2] = Price,
            # row[3] = Quantity_in_stocks, row[4] = Unit_of_measurement, row[5] = Total_cost
            if len(row) < 4:
                skipped += 1
                continue

            product_name = row[0].strip()
            category     = row[1].strip() if len(row) > 1 else ""
            price_raw    = row[2].strip() if len(row) > 2 else ""
            qty_raw      = row[3].strip() if len(row) > 3 else ""
            volume       = row[4].strip() if len(row) > 4 else None
            dosage_form  = row[header_map['dosage_form']].strip() if 'dosage_form' in header_map and len(row) > header_map['dosage_form'] else ''
            classification = row[header_map['classification']].strip() if 'classification' in header_map and len(row) > header_map['classification'] else ''
            unit_type    = row[header_map['unit_type']].strip() if 'unit_type' in header_map and len(row) > header_map['unit_type'] else ''

            # skip header row and legend/empty rows
            if not product_name or product_name.lower() in ("product", ""):
                skipped += 1
                continue

            # skip the "e.g., kilos..." unit-of-measurement example row
            if product_name.lower().startswith("e.g"):
                skipped += 1
                continue

            # parse numeric fields
            try:
                unit_price = float(price_raw) if price_raw else 0.0
            except ValueError:
                unit_price = 0.0

            try:
                stock = int(float(qty_raw)) if qty_raw else 0
            except ValueError:
                stock = 0

            # use "General Medicine / Others" if category is blank
            if not category:
                category = "General Medicine / Others"
            dosage_form = dosage_form or infer_dosage_form(product_name)
            classification = classification or "Generic"
            unit_type = unit_type or infer_unit_type(dosage_form, volume or '')

            try:
                result = db.execute(
                    text(
                        """
                        INSERT INTO medicines
                            (name, category, stock, reorder_level, expiry,
                             avg_daily_sales, unit_price, supplier, added_by,
                             product_code, medicine_name, classification,
                             dosage_form, volume, manufacturer, unit_type,
                             base_unit, purchase_unit, conversion_factor)
                        VALUES
                            (:name, :category, :stock, :reorder_level, :expiry,
                            :avg_daily_sales, :unit_price, NULL, :added_by,
                            :product_code, :medicine_name, :classification,
                            :dosage_form, :volume, NULL, :unit_type,
                            'pcs', :unit_type, 1)
                        ON DUPLICATE KEY UPDATE
                            name = VALUES(name),
                            category = VALUES(category),
                            stock = VALUES(stock),
                            reorder_level = VALUES(reorder_level),
                            expiry = VALUES(expiry),
                            avg_daily_sales = VALUES(avg_daily_sales),
                            unit_price = VALUES(unit_price),
                            added_by = VALUES(added_by),
                            medicine_name = VALUES(medicine_name),
                            classification = VALUES(classification),
                            dosage_form = VALUES(dosage_form),
                            volume = VALUES(volume),
                            unit_type = VALUES(unit_type),
                            purchase_unit = VALUES(purchase_unit),
                            base_unit = 'pcs',
                            conversion_factor = 1
                        """
                    ),
                    {
                        "name"            : product_name,
                        "category"        : category,
                        "stock"           : stock,
                        "reorder_level"   : DEFAULT_REORDER_LEVEL,
                        "expiry"          : DEFAULT_EXPIRY,
                        "avg_daily_sales" : DEFAULT_AVG_DAILY_SALES,
                        "unit_price"      : unit_price,
                        "added_by"        : added_by_id,
                        "product_code"    : f"PRD-{row_num:04d}",
                        "medicine_name"   : product_name,
                        "classification"  : classification,
                        "dosage_form"     : dosage_form,
                        "volume"          : volume,
                        "unit_type"      : unit_type,
                    },
                )
                medicine_id = result.lastrowid
                if not medicine_id:
                    existing = db.execute(
                        text("SELECT id FROM medicines WHERE product_code = :product_code LIMIT 1"),
                        {"product_code": f"PRD-{row_num:04d}"},
                    ).first()
                    medicine_id = existing[0] if existing else None

                if medicine_id:
                    db.execute(
                        text(
                            """
                            INSERT INTO medicine_supplies
                                (medicine_id, batch_number, quantity, supplier,
                                 expiry_date, unit_cost, selling_price)
                            VALUES
                                (:medicine_id, :batch_number, :quantity, NULL,
                                 :expiry_date, :unit_cost, :selling_price)
                            ON DUPLICATE KEY UPDATE
                                quantity = VALUES(quantity),
                                expiry_date = VALUES(expiry_date),
                                unit_cost = VALUES(unit_cost),
                                selling_price = VALUES(selling_price)
                            """
                        ),
                        {
                            "medicine_id": medicine_id,
                            "batch_number": f"PRD-BATCH-{row_num:04d}",
                            "quantity": stock,
                            "expiry_date": DEFAULT_EXPIRY,
                            "unit_cost": unit_price * 0.7,
                            "selling_price": unit_price,
                        },
                    )
                    db.execute(
                        text(
                            """
                            INSERT INTO stock_movements (medicine_id, type, quantity)
                            VALUES (:medicine_id, 'STOCK_IN', :quantity)
                            """
                        ),
                        {"medicine_id": medicine_id, "quantity": stock},
                    )
                inserted += 1
                if inserted % 50 == 0:
                    print(f"  ... {inserted} rows inserted so far")
            except Exception as e:
                print(f"[ROW {row_num}] ERROR inserting '{product_name}': {e}")
                errors += 1

    db.commit()
    print(f"\n✅  Import complete!")
    print(f"   Inserted : {inserted}")
    print(f"   Skipped  : {skipped}")
    print(f"   Errors   : {errors}")

except Exception as e:
    db.rollback()
    print(f"[FATAL] {e}")
    sys.exit(1)
finally:
    db.close()
