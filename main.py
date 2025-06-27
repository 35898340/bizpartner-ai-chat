from fastapi import FastAPI, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from openai import OpenAI
import os, time, requests, json

app = FastAPI()

# ── OPENAI ────────────────────────────────────────────
client       = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

# ── Google Sheets Webhook (опционально) ───────────────
SHEETS_WEBHOOK_URL = os.getenv("SHEETS_WEBHOOK_URL")   # можно не задавать

# ── CORS ──────────────────────────────────────────────
ALLOWED_ORIGINS = {
    "https://bizpartner.pl",  "https://www.bizpartner.pl",
    "http://bizpartner.pl",   "http://www.bizpartner.pl",
    "https://lovable.dev",    "https://lovable.io",  "https://lovable.app",
    "http://localhost:3000",  "http://localhost:5173",
    "http://127.0.0.1:3000",  "http://127.0.0.1:5173",
}
def cors(origin: str) -> dict:
    allow = origin if origin in ALLOWED_ORIGINS else "*"
    return {
        "Access-Control-Allow-Origin":      allow,
        "Access-Control-Allow-Methods":     "POST, OPTIONS",
        "Access-Control-Allow-Headers":     "Content-Type, Authorization",
        "Access-Control-Allow-Credentials": "true" if allow != "*" else "false",
    }

# ── Модель запроса ───────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    lead_id: str | None = None          # чтобы «склеить» сообщения одного лида

# in-memory map  lead_id → thread_id
lead_threads: dict[str, str] = {}

# ── helpers ───────────────────────────────────────────
def push_to_sheets(payload: dict):
    """Отправляем JSON одной строкой в Google Sheets (Apps Script Webhook)."""
    if not SHEETS_WEBHOOK_URL:
        return
    try:
        requests.post(
            SHEETS_WEBHOOK_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            timeout=4,
        )
    except Exception:
        # Журналируем, но не роняем весь чаторбот
        pass

# ── POST /chat ───────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors(origin)

    try:
        # 1️⃣ thread для лида
        thread_id = lead_threads.get(req.lead_id) if req.lead_id else None
        if not thread_id:
            thread_id = client.beta.threads.create().id
            if req.lead_id:
                lead_threads[req.lead_id] = thread_id

        # 2️⃣ сообщение пользователя
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=req.message,
        )

        # 3️⃣ запуск ассистента
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID,
        )

        # 4️⃣ ждём завершения
        while True:
            run = client.beta.threads.runs.retrieve(thread_id, run.id)  # ← ✔️ только 2 аргумента
            if run.status == "completed":
                break
            if run.status in {"failed", "cancelled"}:
                raise RuntimeError(f"Run {run.id} ended with status {run.status}")
            time.sleep(1)

        # 5️⃣ берём ответ ассистента
        messages = client.beta.threads.messages.list(thread_id, order="desc")
        reply    = messages.data[0].content[0].text.value

        # 6️⃣ логируем в Google Sheets (если надо)
        push_to_sheets({
            "timestamp": time.time(),
            "lead_id":   req.lead_id,
            "question":  req.message,
            "answer":    reply,
            "thread_id": thread_id,
        })

        return JSONResponse({"reply": reply}, headers=headers)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500, headers=headers)

# ── OPTIONS /chat (pre-flight) ───────────────────────
@app.options("/chat")
async def chat_options(request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors(origin) | {"Access-Control-Max-Age": "86400"}
    return JSONResponse({}, status_code=204, headers=headers)
