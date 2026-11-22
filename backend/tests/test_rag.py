"""Phase 2: upload, parse, chunk, embed, hybrid search, citations."""

import io


from app.services.chunking import split_text
from app.services.parsing import parse_bytes
from app.services.retrieval import rrf_merge
from app.services.search_store import Hit
from tests.helpers import parse_sse, tokens


SOP = """Expiry SOP for dairy products

Dairy items expiring within 24 hours must be moved to the markdown shelf and sold at a 30% discount.
Items past their expiry date must be removed from shelves immediately and logged in the wastage register.

Cold chain SOP

Cold storage must stay between 2 and 4 degrees Celsius. Check the temperature log every 2 hours.
"""
RETURNS = "Returns policy\n\nCustomers can return damaged items within 48 hours with the order id."


async def upload(client, name: str, content: bytes, url: str = "/api/documents"):
    r = await client.post(url, files={"file": (name, content)})
    assert r.status_code == 202, r.text
    return r.json()


def test_split_text_respects_size_and_overlap():
    text = " ".join(f"word{i}" for i in range(500))
    chunks = split_text(text, size=200, overlap=40)
    assert len(chunks) > 5
    assert all(len(c) <= 240 for c in chunks)
    assert chunks[0][-20:].split()[-1] in chunks[1]  # overlap carries context forward


def test_rrf_rewards_documents_found_by_both_retrievers():
    a, b, c = (Hit(x, "d", "doc", None, x, 0) for x in "abc")
    fused = rrf_merge({"bm25": [a, b], "knn": [b, c]}, k=60)
    assert [h.hit.chunk_id for h in fused][0] == "b"
    assert fused[0].bm25_rank == 2 and fused[0].knn_rank == 1


def test_parse_docx_and_csv():
    import docx

    d = docx.Document()
    d.add_paragraph("Fire safety SOP: keep exits clear.")
    buf = io.BytesIO()
    d.save(buf)
    assert "exits clear" in parse_bytes(buf.getvalue(), "sop.docx").sections[0].text

    csv = parse_bytes(b"sku,stock\nSKU-1,5\n", "stock.csv").sections[0].text
    assert "sku: SKU-1; stock: 5" in csv


async def test_upload_index_and_search(client):
    doc = await upload(client, "dairy_sop.txt", SOP.encode())
    await upload(client, "returns.md", RETURNS.encode())

    docs = (await client.get("/api/documents")).json()
    assert {d["status"] for d in docs} == {"ready"}
    assert next(d for d in docs if d["id"] == doc["id"])["chunk_count"] >= 1

    hits = (await client.get("/api/search", params={"q": "dairy markdown discount"})).json()
    assert hits[0]["doc_name"] == "dairy_sop.txt"
    assert hits[0]["bm25_rank"] is not None and hits[0]["knn_rank"] is not None

    file = await client.get(f"/api/documents/{doc['id']}/file")
    assert file.status_code == 200 and b"markdown shelf" in file.content


async def test_unsupported_and_empty_files_rejected(client):
    assert (await client.post("/api/documents", files={"file": ("x.exe", b"MZ")})).status_code == 415
    assert (await client.post("/api/documents", files={"file": ("x.txt", b"")})).status_code == 400


async def test_failed_parse_marks_document_failed(client):
    doc = await upload(client, "broken.pdf", b"not really a pdf")
    assert (await client.get(f"/api/documents/{doc['id']}")).json()["status"] == "failed"


async def test_chat_answers_with_citations(client):
    await upload(client, "dairy_sop.txt", SOP.encode())
    chat = (await client.post("/api/chats")).json()

    r = await client.post(f"/api/chats/{chat['id']}/messages", json={"content": "What is the SOP for expiring dairy?"})
    events = parse_sse(r.text)

    citations = next(d for e, d in events if e == "citations")["citations"]
    assert citations[0]["doc_name"] == "dairy_sop.txt" and citations[0]["n"] == 1
    assert "[1]" in tokens(events)  # fake model cites the sources it was given
    assert any(e == "step" and d["kind"] == "search" for e, d in events)

    saved = (await client.get(f"/api/chats/{chat['id']}")).json()["messages"][1]
    assert saved["citations"][0]["doc_name"] == "dairy_sop.txt"


async def test_deleted_document_is_no_longer_searchable(client):
    doc = await upload(client, "dairy_sop.txt", SOP.encode())
    assert (await client.delete(f"/api/documents/{doc['id']}")).status_code == 204
    assert (await client.get("/api/search", params={"q": "dairy"})).json() == []
