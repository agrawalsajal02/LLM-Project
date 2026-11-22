
from app.main import app
from app.services.llm import get_llm
from tests.helpers import SpyLLM, parse_sse, tokens



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

    assert len([e for e, _ in events if e == "token"]) > 1  # streamed in pieces
    assert events[-1][0] == "done"
    assert events[-1][1]["title"] == "Fake chat title"

    detail = (await client.get(f"/api/chats/{chat['id']}")).json()
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["content"] == tokens(events).strip()
    assert detail["messages"][1]["steps"]  # agent steps are saved with the message
    assert detail["title"] == "Fake chat title"


async def test_history_is_sent_to_model(client):
    spy = SpyLLM(plan={"route": "direct", "steps": []})
    app.dependency_overrides[get_llm] = lambda: spy
    chat = (await client.post("/api/chats")).json()
    await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "first"})
    await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "second"})

    assert spy.turns[-1] == [("user", "first"), ("assistant", "ok"), ("user", "second")]


async def test_model_error_returns_error_event(client):
    class Broken(SpyLLM):
        async def stream(self, system, turns, images=None):
            raise RuntimeError("boom")
            yield  # pragma: no cover

    app.dependency_overrides[get_llm] = lambda: Broken(plan={"route": "direct", "steps": []})
    chat = (await client.post("/api/chats")).json()
    r = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "hi"})

    assert parse_sse(r.text)[-1][0] == "error"


async def test_unknown_chat_404(client):
    r = await client.post("/api/chats/00000000-0000-0000-0000-000000000000/messages", json={"content": "hi"})
    assert r.status_code == 404
