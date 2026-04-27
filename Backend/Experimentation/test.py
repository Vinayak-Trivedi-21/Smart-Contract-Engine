import os
from sqlalchemy import create_engine, text
import boto3
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

def test_db():
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        db_name = conn.execute(text("select current_database()")).scalar()
        user_name = conn.execute(text("select current_user")).scalar()
        table_exists = conn.execute(text("""
            select exists (
                select 1
                from information_schema.tables
                where table_schema='public' and table_name='contracts'
            )
        """)).scalar()
        print("DB:", db_name)
        print("User:", user_name)
        print("contracts table exists:", table_exists)

def test_s3():
    s3 = boto3.client("s3", region_name=AWS_REGION)
    key = "contracts/test-permission-check.txt"
    s3.put_object(Bucket=S3_BUCKET_NAME, Key=key, Body=b"ok")
    print("S3 put_object works:", key)

def show_contracts(limit=20):
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT *
            FROM contracts
            ORDER BY created_at DESC
            LIMIT :limit
        """), {"limit": limit}).mappings().all()

        if not rows:
            print("No rows found in contracts table.")
            return

        for row in rows:
            print(
                f"id={row['id']} | title={row['title']} | "
                f"type={row['contract_type']} | created_at={row['created_at']}"
            )

if __name__ == "__main__":
    test_db()
    show_contracts(2)