from sqlalchemy import create_engine, text
from transactions import build_mysql_url
from datetime import date, timedelta

url = build_mysql_url()
engine = create_engine(url)

today = date.today().isoformat()
sixty = (date.today() + timedelta(days=60)).isoformat()

with engine.connect() as conn:
    sql = f"""
        SELECT
            m.id,
            COALESCE(m.medicine_name, m.name)                                    AS medicine_name,
            m.category,
            m.stock,
            COALESCE(m.reorder_level, 0)                                         AS reorder_level,
            m.expiry,
            m.unit_price,
            CASE
                WHEN m.stock <= 0                                        THEN 'Out of Stock'
                WHEN m.stock <= COALESCE(m.reorder_level, 0)             THEN 'Low Stock'
                WHEN m.expiry IS NOT NULL AND DATE(m.expiry) < :today    THEN 'Expired'
                WHEN m.expiry IS NOT NULL AND DATE(m.expiry) <= :sixty   THEN 'Expiring Soon'
                ELSE 'Healthy'
            END                                                                  AS status
        FROM medicines m
        WHERE m.name LIKE '%Levocetirizine%'
    """
    row = conn.execute(text(sql), {"today": today, "sixty": sixty}).fetchone()
    print("Row:", row)
