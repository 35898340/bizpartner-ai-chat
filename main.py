# ────────────────────────────────────────────────────────────────────────────
#  BizPartner-AI · FastAPI + OpenAI Assistants  (рабочая «базовая» версия)
# ────────────────────────────────────────────────────────────────────────────
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel
from openai import OpenAI
import os, time, json, asyncio, csv, io
import requests
from datetime import datetime, timezone
from typing import Optional

# ── Persistence (SQLAlchemy) ───────────────────────────────────────────────
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime, ForeignKey, JSON as SA_JSON, func
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

DEBUG = os.getenv("DEBUG", "0") in {"1", "true", "True", "yes", "on"}

app = FastAPI()

# ── OpenAI ────────────────────────────────────────────────────────────────
client       = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ASSISTANT_ID = os.getenv("ASSISTANT_ID")            # Railway → Variables
BITRIX_WEBHOOK_URL = os.getenv("BITRIX_WEBHOOK_URL") or os.getenv("BITRIX_WEBHOOK")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN")

# ── DB setup ───────────────────────────────────────────────────────────────
Base = declarative_base()
engine = create_engine(DATABASE_URL, pool_pre_ping=True) if DATABASE_URL else None
SessionLocal = sessionmaker(bind=engine) if engine else None

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(Integer, primary_key=True)
    thread_id = Column(String(128), unique=True, index=True, nullable=False)
    lead_id = Column(Integer, nullable=True)
    origin = Column(String(256), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=False, index=True)
    role = Column(String(32), nullable=False)  # user | assistant | tool
    content = Column(Text, nullable=False)
    tool_name = Column(String(128), nullable=True)
    tool_args = Column(SA_JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    conversation = relationship("Conversation", back_populates="messages")

if engine:
    try:
        Base.metadata.create_all(engine)
        if DEBUG:
            print("[db] Tables ensured")
    except Exception as _e:
        if DEBUG:
            print(f"[db] init error: {_e}")

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

# ── DB helpers ─────────────────────────────────────────────────────────────

def _db_session():
    if not SessionLocal:
        return None
    return SessionLocal()

def _get_or_create_conversation(session, thread_id: str, origin: Optional[str], lead_id: Optional[int] = None) -> Conversation:
    conv = session.query(Conversation).filter_by(thread_id=thread_id).one_or_none()
    if conv is None:
        conv = Conversation(thread_id=thread_id, origin=origin, lead_id=lead_id)
        session.add(conv)
        session.commit()
        session.refresh(conv)
    else:
        if lead_id is not None and conv.lead_id is None:
            conv.lead_id = lead_id
            session.commit()
    return conv

def _save_message(thread_id: str, origin: Optional[str], role: str, content: str, *, tool_name: Optional[str] = None, tool_args: Optional[dict] = None, lead_id: Optional[int] = None) -> None:
    session = _db_session()
    if not session:
        return
    try:
        conv = _get_or_create_conversation(session, thread_id, origin, lead_id)
        msg = Message(
            conversation_id=conv.id,
            role=role,
            content=content or "",
            tool_name=tool_name,
            tool_args=tool_args,
        )
        session.add(msg)
        session.commit()
    except Exception as e:
        if DEBUG:
            print(f"[db] save_message error: {e}")
    finally:
        session.close()

def _parse_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    val = value.strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    return None

def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

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
        # Persist user message
        try:
            _save_message(thread_id, origin, role="user", content=req.message)
        except Exception:
            pass

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

                    out: dict
                    try:
                        if fn_name in {"create_bitrix_lead", "crm_create_lead", "create_lead"}:
                            lead_id_val = create_bitrix_lead(fn_args)
                            last_lead_id = lead_id_val
                            out = {"ok": True, "lead_id": lead_id_val}
                            # Persist tool call
                            try:
                                _save_message(
                                    thread_id, origin, role="tool",
                                    content=json.dumps(out),
                                    tool_name=fn_name, tool_args=fn_args, lead_id=lead_id_val,
                                )
                            except Exception:
                                pass
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
        # Persist assistant reply
        try:
            _save_message(thread_id, origin, role="assistant", content=reply, lead_id=last_lead_id)
        except Exception:
            pass

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

# ── Admin helpers ──────────────────────────────────────────────────────────

def _require_admin(request: Request):
    if not ADMIN_TOKEN:
        raise PermissionError("ADMIN_TOKEN is not configured")
    token = request.headers.get("x-admin-token") or request.headers.get("X-Admin-Token")
    if token != ADMIN_TOKEN:
        raise PermissionError("unauthorized")

# ── Admin endpoints (read-only) ────────────────────────────────────────────

@app.get("/admin/conversations")
async def admin_list_conversations(request: Request):
    try:
        _require_admin(request)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    if not SessionLocal:
        return JSONResponse({"error": "DATABASE_URL is not configured"}, status_code=501)

    limit_param = request.query_params.get("limit", "50")
    offset_param = request.query_params.get("offset", "0")
    from_param = request.query_params.get("from")
    to_param = request.query_params.get("to")
    origin_param = request.query_params.get("origin")  # exact or suffix with leading *
    has_lead_param = request.query_params.get("has_lead")
    thread_param = request.query_params.get("thread_id")
    sort_by = request.query_params.get("sort_by", "created_at")  # created_at|id
    sort_dir = request.query_params.get("sort_dir", "desc")       # asc|desc

    try:
        limit = max(1, min(200, int(limit_param)))
        offset = max(0, int(offset_param))
    except Exception:
        limit, offset = 50, 0

    dt_from = _parse_dt(from_param)
    dt_to = _parse_dt(to_param)
    has_lead = _parse_bool(has_lead_param)

    session = SessionLocal()
    try:
        q = session.query(Conversation)
        if dt_from:
            q = q.filter(Conversation.created_at >= dt_from)
        if dt_to:
            q = q.filter(Conversation.created_at <= dt_to)
        if thread_param:
            q = q.filter(Conversation.thread_id == thread_param)
        if origin_param:
            if origin_param.startswith("*"):
                q = q.filter(Conversation.origin.ilike(f"%{origin_param[1:]}"))
            else:
                q = q.filter(Conversation.origin == origin_param)
        if has_lead is True:
            q = q.filter(Conversation.lead_id.isnot(None))
        elif has_lead is False:
            q = q.filter(Conversation.lead_id.is_(None))

        # sort
        order_col = Conversation.created_at if sort_by == "created_at" else Conversation.id
        if sort_dir == "asc":
            q = q.order_by(order_col.asc())
        else:
            q = q.order_by(order_col.desc())

        total = q.count()
        conversations = q.offset(offset).limit(limit).all()
        # augment with messages_count and last_message_at
        conv_ids = [c.id for c in conversations]
        stats = {}
        if conv_ids:
            mstats = (
                session.query(Message.conversation_id, func.count(Message.id), func.max(Message.created_at))
                .filter(Message.conversation_id.in_(conv_ids))
                .group_by(Message.conversation_id)
                .all()
            )
            for cid, cnt, last_dt in mstats:
                stats[cid] = {"messages_count": int(cnt), "last_message_at": last_dt.isoformat() if last_dt else None}

        items = []
        for c in conversations:
            s = stats.get(c.id, {"messages_count": 0, "last_message_at": None})
            items.append({
                "id": c.id,
                "thread_id": c.thread_id,
                "lead_id": c.lead_id,
                "origin": c.origin,
                "created_at": c.created_at.isoformat() if c.created_at else None,
                "messages_count": s["messages_count"],
                "last_message_at": s["last_message_at"],
            })
        return JSONResponse({"total": total, "limit": limit, "offset": offset, "items": items})
    finally:
        session.close()

@app.get("/admin/conversations/{conversation_id}/messages")
async def admin_get_conversation_messages(conversation_id: int, request: Request):
    try:
        _require_admin(request)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    if not SessionLocal:
        return JSONResponse({"error": "DATABASE_URL is not configured"}, status_code=501)

    session = SessionLocal()
    try:
        conv = session.query(Conversation).filter_by(id=conversation_id).one_or_none()
        if not conv:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        msgs = (
            session.query(Message)
            .filter_by(conversation_id=conv.id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .all()
        )
        items = []
        for m in msgs:
            items.append({
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
                "tool_args": m.tool_args,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            })
        return JSONResponse({
            "conversation": {
                "id": conv.id,
                "thread_id": conv.thread_id,
                "lead_id": conv.lead_id,
                "origin": conv.origin,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
            },
            "messages": items,
        })
    finally:
        session.close()

@app.get("/admin/threads/{thread_id}/messages")
async def admin_get_thread_messages(thread_id: str, request: Request):
    try:
        _require_admin(request)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    if not SessionLocal:
        return JSONResponse({"error": "DATABASE_URL is not configured"}, status_code=501)

    session = SessionLocal()
    try:
        conv = session.query(Conversation).filter_by(thread_id=thread_id).one_or_none()
        if not conv:
            return JSONResponse({"error": "conversation not found"}, status_code=404)
        msgs = (
            session.query(Message)
            .filter_by(conversation_id=conv.id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .all()
        )
        items = []
        for m in msgs:
            items.append({
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
                "tool_args": m.tool_args,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            })
        return JSONResponse({
            "conversation": {
                "id": conv.id,
                "thread_id": conv.thread_id,
                "lead_id": conv.lead_id,
                "origin": conv.origin,
                "created_at": conv.created_at.isoformat() if conv.created_at else None,
            },
            "messages": items,
        })
    finally:
        session.close()

@app.get("/admin/messages")
async def admin_list_messages(request: Request):
    try:
        _require_admin(request)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    if not SessionLocal:
        return JSONResponse({"error": "DATABASE_URL is not configured"}, status_code=501)

    qp = request.query_params
    limit_param = qp.get("limit", "100")
    offset_param = qp.get("offset", "0")
    roles_param = qp.get("role")  # e.g. user,assistant,tool
    from_param = qp.get("from")
    to_param = qp.get("to")
    lead_param = qp.get("lead_id")
    has_lead_param = qp.get("has_lead")
    thread_param = qp.get("thread_id")
    origin_param = qp.get("origin")
    tool_param = qp.get("tool_name")
    search_param = qp.get("search")
    sort_by = qp.get("sort_by", "created_at")   # created_at|id
    sort_dir = qp.get("sort_dir", "desc")       # asc|desc

    try:
        limit = max(1, min(500, int(limit_param)))
        offset = max(0, int(offset_param))
    except Exception:
        limit, offset = 100, 0

    dt_from = _parse_dt(from_param)
    dt_to = _parse_dt(to_param)
    has_lead = _parse_bool(has_lead_param)

    roles = None
    if roles_param:
        roles = [r.strip() for r in roles_param.split(',') if r.strip()]

    session = SessionLocal()
    try:
        q = session.query(Message, Conversation).join(Conversation, Message.conversation_id == Conversation.id)
        if roles:
            q = q.filter(Message.role.in_(roles))
        if dt_from:
            q = q.filter(Message.created_at >= dt_from)
        if dt_to:
            q = q.filter(Message.created_at <= dt_to)
        if lead_param:
            try:
                lead_val = int(lead_param)
                q = q.filter(Conversation.lead_id == lead_val)
            except Exception:
                pass
        if has_lead is True:
            q = q.filter(Conversation.lead_id.isnot(None))
        elif has_lead is False:
            q = q.filter(Conversation.lead_id.is_(None))
        if thread_param:
            q = q.filter(Conversation.thread_id == thread_param)
        if origin_param:
            if origin_param.startswith("*"):
                q = q.filter(Conversation.origin.ilike(f"%{origin_param[1:]}"))
            else:
                q = q.filter(Conversation.origin == origin_param)
        if tool_param:
            if tool_param == "*":
                q = q.filter(Message.tool_name.isnot(None))
            else:
                q = q.filter(Message.tool_name == tool_param)
        if search_param:
            q = q.filter(Message.content.ilike(f"%{search_param}%"))

        order_col = Message.created_at if sort_by == "created_at" else Message.id
        if sort_dir == "asc":
            q = q.order_by(order_col.asc())
        else:
            q = q.order_by(order_col.desc())

        total = q.count()
        rows = q.offset(offset).limit(limit).all()
        items = []
        for m, c in rows:
            items.append({
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "tool_name": m.tool_name,
                "tool_args": m.tool_args,
                "created_at": m.created_at.isoformat() if m.created_at else None,
                "conversation": {
                    "id": c.id,
                    "thread_id": c.thread_id,
                    "lead_id": c.lead_id,
                    "origin": c.origin,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                }
            })
        return JSONResponse({"total": total, "limit": limit, "offset": offset, "items": items})
    finally:
        session.close()

@app.get("/admin/export/messages.ndjson")
async def admin_export_messages_ndjson(request: Request):
    try:
        _require_admin(request)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    if not SessionLocal:
        return JSONResponse({"error": "DATABASE_URL is not configured"}, status_code=501)

    qp = request.query_params
    dt_from = _parse_dt(qp.get("from"))
    dt_to = _parse_dt(qp.get("to"))
    roles = [r.strip() for r in qp.get("role", "").split(',') if r.strip()] or None
    origin_param = qp.get("origin")
    thread_param = qp.get("thread_id")
    has_lead = _parse_bool(qp.get("has_lead"))
    lead_param = qp.get("lead_id")
    tool_param = qp.get("tool_name")
    search_param = qp.get("search")

    session = SessionLocal()

    def generate():
        try:
            q = session.query(Message, Conversation).join(Conversation, Message.conversation_id == Conversation.id)
            if roles:
                q = q.filter(Message.role.in_(roles))
            if dt_from:
                q = q.filter(Message.created_at >= dt_from)
            if dt_to:
                q = q.filter(Message.created_at <= dt_to)
            if origin_param:
                if origin_param.startswith("*"):
                    q = q.filter(Conversation.origin.ilike(f"%{origin_param[1:]}"))
                else:
                    q = q.filter(Conversation.origin == origin_param)
            if thread_param:
                q = q.filter(Conversation.thread_id == thread_param)
            if has_lead is True:
                q = q.filter(Conversation.lead_id.isnot(None))
            elif has_lead is False:
                q = q.filter(Conversation.lead_id.is_(None))
            if lead_param:
                try:
                    q = q.filter(Conversation.lead_id == int(lead_param))
                except Exception:
                    pass
            if tool_param:
                if tool_param == "*":
                    q = q.filter(Message.tool_name.isnot(None))
                else:
                    q = q.filter(Message.tool_name == tool_param)
            if search_param:
                q = q.filter(Message.content.ilike(f"%{search_param}%"))

            q = q.order_by(Message.created_at.asc(), Message.id.asc())
            for m, c in q.yield_per(1000):
                row = {
                    "id": m.id,
                    "role": m.role,
                    "content": m.content,
                    "tool_name": m.tool_name,
                    "tool_args": m.tool_args,
                    "created_at": m.created_at.isoformat() if m.created_at else None,
                    "conversation": {
                        "id": c.id,
                        "thread_id": c.thread_id,
                        "lead_id": c.lead_id,
                        "origin": c.origin,
                        "created_at": c.created_at.isoformat() if c.created_at else None,
                    }
                }
                yield json.dumps(row, ensure_ascii=False) + "\n"
        finally:
            session.close()

    return StreamingResponse(generate(), media_type="application/x-ndjson")

@app.get("/admin/export/messages.csv")
async def admin_export_messages_csv(request: Request):
    try:
        _require_admin(request)
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=401)

    if not SessionLocal:
        return JSONResponse({"error": "DATABASE_URL is not configured"}, status_code=501)

    qp = request.query_params
    dt_from = _parse_dt(qp.get("from"))
    dt_to = _parse_dt(qp.get("to"))
    roles = [r.strip() for r in qp.get("role", "").split(',') if r.strip()] or None
    origin_param = qp.get("origin")
    thread_param = qp.get("thread_id")
    has_lead = _parse_bool(qp.get("has_lead"))
    lead_param = qp.get("lead_id")
    tool_param = qp.get("tool_name")
    search_param = qp.get("search")

    session = SessionLocal()

    def generate():
        try:
            q = session.query(Message, Conversation).join(Conversation, Message.conversation_id == Conversation.id)
            if roles:
                q = q.filter(Message.role.in_(roles))
            if dt_from:
                q = q.filter(Message.created_at >= dt_from)
            if dt_to:
                q = q.filter(Message.created_at <= dt_to)
            if origin_param:
                if origin_param.startswith("*"):
                    q = q.filter(Conversation.origin.ilike(f"%{origin_param[1:]}"))
                else:
                    q = q.filter(Conversation.origin == origin_param)
            if thread_param:
                q = q.filter(Conversation.thread_id == thread_param)
            if has_lead is True:
                q = q.filter(Conversation.lead_id.isnot(None))
            elif has_lead is False:
                q = q.filter(Conversation.lead_id.is_(None))
            if lead_param:
                try:
                    q = q.filter(Conversation.lead_id == int(lead_param))
                except Exception:
                    pass
            if tool_param:
                if tool_param == "*":
                    q = q.filter(Message.tool_name.isnot(None))
                else:
                    q = q.filter(Message.tool_name == tool_param)
            if search_param:
                q = q.filter(Message.content.ilike(f"%{search_param}%"))

            q = q.order_by(Message.created_at.asc(), Message.id.asc())

            # CSV header
            header = [
                "msg_id", "role", "content", "tool_name", "tool_args", "msg_created_at",
                "conv_id", "thread_id", "lead_id", "origin", "conv_created_at"
            ]
            sio = io.StringIO()
            writer = csv.writer(sio)
            writer.writerow(header)
            yield sio.getvalue()
            sio.seek(0)
            sio.truncate(0)

            for m, c in q.yield_per(1000):
                row = [
                    m.id, m.role, m.content, m.tool_name, json.dumps(m.tool_args, ensure_ascii=False) if m.tool_args else "",
                    m.created_at.isoformat() if m.created_at else "",
                    c.id, c.thread_id, c.lead_id if c.lead_id is not None else "", c.origin,
                    c.created_at.isoformat() if c.created_at else "",
                ]
                writer.writerow(row)
                yield sio.getvalue()
                sio.seek(0)
                sio.truncate(0)
        finally:
            session.close()

    return StreamingResponse(generate(), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=messages.csv"
    })
