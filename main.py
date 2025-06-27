# main.py  ───────── BizPartner AI Chat + Google Sheets Log ─────────
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from openai import OpenAI
import os, time, requests, json, logging

app = FastAPI()

# ── OpenAI ─────────────────────────────────────────────────────────
client       = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")           # в Railway → Variables

# ── Webhook в Google Sheets (Apps Script) ─────────────────────────
SHEETS_URL   = os.getenv("SHEETS_WEBHOOK_URL")      # URL вида https://script.google.com/.../exec

def push_to_sheets(lead_id: str|None, role: str, content: str):
    """Отправляем одну реплику в таблицу"""
    if not SHEETS_URL:
        return
    payload = {
        "lead_id": lead_id,
        "messages": [
            { "role": role, "content": content }
        ]
    }
    try:
        r = requests.post(SHEETS_URL, json=payload, timeout=4)
        logging.info(f"Sheets {r.status_code}")
    except Exception as e:
        logging.warning("Sheets error: %s", e)

# ── CORS ───────────────────────────────────────────────────────────
ALLOWED_ORIGINS = {
    "https://bizpartner.pl",      "https://www.bizpartner.pl",
    "http://bizpartner.pl",       "http://www.bizpartner.pl",
    "https://lovable.dev",        "https://lovable.io",  "https://lovable.app",
    "http://localhost:3000",      "http://localhost:5173",
    "http://127.0.0.1:3000",      "http://127.0.0.1:5173",
}

def cors_headers(origin: str) -> dict:
    allow = origin if origin in ALLOWED_ORIGINS else "*"
    return {
        "Access-Control-Allow-Origin":      allow,
        "Access-Control-Allow-Methods":     "POST, OPTIONS",
        "Access-Control-Allow-Headers":     "Content-Type, Authorization",
        "Access-Control-Allow-Credentials": "true" if allow != "*" else "false",
    }

# ── Pydantic модель запроса ───────────────────────────────────────
class ChatRequest(BaseModel):
    message : str
    lead_id : str | None = None

# ── Хранилище lead_id ↔ thread_id (in-memory) ─────────────────────
lead_threads: dict[str, str] = {}

# ── POST /chat ─────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors_headers(origin)

    try:
        # 1) Получаем/создаём thread
        thread_id = lead_threads.get(req.lead_id) if req.lead_id else None
        if not thread_id:
            thread_id = client.beta.threads.create().id
            if req.lead_id:
                lead_threads[req.lead_id] = thread_id

        # 2) Логируем сообщение пользователя
        push_to_sheets(req.lead_id, "user", req.message)

        # 3) Добавляем сообщение пользователя в thread
        client.beta.threads.messages.create(
            thread_id = thread_id,
            role      = "user",
            content   = req.message,
        )

        # 4) Запускаем ассистента
        run = client.beta.threads.runs.create(
            thread_id   = thread_id,
            assistant_id= ASSISTANT_ID,
        )
        logging.info("🟢 run %s for thread %s", run.id, thread_id)

        # 5) Ждём завершения
        while True:
            run = client.beta.threads.runs.retrieve(
                thread_id = thread_id,
                run_id    = run.id,
            )
            if run.status == "completed":
                break
            if run.status in {"failed", "cancelled"}:
                raise RuntimeError(f"Run {run.id} ended with status {run.status}")
            time.sleep(1)

        # 6) Берём ответ ассистента
        messages = client.beta.threads.messages.list(thread_id, order="desc")
        reply    = messages.data[0].content[0].text.value

        # 7) Логируем ответ ассистента
        push_to_sheets(req.lead_id, "assistant", reply)

        return JSONResponse({"reply": reply}, headers=headers)

    except Exception as err:
        logging.error("chat error: %s", err, exc_info=True)
        return JSONResponse({"error": str(err)}, status_code=500, headers=headers)

# ── OPTIONS /chat ─────────────────────────────────────────────────
@app.options("/chat")
async def chat_options(request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors_headers(origin) | {"Access-Control-Max-Age": "86400"}
    return JSONResponse({}, status_code=204, headers=headers)
