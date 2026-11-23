"""Webhook authentication: a reply stops an escalation, so every inbound
channel must reject unauthenticated requests and record nothing."""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from pipeline import config
from pipeline.deliver import webhook
from pipeline.deliver.store import Store

SMS_TOKEN = "sms-token-for-tests"
TG_SECRET = "tg-secret-for-tests"
APP_SECRET = "meta-app-secret-for-tests"


@pytest.fixture
def db(tmp_path, monkeypatch):
    # The app opens its own connection lazily, on the server thread, as in
    # production; tests read through a separate connection.
    path = tmp_path / "abiso.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(webhook, "_store", None)
    return path


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", SMS_TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_WEBHOOK_SECRET", TG_SECRET)
    monkeypatch.setattr(config, "MESSENGER_APP_SECRET", APP_SECRET)
    monkeypatch.setattr(config, "MESSENGER_VERIFY_TOKEN", "verify-me")
    return TestClient(webhook.app)


def inbound(db) -> list:
    return Store(db).conn.execute("SELECT sender, text FROM inbound_message").fetchall()


SMS_BODY = {"number": "09170000001", "message": "OO"}


# --- SMS ---------------------------------------------------------------------


def test_sms_header_token_accepted(client, db):
    r = client.post("/webhook/sms", json=SMS_BODY, headers={"X-Abiso-Token": SMS_TOKEN})
    assert r.status_code == 200
    [row] = inbound(db)
    assert (row["sender"], row["text"]) == ("09170000001", "OO")


def test_sms_query_token_accepted(client, db):
    r = client.post(f"/webhook/sms?token={SMS_TOKEN}", json=SMS_BODY)
    assert r.status_code == 200
    assert len(inbound(db)) == 1


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-Abiso-Token": "wrong"}, {"X-Abiso-Token": ""}],
    ids=["none", "wrong", "empty"],
)
def test_sms_bad_token_rejected_and_not_recorded(client, db, headers):
    r = client.post("/webhook/sms", json=SMS_BODY, headers=headers)
    assert r.status_code == 403
    assert inbound(db) == []


def test_sms_unconfigured_secret_fails_closed(client, db, monkeypatch):
    monkeypatch.setattr(config, "WEBHOOK_TOKEN", "")
    # Even a request that presents an empty token must not get through.
    r = client.post("/webhook/sms?token=", json=SMS_BODY)
    assert r.status_code == 503
    assert inbound(db) == []


# --- Telegram ----------------------------------------------------------------

TG_UPDATE = {"message": {"from": {"id": 4242}, "text": "OO"}}


def test_telegram_secret_accepted(client, db):
    r = client.post(
        "/webhook/telegram",
        json=TG_UPDATE,
        headers={"X-Telegram-Bot-Api-Secret-Token": TG_SECRET},
    )
    assert r.status_code == 200
    [row] = inbound(db)
    assert row["sender"] == "4242"


def test_telegram_missing_secret_rejected(client, db):
    r = client.post("/webhook/telegram", json=TG_UPDATE)
    assert r.status_code == 403
    assert inbound(db) == []


# --- Messenger ---------------------------------------------------------------

MSG_BODY = json.dumps(
    {"entry": [{"messaging": [{"sender": {"id": "psid-1"}, "message": {"text": "OO"}}]}]}
).encode()


def sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_messenger_valid_signature_accepted(client, db):
    r = client.post(
        "/webhook/messenger",
        content=MSG_BODY,
        headers={"X-Hub-Signature-256": sign(MSG_BODY), "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert len(inbound(db)) == 1


def test_messenger_tampered_body_rejected(client, db):
    signature = sign(MSG_BODY)
    tampered = MSG_BODY.replace(b"psid-1", b"psid-2")
    r = client.post(
        "/webhook/messenger", content=tampered, headers={"X-Hub-Signature-256": signature}
    )
    assert r.status_code == 403
    assert inbound(db) == []


def test_messenger_wrong_secret_rejected(client, db):
    r = client.post(
        "/webhook/messenger",
        content=MSG_BODY,
        headers={"X-Hub-Signature-256": sign(MSG_BODY, "not-the-app-secret")},
    )
    assert r.status_code == 403


def test_messenger_verify_handshake(client):
    ok = client.get(
        "/webhook/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"},
    )
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get(
        "/webhook/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"},
    )
    assert bad.status_code == 403


def test_health_needs_no_auth(client):
    assert client.get("/health").status_code == 200
