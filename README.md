# Smart-Contract-Engine1

## Chatbot-Only Testing (No AWS)

If AWS services are disabled or removed, you can still run and test the Gemini chatbot independently.

### 1. Backend environment

Set these variables in `Backend/.env`:

- `GEMINI_API_KEY=...`
- `DATABASE_URL=...` (optional; if omitted, backend falls back to `sqlite:///./local_dev.db`)

Do not set `S3_BUCKET_NAME` when AWS is unavailable. The backend will start in chatbot mode and return `503` only for upload/download endpoints.

### 2. Run backend

From `Backend/`:

```bash
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

### 3. Test chatbot UI only

Open `Frontend/chatbot-test.html` in your browser.

It sends requests to:

- `http://localhost:8001/chatbot/query`

You can also continue using the dashboard widget on `Frontend/dashboard.html`, but `chatbot-test.html` is isolated for chatbot verification.