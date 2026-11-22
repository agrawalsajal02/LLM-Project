import json

import pytest

from app.services.llm import get_llm
from app.main import app

pytestmark = pytest.mark.asyncio


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


async def test_health(client):
    r = await client.get("/health")
    assert r.json()["status"] == "ok"


async def test_chat_crud(client):
    chat = (await client.post("/api/chats")).json()
    assert chat["title"] == "New chat"

    assert [c["id"] for c in (await client.get("/api/chats")).json()] == [chat["id"]]

    r = await client.patch(f"/api/chats/{chat['id']}", json={"title": "Renamed"})
    assert r.json()["title"] == "Renamed"

    assert (await client.delete(f"/api/chats/{chat['id']}")).status_code == 204
    assert (await client.get(f"/api/chats/{chat['id']}")).status_code == 404


async def test_send_message_streams_and_persists(client):
    chat = (await client.post("/api/chats")).json()

    r = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "hello"})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)

    tokens = [d["text"] for e, d in events if e == "token"]
    assert len(tokens) > 1  # streamed in pieces
    assert events[-1][0] == "done"
    assert events[-1][1]["title"] == "Fake chat title"

    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == "".join(tokens).strip()
    assert detail["title"] == "Fake chat title"


async def test_history_is_sent_to_model(client):
    seen = {}

    class Spy:
        async def stream(self, system, turns):
            seen["turns"] = [(t.role, t.content) for t in turns]
            yield "ok"

        async def complete(self, prompt):
            return "t"

    app.dependency_overrides[get_llm] = lambda: Spy()
    try:
        chat = (await client.post("/api/chats")).json()
        await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "first"})
        await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "second"})
    finally:
        app.dependency_overrides.clear()

    assert seen["turns"] == [("user", "first"), ("assistant", "ok"), ("user", "second")]


async def test_model_error_returns_error_event(client):
    class Broken:
        async def stream(self, system, turns):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        async def complete(self, prompt):
            return "t"

    app.dependency_overrides[get_llm] = lambda: Broken()
    try:
        chat = (await client.post("/api/chats")).json()
        r = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "hi"})
    finally:
        app.dependency_overrides.clear()

    assert parse_sse(r.text)[-1][0] == "error"


async def test_unknown_chat_404(client):
    r = await client.post(
        "/api/chats/00000000-0000-0000-0000-000000000000/messages", json={"content": "hi"}
    )
    assert r.status_code == 404
