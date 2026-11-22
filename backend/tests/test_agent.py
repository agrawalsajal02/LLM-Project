"""Phase 3: planner/router, MCP tools, web search, attachments, relevance retry."""

import json
import os
import sys

import pytest

from app.config import Settings
from app.main import app
from app.services import agent
from app.services.llm import get_llm
from app.services.mcp_client import MCPToolbox, ToolSpec, toolbox
from tests.helpers import SpyLLM, parse_sse
from tests.test_rag import SOP, upload



def use(llm):
    app.dependency_overrides[get_llm] = lambda: llm
    return llm


def steps(events, kind=None):
    return [d for e, d in events if e == "step" and d["status"] != "running" and (kind is None or d["kind"] == kind)]


async def ask(client, chat_id, text):
    r = await client.post(f"/api/chats/{chat_id}/messages", json={"content": text})
    return parse_sse(r.text)


@pytest.fixture
def fake_tools(monkeypatch):
    calls = []

    async def call(name, args):
        calls.append((name, args))
        return json.dumps([{"sku_id": "SKU-1004", "name": "Paneer", "days_left": 1}])

    monkeypatch.setattr(toolbox, "tools", [ToolSpec("get_expiring_skus", "SKUs expiring soon", {"properties": {"days": {"type": "integer"}}})])
    monkeypatch.setattr(toolbox, "call", call)
    return calls


async def test_direct_route_uses_no_tools(client, fake_tools):
    llm = use(SpyLLM(plan={"route": "direct", "reason": "greeting", "steps": []}))
    chat = (await client.post("/api/chats")).json()
    events = await ask(client, chat["id"], "hi there")
    assert fake_tools == [] and not steps(events, "search")
    assert "Sources:" not in llm.systems[0]


async def test_tool_route_calls_mcp_tool_and_passes_result(client, fake_tools):
    llm = use(SpyLLM(plan={"route": "tool", "steps": [{"tool": "get_expiring_skus", "args": {"days": 3}}]}))
    chat = (await client.post("/api/chats")).json()
    events = await ask(client, chat["id"], "What expires in 3 days?")
    assert fake_tools == [("get_expiring_skus", {"days": 3})]
    assert steps(events, "tool")[0]["label"].startswith("Tool get_expiring_skus")
    assert "SKU-1004" in llm.systems[0]


async def test_multi_step_combines_tool_and_documents(client, fake_tools):
    await upload(client, "dairy_sop.txt", SOP.encode())
    llm = use(SpyLLM(plan={"route": "multi_step", "steps": [
        {"tool": "get_expiring_skus", "args": {"days": 7}},
        {"tool": "search_documents", "args": {"query": "SOP for expiring dairy"}},
    ]}))
    chat = (await client.post("/api/chats")).json()
    events = await ask(client, chat["id"], "Which SKUs expire this week and what's the SOP for them?")
    assert fake_tools and any(e == "citations" for e, _ in events)
    assert "SKU-1004" in llm.systems[0] and "markdown shelf" in llm.systems[0]


async def test_planner_cannot_call_unknown_tools_and_falls_back(client, fake_tools):
    use(SpyLLM(plan={"route": "tool", "steps": [{"tool": "drop_database", "args": {}}]}))
    chat = (await client.post("/api/chats")).json()
    events = await ask(client, chat["id"], "Which dairy items expire soon?")
    # Invalid plan -> keyword planner picks the real expiry tool.
    assert fake_tools == [("get_expiring_skus", {"days": 7, "category": "dairy"})]
    assert events[-1][0] == "done"


async def test_keyword_planner_used_by_fake_model(client, fake_tools):
    await upload(client, "dairy_sop.txt", SOP.encode())
    chat = (await client.post("/api/chats")).json()  # default FakeClient returns no JSON
    events = await ask(client, chat["id"], "Which items expire this week and what is the SOP?")
    assert fake_tools and steps(events, "search")


async def test_tool_failure_is_reported_not_fatal(client, monkeypatch, fake_tools):
    async def boom(name, args):
        raise RuntimeError("inventory DB down")

    monkeypatch.setattr(toolbox, "call", boom)
    llm = use(SpyLLM(plan={"route": "tool", "steps": [{"tool": "get_expiring_skus", "args": {}}]}))
    chat = (await client.post("/api/chats")).json()
    events = await ask(client, chat["id"], "expiring?")
    assert steps(events, "error") and events[-1][0] == "done"
    assert "inventory DB down" in llm.systems[0]  # model is told the tool failed


async def test_web_search_route_cites_urls(client, monkeypatch):
    async def fake_web(query, settings):
        return [{"title": "FSSAI labelling rules", "url": "https://example.org/fssai", "content": "Best-before must be printed."}]

    monkeypatch.setattr(agent, "web_search", fake_web)
    settings = Settings()
    settings.web_search_provider = "tavily"
    from app.config import get_settings
    app.dependency_overrides[get_settings] = lambda: settings

    llm = use(SpyLLM(plan={"route": "web", "steps": [{"tool": "web_search", "args": {"query": "FSSAI expiry label rules 2026"}}]}))
    chat = (await client.post("/api/chats")).json()
    events = await ask(client, chat["id"], "Latest FSSAI rules on expiry labels?")
    citations = next(d for e, d in events if e == "citations")["citations"]
    assert citations[0]["kind"] == "web" and citations[0]["url"] == "https://example.org/fssai"
    assert "<untrusted>" in llm.systems[0]  # web text is fenced as untrusted data


async def test_csv_attachment_goes_into_context(client):
    chat = (await client.post("/api/chats")).json()
    csv = b"sku,name,stock,reorder_level\nSKU-9,Ghee 1L,2,10\nSKU-8,Salt 1kg,50,10\n"
    att = await upload(client, "stock.csv", csv, f"/api/chats/{chat['id']}/attachments")
    assert att["kind"] == "attachment"
    assert (await client.get(f"/api/chats/{chat['id']}/attachments")).json()[0]["status"] == "ready"
    assert (await client.get("/api/documents")).json() == []  # not in the shared knowledge base

    llm = use(SpyLLM(plan={"route": "attachment", "steps": [{"tool": "search_attachments", "args": {"query": "below reorder level"}}]}))
    await ask(client, chat["id"], "Which items in the attached file are below reorder level?")
    assert "Ghee 1L" in llm.systems[0] and "stock.csv" in llm.systems[0]


async def test_attachments_are_private_to_their_chat(client):
    chat_a = (await client.post("/api/chats")).json()
    chat_b = (await client.post("/api/chats")).json()
    await upload(client, "secret.txt", b"The vault code is 4242.", f"/api/chats/{chat_a['id']}/attachments")

    llm = use(SpyLLM(plan={"route": "rag", "steps": [{"tool": "search_documents", "args": {"query": "vault code"}}]}))
    await ask(client, chat_b["id"], "What is the vault code?")
    assert "4242" not in llm.systems[0]


async def test_image_attachment_is_sent_to_model(client):
    chat = (await client.post("/api/chats")).json()
    await upload(client, "shelf.png", b"\x89PNG\r\n\x1a\nfake", f"/api/chats/{chat['id']}/attachments")
    llm = use(SpyLLM(plan={"route": "direct", "steps": []}))
    await ask(client, chat["id"], "What do you see in this photo?")
    assert llm.images[0][0].mime_type == "image/png"


async def test_promote_attachment_to_knowledge_base(client):
    chat = (await client.post("/api/chats")).json()
    att = await upload(client, "sop.txt", SOP.encode(), f"/api/chats/{chat['id']}/attachments")
    r = await client.post(f"/api/documents/{att['id']}/promote")
    assert r.json()["kind"] == "kb"
    hits = (await client.get("/api/search", params={"q": "markdown shelf"})).json()
    assert hits and hits[0]["doc_name"] == "sop.txt"


async def test_irrelevant_docs_trigger_one_retry(client):
    await upload(client, "dairy_sop.txt", SOP.encode())
    use(SpyLLM(plan={"route": "rag", "steps": [{"tool": "search_documents", "args": {"query": "x"}}]}, grades=[False, False]))
    chat = (await client.post("/api/chats")).json()
    events = await ask(client, chat["id"], "Something unrelated?")
    assert len(steps(events, "rewrite")) == 1  # retried once, then answered (no loop)
    assert len(steps(events, "search")) == 2
    assert events[-1][0] == "done"


async def test_rag_mode_always_searches(client, monkeypatch):
    await upload(client, "dairy_sop.txt", SOP.encode())
    settings = Settings()
    settings.chat_mode = "rag"
    from app.config import get_settings
    app.dependency_overrides[get_settings] = lambda: settings
    use(SpyLLM(plan={"route": "direct", "steps": []}))
    chat = (await client.post("/api/chats")).json()
    events = await ask(client, chat["id"], "hello")
    assert steps(events, "search")


async def test_real_mcp_server_over_stdio(tmp_path):
    """Starts mcp_server/darkstore.py as a subprocess and calls its tools."""
    os.environ["DARKSTORE_DB"] = str(tmp_path / "darkstore.db")
    box = MCPToolbox()
    await box.start(Settings(mcp_enabled=True, mcp_server_command=sys.executable))
    try:
        assert {"get_expiring_skus", "get_low_stock", "create_ticket"} <= {t.name for t in box.tools}
        low = json.loads(await box.call("get_low_stock", {"category": "dairy"}))
        assert any(r["sku_id"] == "SKU-1004" for r in low["result"])
        ticket = json.loads(await box.call("create_ticket", {"title": "Restock paneer", "description": "Below reorder level"}))
        assert ticket["ticket"].startswith("OPS-")
    finally:
        await box.stop()
