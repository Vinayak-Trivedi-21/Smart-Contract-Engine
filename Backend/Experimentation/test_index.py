from sqlalchemy import create_engine, text
from dotenv import load_dotenv
from datetime import datetime
import os
import uuid
import random

load_dotenv()

db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError("DATABASE_URL not found in .env")

engine = create_engine(db_url, pool_pre_ping=True)

def seed_rows(conn, n=2000):
    print(f"Seeding {n} rows...")
    words = ["NDA", "Service", "Lease", "Employment", "Purchase", "License", "Vendor"]
    for _ in range(n):
        cid = str(uuid.uuid4())
        w = random.choice(words)
        title = f"{w} Contract {uuid.uuid4().hex[:8]}"
        ctype = w if w != "Vendor" else "Service Agreement"
        bucket = "dev-bucket"
        skey = f"contracts/{cid}/file.pdf"
        extracted = "sample text"
        conn.execute(
            text(
                """
                INSERT INTO public.contracts
                (id, title, contract_type, s3_bucket, s3_key, extracted_text, created_at)
                VALUES
                (:id, :title, :contract_type, :s3_bucket, :s3_key, :extracted_text, :created_at)
                """
            ),
            {
                "id": cid,
                "title": title,
                "contract_type": ctype,
                "s3_bucket": bucket,
                "s3_key": skey,
                "extracted_text": extracted,
                "created_at": datetime.utcnow(),
            },
        )
    print("Seed complete.")

def show_plan(conn, term):
    print(f"\nEXPLAIN ANALYZE for term: {term}")
    rows = conn.execute(
        text(
            """
            EXPLAIN ANALYZE
            SELECT id, title, contract_type, s3_key, created_at
            FROM public.contracts
            WHERE title ILIKE :pattern
            ORDER BY created_at DESC
            LIMIT 25
            """
        ),
        {"pattern": f"%{term}%"},
    ).fetchall()
    for r in rows:
        print(r[0])

def show_indexes(conn):
    print("\nCurrent indexes on contracts:")
    rows = conn.execute(
        text(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = 'public' AND tablename = 'contracts'
            ORDER BY indexname
            """
        )
    ).fetchall()
    for name, definition in rows:
        print("-", name)
        print(" ", definition)

with engine.connect() as conn:
    # Optional: seed if table is tiny
    do_seed = input("Seed 2000 test rows first? (y/n): ").strip().lower() == "y"
    if do_seed:
        seed_rows(conn, 2000)
        conn.commit()

    show_indexes(conn)

    term = input("\nEnter search term (example: NDA, Lease, Service): ").strip() or "NDA"
    show_plan(conn, term)

    force = input("\nForce planner to avoid seq scan for demo? (y/n): ").strip().lower() == "y"
    if force:
        conn.execute(text("SET enable_seqscan = off"))
        show_plan(conn, term)