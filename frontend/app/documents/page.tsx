"use client";

import Link from "next/link";
import { DragEvent, FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { api, DocumentInfo, documentUrl, SearchHit } from "@/lib/api";

function size(bytes: number) {
  return bytes > 1_000_000 ? `${(bytes / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1000))} KB`;
}

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await api.listDocuments());
    } catch (err) {
      setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll while documents are being processed in the background.
  useEffect(() => {
    if (!docs.some((d) => d.status === "processing")) return;
    const t = setInterval(refresh, 1500);
    return () => clearInterval(t);
  }, [docs, refresh]);

  async function uploadFiles(files: File[]) {
    if (!files.length) return;
    setUploading(true);
    setError(null);
    try {
      for (const f of files) await api.uploadDocument(f);
    } catch (err) {
      setError("Upload failed: " + (err as Error).message);
    } finally {
      setUploading(false);
      refresh();
    }
  }

  function onDrop(e: DragEvent) {
    e.preventDefault();
    setDragging(false);
    uploadFiles(Array.from(e.dataTransfer.files));
  }

  async function remove(id: string) {
    await api.deleteDocument(id);
    refresh();
  }

  async function search(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    try {
      setHits(await api.search(query.trim()));
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <div className="page">
      <header className="page-header">
        <Link href="/">← Back to chat</Link>
        <h1>Knowledge base</h1>
        <p className="muted">Upload SOPs, policies and manuals (PDF, DOCX, TXT, MD). The assistant searches them and cites the source.</p>
      </header>

      <div
        className={`dropzone ${dragging ? "dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => fileRef.current?.click()}
      >
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          multiple
          hidden
          onChange={(e) => {
            uploadFiles(Array.from(e.target.files ?? []));
            e.target.value = "";
          }}
        />
        {uploading ? "Uploading…" : "Drop files here or click to upload"}
      </div>

      {error && <div className="error">{error}</div>}

      <table className="doc-table">
        <thead>
          <tr>
            <th>File</th>
            <th>Status</th>
            <th>Pages</th>
            <th>Chunks</th>
            <th>Size</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {docs.length === 0 && (
            <tr>
              <td colSpan={6} className="muted">
                No documents yet.
              </td>
            </tr>
          )}
          {docs.map((d) => (
            <tr key={d.id}>
              <td>
                <a href={documentUrl(d.id)} target="_blank" rel="noreferrer">
                  {d.filename}
                </a>
              </td>
              <td>
                <span className={`badge ${d.status}`} title={d.error ?? ""}>
                  {d.status}
                </span>
                {d.error && <div className="doc-error">{d.error}</div>}
              </td>
              <td>{d.page_count ?? "–"}</td>
              <td>{d.chunk_count}</td>
              <td>{size(d.size_bytes)}</td>
              <td>
                <button className="link-button" onClick={() => remove(d.id)}>
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <section className="search-debug">
        <h2>Test hybrid search</h2>
        <p className="muted">Shows how BM25 (keyword) and kNN (vector) ranks are fused with RRF.</p>
        <form onSubmit={search} className="search-form">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. dairy expiry markdown" />
          <button type="submit">Search</button>
        </form>
        {hits && hits.length === 0 && <p className="muted">No results.</p>}
        {hits && hits.length > 0 && (
          <ol className="hits">
            {hits.map((h, i) => (
              <li key={i}>
                <div className="hit-meta">
                  <strong>
                    {h.doc_name}
                    {h.page ? `, p.${h.page}` : ""}
                  </strong>
                  <span>RRF {h.rrf_score.toFixed(4)}</span>
                  <span>BM25 #{h.bm25_rank ?? "–"}</span>
                  <span>kNN #{h.knn_rank ?? "–"}</span>
                  {h.rerank_score !== null && <span>rerank {h.rerank_score.toFixed(3)}</span>}
                </div>
                <p>{h.text.slice(0, 300)}…</p>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}
