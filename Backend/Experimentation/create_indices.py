from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()

db_url = os.getenv("DATABASE_URL")
if not db_url:
    raise RuntimeError("DATABASE_URL not found in .env")

engine = create_engine(db_url, pool_pre_ping=True)

statements = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm;",
    "CREATE INDEX IF NOT EXISTS idx_contracts_title_trgm ON public.contracts USING GIN (title gin_trgm_ops);",
    "CREATE INDEX IF NOT EXISTS idx_contracts_created_at_desc ON public.contracts (created_at DESC);",
    "ANALYZE public.contracts;",
]

with engine.connect() as conn:
    for stmt in statements:
        print(f"Running: {stmt}")
        conn.execute(text(stmt))
    conn.commit()

    print("\nEXPLAIN ANALYZE:")
    rows = conn.execute(text("""
        EXPLAIN ANALYZE
        SELECT id, title, contract_type, s3_key, created_at
        FROM public.contracts
        WHERE title ILIKE '%nda%'
        ORDER BY created_at DESC
        LIMIT 25;
    """)).fetchall()

    for row in rows:
        print(row[0])
    import os


DATABASE_URL = os.getenv("DATABASE_URL")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
IVFFLAT_LISTS = int(os.getenv("IVFFLAT_LISTS", "100"))

if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL in .env")

# AUTOCOMMIT keeps DDL behavior predictable on Postgres
engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    isolation_level="AUTOCOMMIT",
)

def run():
    with engine.connect() as conn:
        print("Checking vector extension availability...")
        available = conn.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_available_extensions
                    WHERE name = 'vector'
                )
                """
            )
        ).scalar()

        if not available:
            raise RuntimeError(
                "pgvector extension is not available on this RDS instance/version."
            )

        print("Enabling pgvector extension...")
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))

        print("Creating contract_chunks table...")
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS public.contract_chunks (
                    id BIGSERIAL PRIMARY KEY,
                    contract_id VARCHAR(36) NOT NULL
                        REFERENCES public.contracts(id)
                        ON DELETE CASCADE,
                    chunk_index INT NOT NULL,
                    chunk_text TEXT NOT NULL,
                    token_count INT NOT NULL DEFAULT 0,
                    contract_type VARCHAR(100) NOT NULL,
                    embedding VECTOR({EMBEDDING_DIM}) NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                    CONSTRAINT uq_contract_chunks_contract_chunk
                        UNIQUE (contract_id, chunk_index)
                );
                """
            )
        )

        print("Creating B-tree indexes...")
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_contract_chunks_contract_id
                ON public.contract_chunks (contract_id);
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_contract_chunks_contract_type
                ON public.contract_chunks (contract_type);
                """
            )
        )

        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_contract_chunks_created_at_desc
                ON public.contract_chunks (created_at DESC);
                """
            )
        )

        print("Creating vector index (ivfflat)...")
        conn.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS idx_contract_chunks_embedding_ivfflat
                ON public.contract_chunks
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = {IVFFLAT_LISTS});
                """
            )
        )

        print("Refreshing planner stats...")
        conn.execute(text("ANALYZE public.contract_chunks;"))

        print("Verifying setup...")
        ext_enabled = conn.execute(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        ).scalar()

        idx_rows = conn.execute(
            text(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'public'
                  AND tablename = 'contract_chunks'
                ORDER BY indexname;
                """
            )
        ).fetchall()

        print("vector extension enabled:", ext_enabled)
        print("contract_chunks indexes:")
        for row in idx_rows:
            print("-", row[0])

if __name__ == "__main__":
    run()