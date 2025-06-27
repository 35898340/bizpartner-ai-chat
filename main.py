from fastapi import FastAPI
from pydantic import BaseModel
from openai import OpenAI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI()

# ✅ Разрешаем CORS (временно для всех доменов — можно заменить на свой позже)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # ← здесь можно поставить ["https://bizpartner.pl"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ✅ Инициализация OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ✅ Структура входящего запроса
class ChatRequest(BaseModel):
    message: str

# ✅ Основной эндпоинт
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
