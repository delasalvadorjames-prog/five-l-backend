from sqlalchemy import create_engine, text
from transactions import build_mysql_url

url = build_mysql_url()
engine = create_engine(url)

with engine.connect() as conn:
    med = conn.execute(text("SELECT id, name, category, stock, unit_price, expiry FROM medicines WHERE name LIKE '%Levocetirizine%'")).fetchone()
    print("Levocetirizine:", med)
