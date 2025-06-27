# ────────────────────────────────────────────────────────────────────────────
#  BizPartner-AI · FastAPI + OpenAI Assistants · Tracing ON
# ────────────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from openai import OpenAI
import os, time, traceback, sys

app = FastAPI()

# ── OpenAI ───────────────────────────────────────────────────────────────
client = OpenAI(
    api_key        = os.getenv("OPENAI_API_KEY"),
    enable_tracing = True                       # ← присылать события в Traces
)
ASSISTANT_ID = os.getenv("ASSISTANT_ID")        # Railway → Variables

# ── CORS ─────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = {
    "https://bizpartner.pl",     "https://www.bizpartner.pl",
    "http://bizpartner.pl",      "http://www.bizpartner.pl",
    "https://lovable.dev",       "https://lovable.io",   "https://lovable.app",
    "http://localhost:3000",     "http://localhost:5173",
    "http://127.0.0.1:3000",     "http://127.0.0.1:5173",
}

def cors_headers(origin: str) -> dict:
    """CORS-заголовки ответа."""
    allow = origin if origin in ALLOWED_ORIGINS else "*"
    return {
        "Access-Control-Allow-Origin":      allow,
        "Access-Control-Allow-Methods":     "POST, OPTIONS",
        "Access-Control-Allow-Headers":     "Content-Type, Authorization",
        "Access-Control-Allow-Credentials": "true" if allow != "*" else "false",
    }

# ── In-memory хранилище lead_id ↔ thread_id ────────────────────────────
lead_threads: dict[str, str] = {}

# ── Schema входящего JSON ───────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    lead_id: str | None = None

# ── POST /chat ───────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors_headers(origin)

    try:
        # 1. Thread для клиента
        thread_id = lead_threads.get(req.lead_id) if req.lead_id else None
        if not thread_id:
            thread_id = client.beta.threads.create().id
            if req.lead_id:
                lead_threads[req.lead_id] = thread_id

        # 2. Сообщение пользователя
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=req.message
        )

        # 3. Запуск ассистента
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )

        # 4. Ожидание выполнения
        while True:
            run = client.beta.threads.runs.retrieve(
                run_id   = run.id,           # ← обязательно именованные арг-ты
                thread_id=thread_id
            )
            if run.status == "completed":
                break
            if run.status in {"failed", "cancelled"}:
                raise RuntimeError(f"Run {run.id} ended with status {run.status}")
            time.sleep(1)

        # 5. Ответ ассистента
        messages = client.beta.threads.messages.list(thread_id, order="desc")
        reply = messages.data[0].content[0].text.value

        return JSONResponse({"reply": reply}, headers=headers)

    except Exception as e:
        traceback.print_exc(file=sys.stderr)     # увидеть 500-ошибку в логах
        return JSONResponse({"error": str(e)}, status_code=500, headers=headers)

# ── OPTIONS /chat (pre-flight) ───────────────────────────────────────────
@app.options("/chat")
async def chat_options(request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors_headers(origin) | {"Access-Control-Max-Age": "86400"}
    return JSONResponse({}, status_code=204, headers=headers)
