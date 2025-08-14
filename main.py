# ────────────────────────────────────────────────────────────────────────────
#  BizPartner-AI · FastAPI + OpenAI Assistants  (рабочая «базовая» версия)
# ────────────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from openai import OpenAI
import os, time, json
import requests

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

def cors_headers(origin: str) -> dict:
    allow = origin if origin in ALLOWED_ORIGINS else "*"
    return {
        "Access-Control-Allow-Origin":      allow,
        "Access-Control-Allow-Methods":     "POST, OPTIONS",
        "Access-Control-Allow-Headers":     "Content-Type, Authorization",
        "Access-Control-Allow-Credentials": "true" if allow != "*" else "false",
    }

# ── In-memory :  lead_id ↔ thread_id ───────────────────────────────────────
lead_threads: dict[str, str] = {}

# ── Bitrix24 helpers ───────────────────────────────────────────────────────
def _bitrix_call(method: str, payload: dict) -> dict:
    if not BITRIX_WEBHOOK_URL:
        raise RuntimeError("Bitrix24 webhook URL is not configured. Set BITRIX_WEBHOOK_URL env var.")
    url = f"{BITRIX_WEBHOOK_URL.rstrip('/')}/{method}.json"
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
        # 1. thread для клиента
        thread_id = lead_threads.get(req.lead_id) if req.lead_id else None
        if not thread_id:
            thread_id = client.beta.threads.create().id
            if req.lead_id:
                lead_threads[req.lead_id] = thread_id

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

        last_lead_id: int | None = None
        while True:
            run_status = client.beta.threads.runs.retrieve(
                run_id=run.id,
                thread_id=thread_id
            )

            if run_status.status == "requires_action":
                tool_calls = run_status.required_action.submit_tool_outputs.tool_calls
                tool_outputs = []
                for tool_call in tool_calls:
                    fn_name = tool_call.function.name
                    try:
                        fn_args = json.loads(tool_call.function.arguments or "{}")
                    except Exception:
                        fn_args = {}

                    if fn_name in {"create_bitrix_lead", "crm_create_lead", "create_lead"}:
                        lead_id = create_bitrix_lead(fn_args)
                        last_lead_id = lead_id
                        tool_outputs.append({
                            "tool_call_id": tool_call.id,
                            "output": json.dumps({"ok": True, "lead_id": lead_id})
                        })
                    else:
                        tool_outputs.append({
                            "tool_call_id": tool_call.id,
                            "output": json.dumps({"ok": False, "error": f"unknown function: {fn_name}"})
                        })

                client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread_id,
                    run_id=run_status.id,
                    tool_outputs=tool_outputs
                )
                continue

            if run_status.status == "completed":
                break
            if run_status.status in {"failed", "cancelled", "expired"}:
                raise RuntimeError(f"Run {run.id} ended with {run_status.status}")
            time.sleep(1)

        # 5. ответ ассистента
        messages = client.beta.threads.messages.list(thread_id, order="desc")
        reply = messages.data[0].content[0].text.value

        resp = {"reply": reply}
        if last_lead_id is not None:
            resp["lead_id"] = last_lead_id
        return JSONResponse(resp, headers=headers)

    except Exception as e:
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
            headers=headers
        )

# ── OPTIONS /chat (CORS pre-flight) ───────────────────────────────────────
@app.options("/chat")
async def chat_options(request: Request):
    origin  = request.headers.get("origin", "")
    headers = cors_headers(origin) | {"Access-Control-Max-Age": "86400"}
    return JSONResponse({}, status_code=204, headers=headers)
