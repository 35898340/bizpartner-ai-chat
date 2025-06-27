from fastapi import FastAPI, Request
from pydantic import BaseModel
from openai import OpenAI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os

app = FastAPI()

# ✅ Временно разрешаем все домены (для отладки CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ← заменим позже на твой домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🔑 Ключ OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# 📥 Входящие данные
class ChatRequest(BaseModel):
    message: str

# 💬 Обработка POST-запроса
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

# ⚙️ Обработка preflight-запроса (OPTIONS /chat)
@app.options("/chat")
async def options_handler(request: Request):
    return JSONResponse(content={}, status_code=204)
