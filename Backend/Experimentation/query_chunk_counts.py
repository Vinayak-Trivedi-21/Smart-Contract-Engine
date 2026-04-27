import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError("Missing DATABASE_URL in .env")

engine = create_engine(database_url, pool_pre_ping=True)

sql = """
SELECT contract_id, COUNT(*) AS chunk_count
FROM public.contract_chunks
GROUP BY contract_id
ORDER BY chunk_count DESC
LIMIT 10;
"""

with engine.connect() as conn:
    rows = conn.execute(text(sql)).mappings().all()

if not rows:
    print("No rows found in contract_chunks.")
else:
    print("Top 10 contracts by chunk count:")
    for row in rows:
        print(f"contract_id={row['contract_id']} | chunk_count={row['chunk_count']}")