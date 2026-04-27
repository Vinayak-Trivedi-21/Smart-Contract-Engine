import os
from pathlib import Path
from datetime import datetime
from typing import Generator, List

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

BASE_DIR = Path(__file__).resolve().parent

# Load env from both repo root and Backend/.env to match existing project setup.
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / "Backend" / ".env")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
LOCAL_FALLBACK_DATABASE_URL = os.getenv("LOCAL_FALLBACK_DATABASE_URL", "sqlite:///./local_dev.db")
DB_FALLBACK_ENABLED = os.getenv("DB_FALLBACK_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
SERVICE_PORT = int(os.getenv("CONTRACT_REVIEW_SERVICE_PORT", "8001"))


def build_engine(db_url: str):
	return create_engine(
		db_url,
		pool_pre_ping=True,
		connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
	)


def initialize_engine_with_fallback():
	if not DATABASE_URL:
		if DB_FALLBACK_ENABLED:
			print("[startup] DATABASE_URL not set. Falling back to local sqlite database.")
			return build_engine(LOCAL_FALLBACK_DATABASE_URL), LOCAL_FALLBACK_DATABASE_URL
		raise RuntimeError("DATABASE_URL is not set and DB_FALLBACK_ENABLED=false.")

	primary_engine = build_engine(DATABASE_URL)
	try:
		with primary_engine.connect() as conn:
			conn.execute(text("SELECT 1"))
		return primary_engine, DATABASE_URL
	except Exception as error:
		if not DB_FALLBACK_ENABLED:
			raise RuntimeError(f"Failed to connect to DATABASE_URL and fallback is disabled: {error}")

		print(f"[startup] Failed to connect to DATABASE_URL: {error}")
		print(f"[startup] Falling back to local database: {LOCAL_FALLBACK_DATABASE_URL}")

		fallback_engine = build_engine(LOCAL_FALLBACK_DATABASE_URL)
		with fallback_engine.connect() as conn:
			conn.execute(text("SELECT 1"))

		return fallback_engine, LOCAL_FALLBACK_DATABASE_URL


engine, ACTIVE_DATABASE_URL = initialize_engine_with_fallback()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

print(f"[startup] Contract review service using database: {ACTIVE_DATABASE_URL}")

app = FastAPI(title="Contract Review Picker Service")

app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


class ContractSearchItem(BaseModel):
	id: str
	title: str
	contractType: str = ""
	createdAt: datetime | None = None


class ContractSearchResponse(BaseModel):
	items: List[ContractSearchItem]


class ReviewContractItem(BaseModel):
	contract_id: str
	filename: str
	status: str = ""
	contract_type: str = ""
	created_at: datetime | None = None


class ReviewContractListResponse(BaseModel):
	items: List[ReviewContractItem]


def get_db() -> Generator[Session, None, None]:
	db = SessionLocal()
	try:
		yield db
	finally:
		db.close()


@app.get("/health")
def health_check():
	return {"status": "ok", "databaseUrl": ACTIVE_DATABASE_URL}


@app.get("/contracts/search", response_model=ContractSearchResponse)
def search_contracts(
	title: str = "",
	limit: int = 200,
	db: Session = Depends(get_db),
):
	safe_limit = max(1, min(limit, 500))
	search_value = title.strip()
	like_value = f"%{search_value}%"

	query = text(
		"""
		SELECT contract_id, filename, contract_type, created_at
		FROM contracts
		WHERE (:title = '' OR lower(filename) LIKE lower(:like_value) OR lower(CAST(contract_id AS TEXT)) LIKE lower(:like_value))
		ORDER BY created_at DESC
		LIMIT :limit
		"""
	)

	try:
		rows = db.execute(query, {"title": search_value, "like_value": like_value, "limit": safe_limit}).mappings().all()
	except Exception as error:
		raise HTTPException(status_code=502, detail=f"Failed to load contracts: {error}")

	return ContractSearchResponse(
		items=[
			ContractSearchItem(
				id=str(row.get("contract_id") or ""),
				title=str(row.get("filename") or "Untitled Contract"),
				contractType=str(row.get("contract_type") or ""),
				createdAt=row.get("created_at"),
			)
			for row in rows
			if row.get("contract_id")
		]
	)


@app.get("/contracts/review-options", response_model=ReviewContractListResponse)
def review_options(limit: int = 200, db: Session = Depends(get_db)):
	safe_limit = max(1, min(limit, 500))
	try:
		rows = db.execute(
			text(
				"""
				SELECT contract_id, filename, status, contract_type, created_at
				FROM contracts
				ORDER BY created_at DESC
				LIMIT :limit
				"""
			),
			{"limit": safe_limit},
		).mappings().all()
	except Exception as error:
		raise HTTPException(status_code=502, detail=f"Failed to load contracts for review: {error}")

	return ReviewContractListResponse(
		items=[
			ReviewContractItem(
				contract_id=str(row.get("contract_id") or ""),
				filename=str(row.get("filename") or "Untitled Contract"),
				status=str(row.get("status") or ""),
				contract_type=str(row.get("contract_type") or ""),
				created_at=row.get("created_at"),
			)
			for row in rows
			if row.get("contract_id")
		]
	)


if __name__ == "__main__":
	import uvicorn

	uvicorn.run("contract_review_service:app", host="0.0.0.0", port=SERVICE_PORT, reload=False)