from fastapi import FastAPI, Request
from pydantic import BaseModel
from openai import OpenAI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os

app = FastAPI()

# ✅ Ограниченный CORS (можно позже сузить до домена)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://bizpartner.pl", "https://www.bizpartner.pl"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

class ChatRequest(BaseModel):
    message: str

@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a helpful assistant specializing in accounting and legal services in Poland."},
                {"role": "user", "content": req.message}
            ]
        )
        return {"reply": response.choices[0].message.content}
    except Exception as e:
        return {"reply": f"⚠️ Ошибка: {str(e)}"}

# ✅ Обработка preflight-запроса от браузера
@app.options("/chat")
async def preflight_handler(request: Request):
    return JSONResponse(
        content={},
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": request.headers.get("origin") or "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )
