from fastapi import FastAPI, Request
from pydantic import BaseModel
from openai import OpenAI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os

app = FastAPI()

# ✅ Разрешаем временно все домены — важно для теста
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ OpenAI клиент
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ✅ Структура запроса
class ChatRequest(BaseModel):
    message: str

# ✅ Основной чат-эндпоинт
@app.post("/chat")
async def chat(req: ChatRequest):
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant specializing in accounting and legal services in Poland. Answer briefly, clearly and professionally."
                },
                {
                    "role": "user",
                    "content": req.message
                }
            ]
        )
        return {"reply": response.choices[0].message.content}
    except Exception as e:
        return {"reply": f"⚠️ Ошибка: {str(e)}"}

# ✅ Обработка preflight-запроса от браузера
@app.options("/chat")
async def options_handler(request: Request):
    return JSONResponse(
        content={},
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )
