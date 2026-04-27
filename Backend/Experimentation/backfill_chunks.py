import os
import math
from typing import List

from dotenv import load_dotenv
from google import genai
from google.genai import types
from sqlalchemy import create_engine, text


load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
CHUNK_CHARS = int(os.getenv("RAG_CHUNK_CHARS", "1800"))
CHUNK_OVERLAP_CHARS = int(os.getenv("RAG_CHUNK_OVERLAP_CHARS", "250"))
BATCH_SIZE = int(os.getenv("RAG_BACKFILL_BATCH_SIZE", "50"))

if not DATABASE_URL:
    raise RuntimeError("Missing DATABASE_URL in .env")
if not GEMINI_API_KEY:
    raise RuntimeError("Missing GEMINI_API_KEY in .env")

client = genai.Client(api_key=GEMINI_API_KEY)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def normalize_text(value: str) -> str:
    return " ".join((value or "").split())


def chunk_text(text_value: str, chunk_chars: int, overlap_chars: int) -> List[str]:
    text_value = normalize_text(text_value)
    if not text_value:
        return []

    if overlap_chars >= chunk_chars:
        overlap_chars = max(0, chunk_chars // 5)

    chunks = []
    start = 0
    n = len(text_value)

    while start < n:
        end = min(n, start + chunk_chars)

        if end < n:
            cut = text_value.rfind(" ", start, end)
            if cut > start + (chunk_chars // 2):
                end = cut

        chunk = text_value[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= n:
            break

        start = max(start + 1, end - overlap_chars)

    return chunks


def embed_text(value: str) -> List[float]:
    result = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=value,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_DOCUMENT",
            output_dimensionality=EMBEDDING_DIM,
        ),
    )

    vector = None
    if result.embeddings and len(result.embeddings) > 0:
        vector = result.embeddings[0].values

    if not vector:
        raise RuntimeError("Embedding API returned empty vector")

    if len(vector) != EMBEDDING_DIM:
        raise RuntimeError(
            f"Embedding dimension mismatch. Expected {EMBEDDING_DIM}, got {len(vector)}"
        )

    return vector


def to_pgvector_literal(vector: List[float]) -> str:
    # Postgres pgvector text format: [0.1,0.2,...]
    return "[" + ",".join(f"{x:.8f}" for x in vector) + "]"


def ensure_table_exists(conn):
    exists = conn.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema='public' AND table_name='contract_chunks'
            )
            """
        )
    ).scalar()

    if not exists:
        raise RuntimeError(
            "contract_chunks table not found. Run your create_indices.py setup first."
        )


def fetch_contracts_missing_chunks(conn, limit: int):
    return conn.execute(
        text(
            """
            SELECT c.id, c.contract_type, c.extracted_text
            FROM public.contracts c
            LEFT JOIN public.contract_chunks cc
                ON cc.contract_id = c.id
            GROUP BY c.id, c.contract_type, c.extracted_text
            HAVING COUNT(cc.id) = 0
            ORDER BY c.created_at ASC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()


def insert_chunk(conn, contract_id: str, contract_type: str, chunk_index: int, chunk_text_value: str, token_count: int, embedding_literal: str):
    conn.execute(
        text(
            """
            INSERT INTO public.contract_chunks
            (contract_id, chunk_index, chunk_text, token_count, contract_type, embedding)
            VALUES
            (:contract_id, :chunk_index, :chunk_text, :token_count, :contract_type, CAST(:embedding AS vector))
            ON CONFLICT (contract_id, chunk_index) DO NOTHING
            """
        ),
        {
            "contract_id": contract_id,
            "chunk_index": chunk_index,
            "chunk_text": chunk_text_value,
            "token_count": token_count,
            "contract_type": contract_type or "Other",
            "embedding": embedding_literal,
        },
    )


def estimate_tokens(text_value: str) -> int:
    # rough heuristic for logging/metadata
    return max(1, math.ceil(len(text_value) / 4))


def backfill_once(limit: int):
    with engine.begin() as conn:
        ensure_table_exists(conn)
        contracts = fetch_contracts_missing_chunks(conn, limit=limit)

    if not contracts:
        print("No contracts pending backfill.")
        return 0, 0

    contracts_done = 0
    chunks_done = 0

    for row in contracts:
        contract_id = row["id"]
        contract_type = row["contract_type"] or "Other"
        extracted_text = row["extracted_text"] or ""

        chunks = chunk_text(
            extracted_text,
            chunk_chars=CHUNK_CHARS,
            overlap_chars=CHUNK_OVERLAP_CHARS,
        )

        if not chunks:
            print(f"Skipping contract {contract_id}: empty extracted_text")
            contracts_done += 1
            continue

        with engine.begin() as conn:
            for idx, chunk in enumerate(chunks):
                vector = embed_text(chunk)
                vector_lit = to_pgvector_literal(vector)
                insert_chunk(
                    conn=conn,
                    contract_id=contract_id,
                    contract_type=contract_type,
                    chunk_index=idx,
                    chunk_text_value=chunk,
                    token_count=estimate_tokens(chunk),
                    embedding_literal=vector_lit,
                )
                chunks_done += 1

        print(f"Backfilled contract {contract_id}: {len(chunks)} chunks")
        contracts_done += 1

    return contracts_done, chunks_done


def main():
    total_contracts = 0
    total_chunks = 0

    while True:
        contracts_done, chunks_done = backfill_once(limit=BATCH_SIZE)
        total_contracts += contracts_done
        total_chunks += chunks_done

        if contracts_done == 0:
            break

    print("\nBackfill complete")
    print(f"Contracts processed: {total_contracts}")
    print(f"Chunks inserted: {total_chunks}")


if __name__ == "__main__":
    main()