# main.py  ─────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from openai import OpenAI
import requests, os, time, logging

# ── BASE CONFIG ───────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

app           = FastAPI()
client        = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID  = os.getenv("ASSISTANT_ID")          # задаётся в Railway → Variables
SHEETS_URL    = os.getenv("SHEETS_WEBHOOK_URL")    # Webhook Google-таблицы (опц.)

# ── CORS ───────────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = {
    "https://bizpartner.pl",  "https://www.bizpartner.pl",
    "http://bizpartner.pl",   "http://www.bizpartner.pl",
    "https://lovable.dev",    "https://lovable.io",   "https://lovable.app",
    "http://localhost:3000",  "http://localhost:5173",
    "http://127.0.0.1:3000",  "http://127.0.0.1:5173",
}

def cors_headers(origin: str) -> dict:
    allow = origin if origin in ALLOWED_ORIGINS else "*"
    return {
        "Access-Control-Allow-Origin":      allow,
        "Access-Control-Allow-Methods":     "POST, OPTIONS",
        "Access-Control-Allow-Headers":     "Content-Type, Authorization",
        "Access-Control-Allow-Credentials": "true" if allow != "*" else "false",
    }

# ── STORE lead_id ↔ thread_id (in-memory) ─────────────────────────────────────
lead_threads: dict[str, str] = {}

# ── PUSH TO SHEETS (необязательно; вызывается, если задан SHEETS_URL) ─────────
def push_to_sheets(lead_id: str | None, role: str, content: str) -> None:
    if not SHEETS_URL:
        return
    try:
        resp = requests.post(
            SHEETS_URL,
            json={"lead_id": lead_id, "role": role, "content": content},
            timeout=4,
        )
        logging.info("Sheets %s %s", resp.status_code, resp.text[:100])
    except requests.RequestException as e:
        logging.warning("Sheets push failed: %s", e)

# ── Pydantic-модель входящего запроса ─────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    lead_id: str | None = None   # чтобы «привязать» тред к конкретному ли́ду

# ── POST /chat ────────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    origin   = request.headers.get("origin", "")
    headers  = cors_headers(origin)

    try:
        # 1. получаем / создаём thread
        thread_id = lead_threads.get(req.lead_id) if req.lead_id else None
        if not thread_id:
            thread_id = client.beta.threads.create().id
            if req.lead_id:
                lead_threads[req.lead_id] = thread_id

        # 2. пишем сообщение пользователя
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=req.message
        )
        push_to_sheets(req.lead_id, "user", req.message)   # ← логируем

        # 3. запускаем ассистента
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )

        # 4. ждём завершения
        while True:
            run = client.beta.threads.runs.retrieve(thread_id, run.id)
            if run.status == "completed":
                break
            if run.status in {"failed", "cancelled"}:
                raise RuntimeError(f"Run {run.id} ended with status {run.status}")
            time.sleep(1)

        # 5. читаем ответ
        messages = client.beta.threads.messages.list(thread_id, order="desc")
        reply = messages.data[0].content[0].text.value
        push_to_sheets(req.lead_id, "assistant", reply)    # ← логируем

        return JSONResponse({"reply": reply}, headers=headers)

    except Exception as e:
        logging.exception("Chat error")
        return JSONResponse({"error": str(e)}, status_code=500, headers=headers)

# ── OPTIONS /chat  (pre-flight CORS) ──────────────────────────────────────────
@app.options("/chat")
async def chat_options(request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors_headers(origin) | {"Access-Control-Max-Age": "86400"}
    return JSONResponse({}, status_code=204, headers=headers)
