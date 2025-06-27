from fastapi import FastAPI, Request
from pydantic import BaseModel
from openai import OpenAI
from fastapi.responses import JSONResponse
import os

app = FastAPI()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
    print(f"Origin received: '{origin}' - Allowed: {origin in ALLOWED_ORIGINS}")
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

class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    data = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system",
             "content": "You are a helpful assistant…"},
            {"role": "user", "content": req.message}]
    )
    
    origin = request.headers.get("origin", "")
    cors_headers = get_cors_headers(origin)
    
    return JSONResponse(
        {"reply": data.choices[0].message.content},
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
