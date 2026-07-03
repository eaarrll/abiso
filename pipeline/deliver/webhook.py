"""Inbound webhook: records replies verbatim; matching them to alert
instances (the ack loop proper) is Phase 5.

Routes:
  POST /webhook/sms        aggregator inbound SMS (generic JSON/form)
  POST /webhook/telegram   Telegram bot updates
  GET  /webhook/messenger  Meta verification handshake
  POST /webhook/messenger  Messenger inbound (user-initiated only)
  GET  /health

run: .venv/bin/uvicorn pipeline.deliver.webhook:app --port 8080
"""

import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from pipeline import config
from pipeline.deliver.store import Store

app = FastAPI(title="abiso-webhook")
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


@app.get("/health")
def health() -> dict:
    return {"ok": True, "live": config.LIVE}


@app.post("/webhook/sms")
async def inbound_sms(request: Request) -> dict:
    raw = (await request.body()).decode("utf-8", errors="replace")
    sender, text = None, None
    try:
        payload = json.loads(raw)
        sender = payload.get("number") or payload.get("from") or payload.get("sender")
        text = payload.get("message") or payload.get("text") or payload.get("body")
    except json.JSONDecodeError:
        form = await request.form()
        sender = form.get("number") or form.get("from")
        text = form.get("message") or form.get("text")
    mid = store().record_inbound("sms", sender, text, raw)
    return {"ok": True, "id": mid}


@app.post("/webhook/telegram")
async def inbound_telegram(request: Request) -> dict:
    raw = (await request.body()).decode("utf-8", errors="replace")
    sender, text = None, None
    try:
        msg = json.loads(raw).get("message") or {}
        sender = str((msg.get("from") or {}).get("id") or (msg.get("chat") or {}).get("id"))
        text = msg.get("text")
    except json.JSONDecodeError:
        pass
    mid = store().record_inbound("telegram", sender, text, raw)
    return {"ok": True, "id": mid}


@app.get("/webhook/messenger")
def messenger_verify(request: Request) -> PlainTextResponse:
    qp = request.query_params
    if (
        qp.get("hub.mode") == "subscribe"
        and config.MESSENGER_VERIFY_TOKEN
        and qp.get("hub.verify_token") == config.MESSENGER_VERIFY_TOKEN
    ):
        return PlainTextResponse(qp.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="verification failed")


@app.post("/webhook/messenger")
async def inbound_messenger(request: Request) -> dict:
    raw = (await request.body()).decode("utf-8", errors="replace")
    sender, text = None, None
    try:
        entry = (json.loads(raw).get("entry") or [{}])[0]
        messaging = (entry.get("messaging") or [{}])[0]
        sender = (messaging.get("sender") or {}).get("id")
        text = (messaging.get("message") or {}).get("text")
    except (json.JSONDecodeError, IndexError):
        pass
    mid = store().record_inbound("messenger", sender, text, raw)
    return {"ok": True, "id": mid}
