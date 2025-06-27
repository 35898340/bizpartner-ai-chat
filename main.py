# ────────────────────────────────────────────────────────────────────────────
#  BizPartner-AI   ·   FastAPI + Assistants   ·   «логируем в Deploy Logs»
# ────────────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from openai import OpenAI
import os, time, json, datetime, sys         # ← + json, datetime

app = FastAPI()

client       = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

ALLOWED_ORIGINS = {  # … как было … 
}

def cors_headers(origin: str) -> dict:  # … как было …
    ...

lead_threads: dict[str, str] = {}

class ChatRequest(BaseModel):
    message: str
    lead_id: str | None = None

@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors_headers(origin)

    try:
        # 1-3.  thread → run  (код не менялся)
        ...

        # 4. ждём run
        ...

        # 5. ответ
        messages = client.beta.threads.messages.list(thread_id, order="desc")
        reply = messages.data[0].content[0].text.value

        # ── *** НОВОЕ: выводим JSON-строку в логи Railway *** ───────────
        log_line = {
            "ts"      : datetime.datetime.utcnow().isoformat(timespec="seconds"),
            "lead_id" : req.lead_id,
            "user"    : req.message,
            "reply"   : reply
        }
        print("CHAT_LOG", json.dumps(log_line, ensure_ascii=False), flush=True)
        # ────────────────────────────────────────────────────────────────

        return JSONResponse({"reply": reply}, headers=headers)

    except Exception as e:
        # чтобы видеть stacktrace
        print("ERROR", e, file=sys.stderr, flush=True)
        return JSONResponse({"error": str(e)}, 500, headers=headers)

@app.options("/chat")
async def chat_options(request: Request):
    origin  = request.headers.get("origin", "")
    return JSONResponse({}, 204, cors_headers(origin) | {"Access-Control-Max-Age": "86400"})
