"""Web search for the agent: Tavily (free monthly credits, built for LLM agents) or a
self-hosted SearXNG instance (free, unlimited). Results are untrusted text."""

import asyncio
import logging

import httpx

from app.config import Settings

log = logging.getLogger(__name__)
MAX_PAGE_CHARS = 3000


class WebSearchUnavailable(Exception):
    pass


async def _tavily(query: str, settings: Settings) -> list[dict]:
    if not settings.tavily_api_key:
        raise WebSearchUnavailable("TAVILY_API_KEY is not set")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.tavily.com/search",
            headers={"Authorization": f"Bearer {settings.tavily_api_key}"},
            json={"query": query, "max_results": settings.web_max_results, "search_depth": "basic"},
        )
        response.raise_for_status()
    return [
        {"title": r.get("title", ""), "url": r["url"], "content": (r.get("content") or "")[:MAX_PAGE_CHARS]}
        for r in response.json().get("results", [])
    ]


async def _fetch_page_text(client: httpx.AsyncClient, url: str) -> str:
    """Download a page and keep only its main text (no menus, ads, scripts)."""
    import trafilatura

    try:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        text = await asyncio.to_thread(trafilatura.extract, response.text)
        return (text or "")[:MAX_PAGE_CHARS]
    except Exception:
        return ""


async def _searxng(query: str, settings: Settings) -> list[dict]:
    headers = {"User-Agent": "llm-project-agent/0.3"}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        response = await client.get(f"{settings.searxng_url}/search", params={"q": query, "format": "json"})
        response.raise_for_status()
        results = response.json().get("results", [])[: settings.web_max_results]
        # SearXNG only gives short snippets, so read the top 3 pages.
        pages = await asyncio.gather(*(_fetch_page_text(client, r["url"]) for r in results[:3]))
    out = []
    for i, r in enumerate(results):
        content = (pages[i] if i < len(pages) else "") or r.get("content", "")
        out.append({"title": r.get("title", ""), "url": r["url"], "content": content})
    return out


async def web_search(query: str, settings: Settings) -> list[dict]:
    if settings.web_search_provider == "tavily":
        return await _tavily(query, settings)
    if settings.web_search_provider == "searxng":
        return await _searxng(query, settings)
    raise WebSearchUnavailable("Web search is turned off (set WEB_SEARCH_PROVIDER=tavily or searxng)")
