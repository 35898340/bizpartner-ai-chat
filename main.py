from fastapi import FastAPI, Request
from pydantic import BaseModel
from openai import OpenAI
from fastapi.responses import JSONResponse
import os

app = FastAPI()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
    return JSONResponse(
        {"reply": data.choices[0].message.content},
        headers={
            "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )

@app.options("/chat")
async def chat_options(request: Request):
    return JSONResponse(
        {},
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": request.headers.get("origin", "*"),
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )
