"""Inbound webhook: authenticates each request, then records the reply
verbatim; matching replies to alert instances is the ack loop's job.

Routes:
  POST /webhook/sms        aggregator inbound SMS (generic JSON/form)
  POST /webhook/telegram   Telegram bot updates
  GET  /webhook/messenger  Meta verification handshake
  POST /webhook/messenger  Messenger inbound (user-initiated only)
  GET  /health

Authentication (fail closed — see config.WEBHOOK_TOKEN):
  sms        shared token in the X-Abiso-Token header, or ?token= for
             aggregators that can't set headers (header preferred: query
             strings end up in access logs)
  telegram   X-Telegram-Bot-Api-Secret-Token, set via setWebhook secret_token
  messenger  X-Hub-Signature-256, HMAC-SHA256 of the body with the app secret

A rejected request is never recorded.

run: .venv/bin/uvicorn pipeline.deliver.webhook:app --port 8080
"""

import hashlib
import hmac
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


def _same(presented: str | None, expected: str) -> bool:
    return presented is not None and hmac.compare_digest(presented.encode(), expected.encode())


def _require(secret: str, channel: str) -> None:
    if not secret:
        raise HTTPException(status_code=503, detail=f"{channel} webhook secret not configured")


def _reject(channel: str) -> None:
    raise HTTPException(status_code=403, detail=f"{channel} webhook authentication failed")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "live": config.LIVE}


@app.post("/webhook/sms")
async def inbound_sms(request: Request) -> dict:
    _require(config.WEBHOOK_TOKEN, "sms")
    presented = request.headers.get("x-abiso-token") or request.query_params.get("token")
    if not _same(presented, config.WEBHOOK_TOKEN):
        _reject("sms")

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
    _require(config.TELEGRAM_WEBHOOK_SECRET, "telegram")
    if not _same(
        request.headers.get("x-telegram-bot-api-secret-token"), config.TELEGRAM_WEBHOOK_SECRET
    ):
        _reject("telegram")

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
        and _same(qp.get("hub.verify_token"), config.MESSENGER_VERIFY_TOKEN)
    ):
        return PlainTextResponse(qp.get("hub.challenge", ""))
    raise HTTPException(status_code=403, detail="verification failed")


@app.post("/webhook/messenger")
async def inbound_messenger(request: Request) -> dict:
    _require(config.MESSENGER_APP_SECRET, "messenger")
    body = await request.body()
    expected = (
        "sha256="
        + hmac.new(config.MESSENGER_APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    )
    if not _same(request.headers.get("x-hub-signature-256"), expected):
        _reject("messenger")

    raw = body.decode("utf-8", errors="replace")
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
