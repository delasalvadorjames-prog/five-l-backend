import argparse
import csv
import os
import sys
from datetime import date, datetime
from pathlib import Path

import pymysql
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from urllib.parse import quote_plus


def load_local_env():
    env_path = Path(__file__).resolve().parent / '.env'
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"\'')
        os.environ[key] = value


load_local_env()


def build_mysql_url():
    host = os.getenv('DB_HOST', '127.0.0.1')
    port = os.getenv('DB_PORT', '3306')
    user = os.getenv('DB_USER', 'root')
    password = os.getenv('DB_PASSWORD', '')
    db_name = os.getenv('DB_NAME', 'five-l')

    user_part = quote_plus(user or 'root')
    password_part = quote_plus(password) if password else ''
    auth = f"{user_part}:{password_part}" if password_part else user_part
    return f'mysql+pymysql://{auth}@{host}:{port}/{db_name}'


def parse_arguments():
    parser = argparse.ArgumentParser(description='Import medicine_price_dataset.csv into medicines inventory')
    parser.add_argument('--file', default=None, help='Path to medicine_price_dataset.csv')
    parser.add_argument('--email', default='delasalvadorjames@gmail.com', help='User email to set as added_by')
    return parser.parse_args()


def as_int(value, default=0):
    try:
        return int(float(value))
    except Exception:
        return default


def as_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def map_demand_to_sales(demand):
    if not demand:
        return 1.0
    normalized = demand.strip().lower()
    if normalized == 'high':
        return 5.0
    if normalized == 'medium':
        return 3.0
    return 1.0


def build_expiry(months):
    months_count = as_int(months, 0)
    if months_count <= 0:
        return date(2027, 12, 31)
    today = date.today()
    # add months roughly
    month = today.month - 1 + months_count
    year = today.year + month // 12
    month = month % 12 + 1
    day = min(today.day, 28)
    return date(year, month, day)


def main():
    args = parse_arguments()
    script_dir = Path(__file__).resolve().parent
    default_file = script_dir.parent / 'five-l' / 'src' / 'file ' / 'medicine_price_dataset.csv'
    csv_path = Path(args.file) if args.file else default_file
    csv_path = csv_path.expanduser().resolve()
    if not csv_path.exists():
        print(f'[ERROR] CSV file not found: {csv_path}')
        sys.exit(1)

    mysql_url = build_mysql_url()
    engine = create_engine(mysql_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    user_id = None
    with engine.connect() as conn:
        result = conn.execute(text('SELECT id FROM users WHERE email = :email'), {'email': args.email})
        row = result.first()
        user_id = row[0] if row else None
        if user_id is None:
            print(f'[WARN] No user found for email {args.email}. Records will be inserted without added_by.')

    inserted = 0
    skipped = 0
    errors = 0

    db = SessionLocal()
    try:
        with open(csv_path, encoding='utf-8-sig', newline='') as f:
            reader = csv.reader(f)
            headers = next(reader, None)
            if headers is None:
                print('[ERROR] CSV is empty')
                sys.exit(1)
            for row_num, row in enumerate(reader, start=2):
                if not any(cell.strip() for cell in row):
                    skipped += 1
                    continue

                medicine_name = row[1].strip() if len(row) > 1 else ''
                dosage_form = row[2].strip() if len(row) > 2 else ''
                manufacturer = row[3].strip() if len(row) > 3 else ''
                strength_val = row[4].strip() if len(row) > 4 else ''
                pack_size = row[5].strip() if len(row) > 5 else ''
                price_raw = row[11].strip() if len(row) > 11 else ''
                import_status = row[7].strip() if len(row) > 7 else ''
                demand_level = row[8].strip() if len(row) > 8 else ''
                expiry_months = row[9].strip() if len(row) > 9 else ''

                if not medicine_name or medicine_name.lower() == 'medicine_name':
                    skipped += 1
                    continue

                try:
                    stock = as_int(pack_size, 0)
                    unit_price = as_float(price_raw, 0.0)
                    expiry = build_expiry(expiry_months)
                    avg_daily_sales = map_demand_to_sales(demand_level)
                    strength = f'{strength_val}mg' if strength_val else None
                    volume = f'{pack_size} pcs' if pack_size else None
                    category = import_status or 'General Medicine / Others'
                    product_code = f'CSV-{medicine_name[:20].replace(" ", "_")}-{row_num}'

                    db.execute(
                        text(
                            '''
                            INSERT INTO medicines
                                (name, category, stock, reorder_level, expiry,
                                 avg_daily_sales, unit_price, supplier, added_by,
                                 product_code, medicine_name, classification, dosage_form,
                                 strength, volume, manufacturer)
                            VALUES
                                (:name, :category, :stock, :reorder_level, :expiry,
                                 :avg_daily_sales, :unit_price, :supplier, :added_by,
                                 :product_code, :medicine_name, :classification, :dosage_form,
                                 :strength, :volume, :manufacturer)
                            '''
                        ),
                        {
                            'name': medicine_name,
                            'category': category,
                            'stock': stock,
                            'reorder_level': 10,
                            'expiry': expiry,
                            'avg_daily_sales': avg_daily_sales,
                            'unit_price': unit_price,
                            'supplier': manufacturer,
                            'added_by': user_id,
                            'product_code': product_code,
                            'medicine_name': medicine_name,
                            'classification': 'Generic',
                            'dosage_form': dosage_form or 'Tablet',
                            'strength': strength,
                            'volume': volume,
                            'manufacturer': manufacturer,
                        },
                    )
                    inserted += 1
                    if inserted % 50 == 0:
                        print(f'  ... {inserted} rows inserted so far')
                except Exception as exc:
                    print(f'[ROW {row_num}] ERROR inserting {medicine_name}: {exc}')
                    errors += 1

        db.commit()
        print('\n✅ Import complete!')
        print(f'   Inserted : {inserted}')
        print(f'   Skipped  : {skipped}')
        print(f'   Errors   : {errors}')
    except Exception as exc:
        db.rollback()
        print(f'[FATAL] {exc}')
        sys.exit(1)
    finally:
        db.close()


if __name__ == '__main__':
    main()
