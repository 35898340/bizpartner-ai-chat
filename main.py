# ────────────────────────────────────────────────────────────────────────────
#  BizPartner-AI · FastAPI + OpenAI Assistants  (рабочая «базовая» версия)
# ────────────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from openai import OpenAI
import os, time, json, asyncio
import requests

DEBUG = os.getenv("DEBUG", "0") in {"1", "true", "True", "yes", "on"}

app = FastAPI()

# ── OpenAI ────────────────────────────────────────────────────────────────
client       = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")            # Railway → Variables
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL") or os.getenv("BITRIX_WEBHOOK")

# ── CORS ──────────────────────────────────────────────────────────────────
ALLOWED_ORIGINS = {
    "https://bizpartner.pl",     "https://www.bizpartner.pl",
    "http://bizpartner.pl",      "http://www.bizpartner.pl",
    "https://lovable.dev",       "https://lovable.io",   "https://lovable.app",
    "http://localhost:3000",     "http://localhost:5173",
    "http://127.0.0.1:3000",     "http://127.0.0.1:5173",
}
ALLOWED_SUFFIXES = (
    ".lovable.dev", ".lovable.io", ".lovable.app",
    ".bizpartner.pl",
)

def _is_allowed_origin(origin: str) -> bool:
    if not origin:
        return False
    o = origin.lower()
    if o in ALLOWED_ORIGINS:
        return True
    return any(o.endswith(suffix) for suffix in ALLOWED_SUFFIXES)

def cors_headers(origin: str) -> dict:
    allow_origin = origin if _is_allowed_origin(origin) else "*"
    return {
        "Access-Control-Allow-Origin":      allow_origin,
        "Access-Control-Allow-Methods":     "POST, OPTIONS",
        "Access-Control-Allow-Headers":     "Content-Type, Authorization, X-Requested-With",
        "Access-Control-Allow-Credentials": "true" if allow_origin != "*" else "false",
        "Vary": "Origin",
    }

# ── In-memory :  lead_id ↔ thread_id ───────────────────────────────────────
lead_threads: dict[str, str] = {}

# ── Bitrix24 helpers ───────────────────────────────────────────────────────
def _bitrix_call(method: str, payload: dict) -> dict:
    if not BITRIX_WEBHOOK_URL:
        raise RuntimeError("Bitrix24 webhook URL is not configured. Set BITRIX_WEBHOOK_URL env var.")
    base = BITRIX_WEBHOOK_URL.rstrip('/')
    # Accept both base webhook URL and a full method endpoint that already ends with .json
    if base.endswith('.json'):
        url = base
    else:
        url = f"{base}/{method}.json"
    resp = requests.post(url, json=payload, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(f"Bitrix24 error: {data.get('error_description') or data.get('error')}")
    return data.get("result", data)

def create_bitrix_lead(args: dict) -> int:
    title = args.get("title") or args.get("deal_title") or "Website Chat Lead"
    first_name = args.get("first_name") or args.get("name") or ""
    last_name = args.get("last_name") or args.get("surname") or ""
    phone = args.get("phone") or args.get("phone_number")
    email = args.get("email")
    comments = (
        args.get("comment") or args.get("comments") or args.get("note") or args.get("notes") or ""
    )
    source_id = args.get("source_id") or "WEB"
    assigned_by_id = args.get("assigned_by_id")

    fields = {
        "TITLE": title,
        "NAME": first_name,
        "LAST_NAME": last_name,
        "COMMENTS": comments,
        "SOURCE_ID": source_id,
        "OPENED": "Y",
    }
    if phone:
        fields["PHONE"] = [{"VALUE": str(phone), "TYPE": "WORK"}]
    if email:
        fields["EMAIL"] = [{"VALUE": str(email), "TYPE": "WORK"}]
    if assigned_by_id:
        fields["ASSIGNED_BY_ID"] = assigned_by_id

    result = _bitrix_call("crm.lead.add", {"fields": fields, "params": {"REGISTER_SONET_EVENT": "Y"}})
    if isinstance(result, int):
        return result
    # sometimes Bitrix returns {"result": <id>} handled in _bitrix_call, but keep a safeguard
    return int(result)

def _extract_last_text_message(client: OpenAI, thread_id: str) -> str:
    messages = client.beta.threads.messages.list(thread_id, order="desc")
    for message in messages.data:
        if getattr(message, "role", None) != "assistant":
            continue
        for part in getattr(message, "content", []):
            if getattr(part, "type", None) == "text" and getattr(part, "text", None):
                text_value = getattr(part.text, "value", None)
                if isinstance(text_value, str) and text_value.strip():
                    return text_value
    return ""

# ── Модель входящего запроса ──────────────────────────────────────────────
class ChatRequest(BaseModel):
    message: str
    lead_id: str | None = None          # используйте, если нужно «склеивать» диалог

# ── POST /chat ────────────────────────────────────────────────────────────
@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors_headers(origin)

    try:
        if DEBUG:
            print(f"[chat] origin={origin} lead_id_in={req.lead_id} message={req.message[:80]!r}")

        # 1. thread для клиента
        thread_id = lead_threads.get(req.lead_id) if req.lead_id else None
        if not thread_id:
            thread_id = client.beta.threads.create().id
            if req.lead_id:
                lead_threads[req.lead_id] = thread_id
        if DEBUG:
            print(f"[chat] thread_id={thread_id}")

        # 2. сообщение пользователя
        client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=req.message
        )

        # 3. запуск ассистента и обработка tool calls
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=ASSISTANT_ID
        )
        if DEBUG:
            print(f"[chat] run_id={run.id}")

        last_lead_id: int | None = None
        deadline = time.time() + 90  # fail-safe to avoid indefinite wait
        while True:
            if time.time() > deadline:
                raise TimeoutError("Assistant run timeout")

            run_status = client.beta.threads.runs.retrieve(
                run_id=run.id,
                thread_id=thread_id
            )
            if DEBUG:
                print(f"[chat] run_status={run_status.status}")

            if run_status.status == "requires_action":
                tool_calls = run_status.required_action.submit_tool_outputs.tool_calls
                if DEBUG:
                    print(f"[chat] requires_action: {len(tool_calls)} tool_calls")
                tool_outputs = []
                for tool_call in tool_calls:
                    fn_name = tool_call.function.name
                    try:
                        fn_args = json.loads(tool_call.function.arguments or "{}")
                    except Exception:
                        fn_args = {}
                    if DEBUG:
                        print(f"[chat] tool_call: {fn_name} args={fn_args}")

                    try:
                        if fn_name in {"create_bitrix_lead", "crm_create_lead", "create_lead"}:
                            lead_id = create_bitrix_lead(fn_args)
                            last_lead_id = lead_id
                            out = {"ok": True, "lead_id": lead_id}
                        else:
                            out = {"ok": False, "error": f"unknown function: {fn_name}"}
                    except Exception as tool_error:
                        out = {"ok": False, "error": str(tool_error)}

                    tool_outputs.append({
                        "tool_call_id": tool_call.id,
                        "output": json.dumps(out)
                    })

                client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread_id,
                    run_id=run_status.id,
                    tool_outputs=tool_outputs
                )
                if DEBUG:
                    print(f"[chat] submit_tool_outputs sent: {tool_outputs}")
                continue

            if run_status.status == "completed":
                if DEBUG:
                    print("[chat] run completed")
                break
            if run_status.status in {"failed", "cancelled", "expired"}:
                raise RuntimeError(f"Run {run.id} ended with {run_status.status}")
            await asyncio.sleep(1)

        # 5. ответ ассистента
        reply = _extract_last_text_message(client, thread_id) or ""
        if DEBUG:
            print(f"[chat] reply_len={len(reply)} last_lead_id={last_lead_id}")

        resp = {"reply": reply}
        if last_lead_id is not None:
            resp["lead_id"] = last_lead_id
        return JSONResponse(resp, headers=headers)

    except Exception as e:
        if DEBUG:
            print(f"[chat] error: {e}")
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
            headers=headers
        )

# ── OPTIONS /chat (CORS pre-flight) ───────────────────────────────────────
@app.options("/chat")
async def chat_options(request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors_headers(origin).copy()
    acrh = request.headers.get("access-control-request-headers")
    if acrh:
        headers["Access-Control-Allow-Headers"] = acrh
    headers["Access-Control-Max-Age"] = "86400"
    return Response(status_code=204, headers=headers)
