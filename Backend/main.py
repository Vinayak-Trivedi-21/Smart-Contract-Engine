import os
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import storage


load_dotenv(Path(__file__).with_name(".env"))

APP_NAME = os.getenv("APP_NAME", "Smart Contract Engine API")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

GCP_PROJECT_ID = os.getenv("GCP_PROJECT_ID", "")
GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")
MAKE_UPLOADED_FILES_PUBLIC = os.getenv("MAKE_UPLOADED_FILES_PUBLIC", "false").lower() == "true"
MAX_UPLOAD_SIZE_MB = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
ALLOWED_FILE_EXTENSIONS = {
    ext.strip().lower() for ext in os.getenv("ALLOWED_FILE_EXTENSIONS", "pdf,doc,docx,txt").split(",") if ext.strip()
}
SIGNED_URL_EXPIRY_SECONDS = int(os.getenv("SIGNED_URL_EXPIRY_SECONDS", "900"))

MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

app = FastAPI(title=APP_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def health_check() -> dict:
    return {"status": "ok", "service": APP_NAME}


@app.get("/contracts/health")
async def contracts_health_check() -> dict:
    if not GCS_BUCKET_NAME or GCS_BUCKET_NAME == "your-gcs-bucket-name":
        return {
            "status": "error",
            "contractsReady": False,
            "bucketConfigured": False,
            "bucketAccessible": False,
            "signedUrlAvailable": False,
            "message": "GCS_BUCKET_NAME is not configured.",
        }

    try:
        client = _get_storage_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        bucket_exists = bucket.exists()

        if not bucket_exists:
            return {
                "status": "error",
                "contractsReady": False,
                "bucketConfigured": True,
                "bucketAccessible": False,
                "signedUrlAvailable": False,
                "message": f"Bucket '{GCS_BUCKET_NAME}' does not exist or is not accessible.",
            }

        signed_url_available = True
        if not MAKE_UPLOADED_FILES_PUBLIC:
            try:
                probe_blob = bucket.blob("contracts/.health-check")
                probe_blob.generate_signed_url(
                    version="v4",
                    expiration=timedelta(seconds=60),
                    method="GET",
                )
            except Exception:
                signed_url_available = False

        return {
            "status": "ok",
            "contractsReady": MAKE_UPLOADED_FILES_PUBLIC or signed_url_available,
            "bucketConfigured": True,
            "bucketAccessible": True,
            "signedUrlAvailable": signed_url_available,
            "publicObjectsEnabled": MAKE_UPLOADED_FILES_PUBLIC,
            "bucketName": GCS_BUCKET_NAME,
            "message": "Contracts storage is reachable.",
        }
    except HTTPException as exc:
        return {
            "status": "error",
            "contractsReady": False,
            "bucketConfigured": True,
            "bucketAccessible": False,
            "signedUrlAvailable": False,
            "message": exc.detail,
        }
    except Exception as exc:
        return {
            "status": "error",
            "contractsReady": False,
            "bucketConfigured": True,
            "bucketAccessible": False,
            "signedUrlAvailable": False,
            "message": f"Contracts health check failed: {exc}",
        }


def _get_storage_client() -> storage.Client:
    try:
        if GCP_PROJECT_ID:
            return storage.Client(project=GCP_PROJECT_ID)
        return storage.Client()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not initialize Google Cloud Storage client: {exc}") from exc


def _build_open_url(blob: storage.Blob) -> str | None:
    if MAKE_UPLOADED_FILES_PUBLIC:
        return blob.public_url

    try:
        return blob.generate_signed_url(
            version="v4",
            expiration=timedelta(seconds=SIGNED_URL_EXPIRY_SECONDS),
            method="GET",
        )
    except Exception:
        return None


def _blob_to_contract_item(blob: storage.Blob) -> dict:
    file_name = Path(blob.name).name
    updated_at = blob.updated.isoformat() if blob.updated else None

    return {
        "id": blob.name,
        "fileName": file_name,
        "objectPath": f"gs://{GCS_BUCKET_NAME}/{blob.name}",
        "updatedAt": updated_at,
        "size": blob.size,
        "contentType": blob.content_type,
        "openUrl": _build_open_url(blob),
    }


@app.get("/contracts")
async def list_contracts(
    q: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> dict:
    if not GCS_BUCKET_NAME or GCS_BUCKET_NAME == "your-gcs-bucket-name":
        raise HTTPException(status_code=500, detail="GCS_BUCKET_NAME is not configured.")

    try:
        client = _get_storage_client()
        blobs = list(client.list_blobs(GCS_BUCKET_NAME, prefix="contracts/"))

        normalized_query = (q or "").strip().lower()
        matched_blobs = []

        for blob in blobs:
            if not blob.name or blob.name.endswith("/"):
                continue

            file_name = Path(blob.name).name.lower()
            object_path = f"gs://{GCS_BUCKET_NAME}/{blob.name}".lower()
            if normalized_query and normalized_query not in file_name and normalized_query not in object_path:
                continue

            matched_blobs.append(blob)

        matched_blobs.sort(
            key=lambda blob: blob.updated.timestamp() if blob.updated else 0,
            reverse=True,
        )
        total = len(matched_blobs)
        paged_blobs = matched_blobs[offset : offset + limit]

        return {
            "items": [_blob_to_contract_item(blob) for blob in paged_blobs],
            "total": total,
            "limit": limit,
            "offset": offset,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not list contracts: {exc}") from exc


@app.post("/contractUpload")
async def upload_contract(contractFile: UploadFile = File(...)) -> dict:
    if not GCS_BUCKET_NAME or GCS_BUCKET_NAME == "your-gcs-bucket-name":
        raise HTTPException(status_code=500, detail="GCS_BUCKET_NAME is not configured.")

    filename = contractFile.filename or "uploaded_contract"
    extension = Path(filename).suffix.lower().lstrip(".")

    if extension not in ALLOWED_FILE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_FILE_EXTENSIONS))
        raise HTTPException(status_code=400, detail=f"Invalid file type. Allowed extensions: {allowed}")

    contents = await contractFile.read()
    file_size = len(contents)

    if file_size == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if file_size > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File is too large. Maximum allowed size is {MAX_UPLOAD_SIZE_MB} MB.",
        )

    unique_name = f"contracts/{uuid4().hex}_{Path(filename).name}"

    try:
        client = _get_storage_client()
        bucket = client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(unique_name)

        blob.upload_from_string(contents, content_type=contractFile.content_type or "application/octet-stream")

        public_url = None
        if MAKE_UPLOADED_FILES_PUBLIC:
            blob.make_public()
            public_url = blob.public_url

        open_url = _build_open_url(blob)
        updated_at = blob.updated.isoformat() if blob.updated else None

        return {
            "message": "Contract uploaded successfully.",
            "id": unique_name,
            "fileName": filename,
            "objectPath": f"gs://{GCS_BUCKET_NAME}/{unique_name}",
            "updatedAt": updated_at,
            "openUrl": open_url,
            "publicUrl": public_url,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=APP_HOST, port=APP_PORT, reload=True)
