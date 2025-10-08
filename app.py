# app.py
from fastapi import FastAPI, WebSocket
from fastapi.responses import HTMLResponse
from kktcmb_worker import run_kktcmb
import json

app = FastAPI()

@app.get("/")
async def index():
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        prompt = await ws.receive_text()

        async def send_log(msg: str):
            await ws.send_text(json.dumps({"type": "log", "msg": msg}))

        await send_log(f"💬 Prompt alındı: {prompt}")
        data = await run_kktcmb(prompt, send_log)
        # mode bilgisini ayrıca gönder
        await ws.send_text(json.dumps({"type": "meta", "data": data}))
        await send_log("✅ Tamamlandı.")
    except Exception as e:
        await ws.send_text(json.dumps({"type": "error", "msg": str(e)}))
    finally:
        await ws.close()
