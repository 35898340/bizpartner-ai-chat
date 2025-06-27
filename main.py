from fastapi import FastAPI, Request
from pydantic import BaseModel
from openai import OpenAI
from fastapi.responses import JSONResponse
import os
import time

app = FastAPI()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

ALLOWED_ORIGINS = [
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
    "http://127.0.0.1:5173"
]

def get_cors_headers(origin: str) -> dict:
    if origin in ALLOWED_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Credentials": "true",
        }
    else:
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Credentials": "false",
        }

# Временное хранилище соответствий lead_id ↔ thread_id
lead_threads = {}

class ChatRequest(BaseModel):
    message: str
    lead_id: str | None = None

@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    origin = request.headers.get("origin", "")
    cors_headers = get_cors_headers(origin)

    try:
        # Получаем или создаём thread для lead_id
        if req.lead_id and req.lead_id in lead_threads:
            thread_id = lead_threads[req.lead_id]
        else:
            thread = client.beta.threads.create()
            thread_id = thread.id
            if req.lead_id:
                lead_threads[req.lead_id] = thread_id

        # Добавляем сообщение пользователя в thread
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=req.message
        )

        # Запускаем ассистента
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )

        # Ждём завершения
        while True:
            run_status = client.beta.threads.runs.retrieve(thread_id, run.id)
            if run_status.status == "completed":
                break
            elif run_status.status in ["failed", "cancelled"]:
                raise Exception(f"Assistant run failed: {run_status.status}")
            time.sleep(1)

        # Получаем ответ ассистента
        messages = client.beta.threads.messages.list(thread_id, order="desc")
        reply = messages.data[0].content[0].text.value

        return JSONResponse({"reply": reply}, headers=cors_headers)

    except Exception as e:
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
            headers=cors_headers
        )

@app.options("/chat")
async def chat_options(request: Request):
    origin = request.headers.get("origin", "")
    cors_headers = get_cors_headers(origin)
    cors_headers["Access-Control-Max-Age"] = "86400"

    return JSONResponse(
        {},
        status_code=200,
        headers=cors_headers
    )
