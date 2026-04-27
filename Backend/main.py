import io
import os
import re
import uuid
from datetime import datetime
from typing import Generator, List

import boto3
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from pypdf import PdfReader
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas
from sqlalchemy import DateTime, String, Text, create_engine, or_, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local_dev.db")
LOCAL_FALLBACK_DATABASE_URL = os.getenv("LOCAL_FALLBACK_DATABASE_URL", "sqlite:///./local_dev.db")
DB_FALLBACK_ENABLED = os.getenv("DB_FALLBACK_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
PRESIGNED_URL_EXPIRY_SECONDS = int(os.getenv("PRESIGNED_URL_EXPIRY_SECONDS", "3600"))
RAG_ENABLED = os.getenv("RAG_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
RAG_TOP_K = int(os.getenv("RAG_TOP_K", "6"))
RAG_MAX_CONTEXT_CHARS = int(os.getenv("RAG_MAX_CONTEXT_CHARS", "6000"))
RAG_EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
RAG_EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "768"))
S3_ENABLED = bool(S3_BUCKET_NAME)

if not os.getenv("DATABASE_URL"):
	print("[startup] DATABASE_URL not set. Using local sqlite database at ./local_dev.db")

if not S3_ENABLED:
	print("[startup] S3 is disabled because S3_BUCKET_NAME is not set. Upload/download endpoints will return 503.")


def build_engine(db_url: str):
	return create_engine(
		db_url,
		pool_pre_ping=True,
		connect_args={"check_same_thread": False} if db_url.startswith("sqlite") else {},
	)


def initialize_engine_with_fallback():
	primary_engine = build_engine(DATABASE_URL)
	try:
		with primary_engine.connect() as conn:
			conn.execute(text("SELECT 1"))
		return primary_engine, DATABASE_URL
	except Exception as error:
		if not DB_FALLBACK_ENABLED:
			raise RuntimeError(
				f"Failed to connect to DATABASE_URL and DB_FALLBACK_ENABLED=false. Original error: {error}"
			)

		print(f"[startup] Failed to connect to DATABASE_URL: {error}")
		print(f"[startup] Falling back to local database: {LOCAL_FALLBACK_DATABASE_URL}")

		fallback_engine = build_engine(LOCAL_FALLBACK_DATABASE_URL)
		with fallback_engine.connect() as conn:
			conn.execute(text("SELECT 1"))

		return fallback_engine, LOCAL_FALLBACK_DATABASE_URL


engine, ACTIVE_DATABASE_URL = initialize_engine_with_fallback()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
	pass


class ContractMetadata(Base):
	__tablename__ = "contracts"

	id: Mapped[str] = mapped_column(String(36), primary_key=True)
	title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
	contract_type: Mapped[str] = mapped_column(String(100), nullable=False)
	s3_bucket: Mapped[str] = mapped_column(String(255), nullable=False)
	s3_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
	extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
	created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Smart Contract Engine API")

app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


class ContractRequest(BaseModel):
	name: str = Field(..., min_length=1)
	party1: str = Field(..., min_length=1)
	party2: str = Field(..., min_length=1)
	contractType: str = Field(..., min_length=1)
	context: str = ""
	compliance: List[str] = []


class UploadContractResponse(BaseModel):
	id: str
	title: str
	contractType: str
	s3Key: str
	createdAt: datetime
	message: str


class ContractSearchItem(BaseModel):
	id: str
	title: str
	contractType: str
	s3Key: str
	createdAt: datetime


class ContractSearchResponse(BaseModel):
	items: List[ContractSearchItem]


class DownloadContractResponse(BaseModel):
	contractId: str
	downloadUrl: str
	expiresInSeconds: int


class ChatbotRequest(BaseModel):
	question: str = Field(..., min_length=1)


class ChatbotResponse(BaseModel):
	answer: str
	references: List[str] = []


def get_db() -> Generator[Session, None, None]:
	db = SessionLocal()
	try:
		yield db
	finally:
		db.close()


def get_s3_client():
	if not S3_ENABLED:
		raise HTTPException(
			status_code=503,
			detail="S3 integration is disabled. Set S3_BUCKET_NAME to enable upload/download APIs.",
		)
	return boto3.client("s3", region_name=AWS_REGION)


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
	try:
		reader = PdfReader(io.BytesIO(pdf_bytes))
		text_chunks = []
		for page in reader.pages:
			text_chunks.append(page.extract_text() or "")
		combined = "\n".join(text_chunks).strip()
		return combined or "No text extracted from PDF."
	except Exception as error:
		raise HTTPException(status_code=400, detail=f"Unable to read PDF text: {error}")


def classify_contract_type_with_gemini(extracted_text: str) -> str:
	api_key = os.getenv("GEMINI_API_KEY")
	if not api_key:
		return classify_contract_type_heuristic(extracted_text)

	client = genai.Client(api_key=api_key)
	prompt = (
		"Classify this contract into one short type label such as NDA, Employment, Service Agreement, "
		"Lease, Purchase Agreement, Licensing, or Other. Respond with only the type label.\n\n"
		f"Contract text:\n{extracted_text[:10000]}"
	)

	try:
		response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
		value = (response.text or "").strip()
		return value[:100] if value else classify_contract_type_heuristic(extracted_text)
	except Exception:
		return classify_contract_type_heuristic(extracted_text)


def classify_contract_type_heuristic(extracted_text: str) -> str:
	text = extracted_text.lower()
	if "non-disclosure" in text or "nda" in text or "confidential" in text:
		return "NDA"
	if "employment" in text or "employee" in text:
		return "Employment"
	if "service agreement" in text or "services" in text:
		return "Service Agreement"
	if "lease" in text or "landlord" in text or "tenant" in text:
		return "Lease"
	if "license" in text or "licensing" in text:
		return "Licensing"
	if "purchase" in text or "buyer" in text or "seller" in text:
		return "Purchase Agreement"
	return "Other"


def sanitize_title(value: str) -> str:
	sanitized = re.sub(r"\s+", " ", value or "").strip()
	return sanitized[:255] if sanitized else "Untitled Contract"


def normalize_contract_type(value: str) -> str:
	return re.sub(r"\s+", " ", value or "").strip().lower()


def to_pgvector_literal(vector: List[float]) -> str:
	return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


def build_retrieval_query_text(payload: ContractRequest) -> str:
	compliance_items = ", ".join(payload.compliance) if payload.compliance else "None"
	return (
		f"Contract Type: {payload.contractType}\n"
		f"Contract Name: {payload.name}\n"
		f"Party 1: {payload.party1}\n"
		f"Party 2: {payload.party2}\n"
		f"Compliance: {compliance_items}\n"
		f"Context: {payload.context or 'No additional context provided.'}"
	)


def embed_retrieval_query(query_text: str) -> List[float]:
	api_key = os.getenv("GEMINI_API_KEY")
	if not api_key:
		raise RuntimeError("Missing GEMINI_API_KEY environment variable on the backend.")

	client = genai.Client(api_key=api_key)
	result = client.models.embed_content(
		model=RAG_EMBEDDING_MODEL,
		contents=query_text,
		config=types.EmbedContentConfig(
			task_type="RETRIEVAL_QUERY",
			output_dimensionality=RAG_EMBEDDING_DIM,
		),
	)

	if not result.embeddings:
		raise RuntimeError("Embedding API returned no query embeddings.")

	vector = result.embeddings[0].values
	if not vector:
		raise RuntimeError("Embedding API returned an empty query embedding.")

	if len(vector) != RAG_EMBEDDING_DIM:
		raise RuntimeError(
			f"Embedding dimension mismatch. Expected {RAG_EMBEDDING_DIM}, got {len(vector)}"
		)

	return vector


def retrieve_same_type_chunks(payload: ContractRequest, db: Session) -> List[dict]:
	if not RAG_ENABLED:
		return []

	type_norm = normalize_contract_type(payload.contractType)
	if not type_norm:
		return []

	query_text = build_retrieval_query_text(payload)
	query_embedding = embed_retrieval_query(query_text)
	query_vector_literal = to_pgvector_literal(query_embedding)

	rows = db.execute(
		text(
			"""
			SELECT
				contract_id,
				chunk_index,
				chunk_text,
				contract_type,
				1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
			FROM public.contract_chunks
			WHERE lower(trim(contract_type)) = :contract_type_norm
			ORDER BY embedding <=> CAST(:query_vector AS vector)
			LIMIT :top_k
			"""
		),
		{
			"query_vector": query_vector_literal,
			"contract_type_norm": type_norm,
			"top_k": max(1, min(RAG_TOP_K, 20)),
		},
	).mappings().all()

	seen = set()
	results = []
	for row in rows:
		key = (row["contract_id"], row["chunk_index"])
		if key in seen:
			continue
		seen.add(key)
		results.append(dict(row))

	return results


def build_prompt_with_references(payload: ContractRequest, chunks: List[dict]) -> str:
	base_prompt = build_prompt(payload)
	if not chunks:
		return base_prompt

	used_chars = 0
	reference_blocks = []
	for idx, item in enumerate(chunks, 1):
		chunk_text = re.sub(r"\s+", " ", item.get("chunk_text", "") or "").strip()
		if not chunk_text:
			continue

		block = (
			f"[Reference {idx}] Type: {item.get('contract_type', 'Unknown')} | "
			f"Contract ID: {item.get('contract_id')} | Chunk: {item.get('chunk_index')}\n"
			f"{chunk_text}\n"
		)

		if used_chars + len(block) > max(500, RAG_MAX_CONTEXT_CHARS):
			break

		reference_blocks.append(block)
		used_chars += len(block)

	if not reference_blocks:
		return base_prompt

	references_text = "\n".join(reference_blocks)
	return (
		base_prompt
		+ "\n\nUse the following reference snippets from prior contracts of the same type. "
		+ "Use them for structure and clause style, but do not copy text verbatim unless the language is generic and standard.\n\n"
		+ references_text
	)


def build_prompt(payload: ContractRequest) -> str:
	compliance_items = ", ".join(payload.compliance) if payload.compliance else "None specified"
	return (
		"Draft a clear, professional contract using the details below. "
		"Use plain legal language and include sections for scope, payment (if relevant), "
		"term, confidentiality, termination, dispute resolution, and signatures.\n\n"
		f"Contract Name: {payload.name}\n"
		f"Party 1: {payload.party1}\n"
		f"Party 2: {payload.party2}\n"
		f"Type of Contract: {payload.contractType}\n"
		f"Compliance Regimes: {compliance_items}\n"
		f"Additional Context: {payload.context or 'No additional context provided.'}\n\n"
		"Output only the final contract text."
	)


def tokenize_question(question: str) -> List[str]:
	stopwords = {
		"the",
		"and",
		"that",
		"with",
		"from",
		"about",
		"have",
		"what",
		"when",
		"where",
		"which",
		"would",
		"could",
		"should",
		"into",
		"your",
		"this",
		"there",
		"their",
		"they",
		"them",
		"just",
		"please",
		"show",
	}

	tokens = re.findall(r"[A-Za-z0-9']+", question.lower())
	filtered = [t for t in tokens if len(t) >= 4 and t not in stopwords]

	seen = set()
	unique_tokens = []
	for token in filtered:
		if token in seen:
			continue
		seen.add(token)
		unique_tokens.append(token)

	return unique_tokens[:8]


def fetch_contract_context_for_chat(question: str, db: Session, limit: int = 4) -> tuple[str, List[str]]:
	tokens = tokenize_question(question)

	query = db.query(ContractMetadata)
	if tokens:
		conditions = []
		for token in tokens:
			like_token = f"%{token}%"
			conditions.append(ContractMetadata.title.ilike(like_token))
			conditions.append(ContractMetadata.extracted_text.ilike(like_token))

		query = query.filter(or_(*conditions))

	contracts = query.order_by(ContractMetadata.created_at.desc()).limit(max(1, min(limit, 8))).all()

	if not contracts:
		contracts = (
			db.query(ContractMetadata)
			.order_by(ContractMetadata.created_at.desc())
			.limit(max(1, min(limit, 8)))
			.all()
		)

	if not contracts:
		return "", []

	references = []
	blocks = []
	used_chars = 0
	max_chars = 9000

	for item in contracts:
		reference_title = f"{item.title} ({item.contract_type})"
		references.append(reference_title)
		snippet = re.sub(r"\s+", " ", item.extracted_text or "").strip()
		if not snippet:
			continue

		snippet = snippet[:2200]
		block = f"Title: {item.title}\nType: {item.contract_type}\nText:\n{snippet}\n"
		if used_chars + len(block) > max_chars:
			break

		blocks.append(block)
		used_chars += len(block)

	return "\n\n".join(blocks), references


def build_chatbot_prompt(question: str, context: str) -> str:
	if not context:
		return (
			"You are a contract analysis assistant. "
			"Answer the user question clearly. If the user asks about specific uploaded documents but no context is available, "
			"say that there are no uploaded contracts available yet.\n\n"
			f"User question: {question}"
		)

	return (
		"You are a contract analysis assistant. Use only the provided contract excerpts as your primary source. "
		"If the answer is not present in the excerpts, say what is missing and avoid making up facts. "
		"Keep the answer concise and practical for business users.\n\n"
		f"User question: {question}\n\n"
		"Contract excerpts:\n"
		f"{context}"
	)


def generate_contract_text(prompt: str) -> str:
	api_key = os.getenv("GEMINI_API_KEY")
	if not api_key:
		raise HTTPException(
			status_code=500,
			detail="Missing GEMINI_API_KEY environment variable on the backend.",
		)

	client = genai.Client(api_key=api_key)
	response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)

	content = (response.text or "").strip()
	if not content:
		raise HTTPException(status_code=502, detail="Gemini returned an empty contract response.")

	return content


def wrap_text_for_pdf(pdf_canvas: canvas.Canvas, text: str, max_width: float):
	lines = []
	paragraphs = text.splitlines() or [""]

	for paragraph in paragraphs:
		paragraph = paragraph.rstrip()
		if not paragraph:
			lines.append("")
			continue

		words = paragraph.split()
		current_line = words[0]

		for word in words[1:]:
			candidate = f"{current_line} {word}"
			if pdf_canvas.stringWidth(candidate, "Times-Roman", 11) <= max_width:
				current_line = candidate
			else:
				lines.append(current_line)
				current_line = word

		lines.append(current_line)

	return lines


def build_contract_pdf(contract_name: str, contract_text: str) -> bytes:
	buffer = io.BytesIO()
	pdf = canvas.Canvas(buffer, pagesize=LETTER)

	width, height = LETTER
	left_margin = 0.8 * inch
	right_margin = width - 0.8 * inch
	top_margin = height - 0.8 * inch
	bottom_margin = 0.8 * inch

	pdf.setTitle(contract_name)
	pdf.setFont("Times-Bold", 14)
	pdf.drawString(left_margin, top_margin, contract_name)

	y = top_margin - 0.4 * inch
	pdf.setFont("Times-Roman", 11)

	lines = wrap_text_for_pdf(pdf, contract_text, right_margin - left_margin)
	line_height = 14

	for line in lines:
		if y <= bottom_margin:
			pdf.showPage()
			pdf.setFont("Times-Roman", 11)
			y = top_margin

		pdf.drawString(left_margin, y, line)
		y -= line_height

	pdf.save()
	buffer.seek(0)
	return buffer.getvalue()


@app.get("/health")
def health_check():
	return {
		"status": "ok",
		"s3Enabled": S3_ENABLED,
		"databaseUrl": ACTIVE_DATABASE_URL,
	}


@app.post("/contracts/upload", response_model=UploadContractResponse)
async def upload_contract(
	file: UploadFile = File(...),
	title: str = Form(""),
	db: Session = Depends(get_db),
):
	if not S3_ENABLED:
		raise HTTPException(
			status_code=503,
			detail="Contract upload is unavailable because S3 is disabled.",
		)

	if not file.filename:
		raise HTTPException(status_code=400, detail="Missing file name.")

	file_name = file.filename
	if not file_name.lower().endswith(".pdf"):
		raise HTTPException(status_code=400, detail="Only PDF files are supported.")

	file_bytes = await file.read()
	if not file_bytes:
		raise HTTPException(status_code=400, detail="Uploaded file is empty.")

	object_id = str(uuid.uuid4())
	safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", file_name)
	s3_key = f"contracts/{object_id}/{safe_name}"

	s3_client = get_s3_client()
	try:
		s3_client.put_object(
			Bucket=S3_BUCKET_NAME,
			Key=s3_key,
			Body=file_bytes,
			ContentType="application/pdf",
		)
	except Exception as error:
		raise HTTPException(status_code=502, detail=f"Failed to upload to S3: {error}")

	extracted_text = extract_text_from_pdf(file_bytes)
	contract_type = classify_contract_type_with_gemini(extracted_text)

	input_title = title or os.path.splitext(file_name)[0]
	normalized_title = sanitize_title(input_title)

	contract = ContractMetadata(
		id=object_id,
		title=normalized_title,
		contract_type=contract_type,
		s3_bucket=S3_BUCKET_NAME,
		s3_key=s3_key,
		extracted_text=extracted_text,
	)

	db.add(contract)
	db.commit()
	db.refresh(contract)

	return UploadContractResponse(
		id=contract.id,
		title=contract.title,
		contractType=contract.contract_type,
		s3Key=contract.s3_key,
		createdAt=contract.created_at,
		message="Contract uploaded, extracted, classified, and saved.",
	)


@app.get("/contracts/search", response_model=ContractSearchResponse)
def search_contracts(
	title: str = "",
	limit: int = 25,
	db: Session = Depends(get_db),
):
	safe_limit = max(1, min(limit, 100))

	query = db.query(ContractMetadata)
	if title.strip():
		query = query.filter(ContractMetadata.title.ilike(f"%{title.strip()}%"))

	contracts = query.order_by(ContractMetadata.created_at.desc()).limit(safe_limit).all()

	return ContractSearchResponse(
		items=[
			ContractSearchItem(
				id=item.id,
				title=item.title,
				contractType=item.contract_type,
				s3Key=item.s3_key,
				createdAt=item.created_at,
			)
			for item in contracts
		]
	)


@app.get("/contracts/{contract_id}/download", response_model=DownloadContractResponse)
def get_contract_download_link(contract_id: str, db: Session = Depends(get_db)):
	if not S3_ENABLED:
		raise HTTPException(
			status_code=503,
			detail="Contract download is unavailable because S3 is disabled.",
		)

	contract = db.get(ContractMetadata, contract_id)
	if not contract:
		raise HTTPException(status_code=404, detail="Contract not found.")

	s3_client = get_s3_client()
	try:
		presigned_url = s3_client.generate_presigned_url(
			"get_object",
			Params={
				"Bucket": contract.s3_bucket,
				"Key": contract.s3_key,
			},
			ExpiresIn=PRESIGNED_URL_EXPIRY_SECONDS,
		)
	except Exception as error:
		raise HTTPException(status_code=502, detail=f"Failed to generate download URL: {error}")

	return DownloadContractResponse(
		contractId=contract.id,
		downloadUrl=presigned_url,
		expiresInSeconds=PRESIGNED_URL_EXPIRY_SECONDS,
	)


@app.post("/contracts/generate-pdf")
def generate_contract_pdf(payload: ContractRequest, db: Session = Depends(get_db)):
	reference_chunks = []
	try:
		reference_chunks = retrieve_same_type_chunks(payload, db)
	except Exception:
		# Fall back to base generation if retrieval is unavailable.
		reference_chunks = []

	prompt = build_prompt_with_references(payload, reference_chunks)
	contract_text = generate_contract_text(prompt)
	pdf_bytes = build_contract_pdf(payload.name, contract_text)

	safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", payload.name).strip("_") or "contract"
	file_name = f"{safe_name}.pdf"

	return StreamingResponse(
		io.BytesIO(pdf_bytes),
		media_type="application/pdf",
		headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
	)


@app.post("/chatbot/query", response_model=ChatbotResponse)
def query_chatbot(payload: ChatbotRequest, db: Session = Depends(get_db)):
	api_key = os.getenv("GEMINI_API_KEY")
	if not api_key:
		raise HTTPException(
			status_code=500,
			detail="Missing GEMINI_API_KEY environment variable on the backend.",
		)

	question = payload.question.strip()
	if not question:
		raise HTTPException(status_code=400, detail="Question is required.")

	context, references = fetch_contract_context_for_chat(question, db)
	prompt = build_chatbot_prompt(question, context)

	client = genai.Client(api_key=api_key)
	try:
		response = client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
	except Exception as error:
		raise HTTPException(status_code=502, detail=f"Chatbot generation failed: {error}")

	answer = (response.text or "").strip()
	if not answer:
		raise HTTPException(status_code=502, detail="Gemini returned an empty chatbot response.")

	return ChatbotResponse(answer=answer, references=references[:4])


