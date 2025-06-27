from fastapi import FastAPI, Request
from pydantic import BaseModel
from fastapi.responses import JSONResponse
from openai import OpenAI
import os, time

app = FastAPI()

# --- OpenAI ---
client        = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID  = os.getenv("ASSISTANT_ID")  # обязательно есть в Railway Vars

# --- Разрешённые домены ---
ALLOWED_ORIGINS = {
    "https://bizpartner.pl",
    "https://www.bizpartner.pl",
    "http://bizpartner.pl",
    "http://www.bizpartner.pl",
    "https://lovable.dev",
    "https://lovable.io",
    "https://lovable.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
}

def cors_headers(origin: str) -> dict:
    if origin in ALLOWED_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Credentials": "true",
        }
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
    }

# --- Pydantic ---
class ChatRequest(BaseModel):
    message : str
    lead_id : str | None = None

# --- Хранилище тредов (memory) ---
lead_threads: dict[str,str] = {}

# ----------  POST  /chat  ----------
@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    origin = request.headers.get("origin", "")
    headers = cors_headers(origin)

    try:
        # 1️⃣ Получаем / создаём thread
        thread_id = lead_threads.get(req.lead_id) if req.lead_id else None
        if not thread_id:
            thread_id = client.beta.threads.create().id
            if req.lead_id:
                lead_threads[req.lead_id] = thread_id

        # 2️⃣ Добавляем сообщение пользователя
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=req.message
        )

        # 3️⃣ Запускаем ассистента
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID,
        )

        # 4️⃣ Ждём завершения
        while True:
            run = client.beta.threads.runs.retrieve(thread_id, run.id)
            if run.status == "completed":
                break
            if run.status in {"failed", "cancelled"}:
                raise RuntimeError(f"Run {run.id} ended with status {run.status}")
            time.sleep(1)

        # 5️⃣ Получаем последнее assistant-сообщение
        messages = client.beta.threads.messages.list(thread_id, order="desc")
        assistant_reply = next(
            (m.content[0].text.value for m in messages.data if m.role == "assistant"),
            "⚠️ Ассистент не ответил."
        )

        return JSONResponse({"reply": assistant_reply}, headers=headers)

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500, headers=headers)

# ----------  OPTIONS  /chat  ----------
@app.options("/chat")
async def options_chat(request: Request):
    origin = request.headers.get("origin", "")
    headers = cors_headers(origin) | {"Access-Control-Max-Age": "86400"}
    return JSONResponse({}, status_code=204, headers=headers)
