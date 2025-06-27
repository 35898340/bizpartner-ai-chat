from fastapi import FastAPI, Request
from pydantic import BaseModel
from openai import OpenAI
from fastapi.responses import JSONResponse
import os

app = FastAPI()

# ✅ Инициализация OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ✅ Структура запроса
class ChatRequest(BaseModel):
    message: str

# ✅ Обработка POST /chat
@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant specializing in accounting and legal services in Poland."
                },
                {
                    "role": "user",
                    "content": req.message
                }
            ]
        )
        return JSONResponse(
            status_code=200,
            content={"reply": response.choices[0].message.content},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            }
        )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"reply": f"⚠️ Ошибка: {str(e)}"},
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
            }
        )

# ✅ Обработка preflight (OPTIONS)
@app.options("/chat")
async def options_chat(request: Request):
    return JSONResponse(
        content={},
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )
