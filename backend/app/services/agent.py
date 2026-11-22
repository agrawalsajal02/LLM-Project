"""The Phase 3 agent, as a LangGraph state graph:

    rewrite -> plan -> execute -> grade -> answer
                 \\________________________^     (route "direct" skips tools)
    grade -> rewrite                            (retry once if docs weren't relevant)

Progress is pushed to the client through `emit(event, data)` while the graph runs:
`step` (what the agent is doing), `citations` (numbered sources) and `token` (answer text).
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from app.config import Settings
from app.services import prompts
from app.services.llm import ChatTurn, ImageInput, LLMClient, parse_json
from app.services.mcp_client import MCPToolbox, ToolSpec
from app.services.retrieval import Retriever
from app.services.web_search import web_search

log = logging.getLogger(__name__)

Emit = Callable[[str, dict], Awaitable[None]]

BUILTIN_TOOLS = [
    ToolSpec("search_documents", "Hybrid search over the company's uploaded documents (SOPs, policies).",
             {"properties": {"query": {"type": "string"}}, "required": ["query"]}),
    ToolSpec("search_attachments", "Search the files the user attached to this chat.",
             {"properties": {"query": {"type": "string"}}, "required": ["query"]}),
    ToolSpec("web_search", "Search the internet for recent or external information.",
             {"properties": {"query": {"type": "string"}}, "required": ["query"]}),
]
ROUTES = {"direct", "rag", "attachment", "web", "tool", "multi_step"}


@dataclass
class Attachment:
    doc_id: str
    filename: str
    kind: str  # "image" | "table" | "text"
    inline_text: str | None = None  # small files go straight into the prompt
    image: ImageInput | None = None


@dataclass
class AgentDeps:
    llm: LLMClient
    retriever: Retriever
    toolbox: MCPToolbox
    settings: Settings
    emit: Emit
    conversation_id: str
    kb_has_docs: bool = False
    attachments: list[Attachment] = field(default_factory=list)
    _step_counter: int = 0

    async def step(self, kind: str, label: str, status: str = "running", detail: str | None = None, id: int | None = None) -> int:
        if id is None:
            self._step_counter += 1
            id = self._step_counter
        await self.emit("step", {"id": id, "kind": kind, "label": label, "status": status, "detail": detail})
        return id

    def available_tools(self) -> list[ToolSpec]:
        tools = [BUILTIN_TOOLS[0]]
        if self.attachments:
            tools.append(BUILTIN_TOOLS[1])
        if self.settings.web_search_provider != "none":
            tools.append(BUILTIN_TOOLS[2])
        return tools + self.toolbox.tools


class AgentState(TypedDict, total=False):
    question: str
    history: list[ChatTurn]
    query: str
    plan: dict
    sources: list[dict]
    tool_results: list[dict]
    attempts: int
    retry: bool
    answer: str


def _deps(config: RunnableConfig) -> AgentDeps:
    return config["configurable"]["deps"]


def _history_text(turns: list[ChatTurn], limit: int = 6) -> str:
    return "\n".join(f"{t.role}: {t.content[:500]}" for t in turns[-limit:])


# ----------------------------------------------------------------------------- nodes


async def rewrite(state: AgentState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    question, previous = state["question"], state["history"][:-1]
    retry = state.get("attempts", 0) > 0
    if not previous and not retry:
        return {"query": question}
    sid = await deps.step("rewrite", "Rewriting the question...")
    try:
        query = (await deps.llm.complete(prompts.REWRITE_PROMPT.format(
            history=_history_text(previous), question=question,
            retry_hint=prompts.RETRY_HINT if retry else "",
        ))).strip().strip('"') or question
    except Exception:
        log.exception("rewrite failed")
        query = question
    await deps.step("rewrite", "Rewrote the question", "done", query, id=sid)
    return {"query": query[:500]}


def heuristic_plan(query: str, deps: AgentDeps) -> dict:
    """Keyword planner: used when the LLM planner returns nothing usable (and by the
    fake model), so the agent still behaves sensibly."""
    q = query.lower()
    tool_names = {t.name for t in deps.available_tools()}
    steps: list[dict] = []

    def add(tool: str, args: dict) -> None:
        if tool in tool_names:
            steps.append({"tool": tool, "args": args})

    if deps.attachments and re.search(r"attach|this file|the file|csv|excel|sheet|image|photo|picture|upload", q):
        add("search_attachments", {"query": query})
    if re.search(r"expir", q):
        category = next((c for c in ("dairy", "bakery", "fruits", "vegetables", "staples", "frozen") if c in q), None)
        add("get_expiring_skus", {"days": 7, **({"category": category} if category else {})})
    if re.search(r"low stock|reorder|restock|out of stock|running low", q):
        add("get_low_stock", {})
    if m := re.search(r"sku-\d+", q):
        add("get_stock", {"sku_id": m.group(0).upper()})
    if re.search(r"\b(create|raise|open|log)\b.*\bticket\b", q):
        add("create_ticket", {"title": query[:80], "description": query, "priority": "medium"})
    if re.search(r"latest|news|today|current|recent|regulation|search the web|internet|online", q):
        add("web_search", {"query": query})
    if re.search(r"\bsop\b|policy|procedure|process|guideline|manual|how (do|should|to)", q) or (
        not steps and deps.kb_has_docs and not re.fullmatch(r"\W*(hi|hello|hey|thanks?|thank you|ok)\W*", q)
    ):
        add("search_documents", {"query": query})

    if not steps and deps.attachments:
        add("search_attachments", {"query": query})
    steps = steps[: deps.settings.agent_max_steps]
    if not steps:
        return {"route": "direct", "reason": "No tools needed", "steps": []}
    if len(steps) > 1:
        route = "multi_step"
    else:
        route = {"search_documents": "rag", "search_attachments": "attachment", "web_search": "web"}.get(steps[0]["tool"], "tool")
    return {"route": route, "reason": "keyword planner", "steps": steps}


def _validate_plan(raw: dict | None, deps: AgentDeps) -> dict | None:
    if not raw or raw.get("route") not in ROUTES:
        return None
    names = {t.name for t in deps.available_tools()}
    steps = [
        {"tool": s["tool"], "args": s.get("args") or {}}
        for s in raw.get("steps") or []
        if isinstance(s, dict) and s.get("tool") in names
    ][: deps.settings.agent_max_steps]
    if raw["route"] != "direct" and not steps:
        return None
    return {"route": raw["route"], "reason": str(raw.get("reason", ""))[:200], "steps": steps if raw["route"] != "direct" else []}


async def plan(state: AgentState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    query = state["query"]
    if deps.settings.chat_mode == "rag":  # Phase 2 behaviour: always search the documents
        return {"plan": {"route": "rag", "reason": "RAG mode", "steps": [{"tool": "search_documents", "args": {"query": query}}]}}

    sid = await deps.step("plan", "Planning...")
    tools_text = "\n".join(
        f"- {t.name}: {t.description.strip().splitlines()[0] if t.description else ''} "
        f"params={json.dumps(t.input_schema.get('properties', {}))}"
        for t in deps.available_tools()
    )
    result = None
    try:
        raw = await deps.llm.complete(prompts.PLANNER_PROMPT.format(
            tools=tools_text, max_steps=deps.settings.agent_max_steps, kb_has_docs=deps.kb_has_docs,
            attachments=", ".join(a.filename for a in deps.attachments) or "none", question=query,
        ), json_mode=True)
        result = _validate_plan(parse_json(raw), deps)
    except Exception:
        log.exception("planner failed")
    result = result or heuristic_plan(query, deps)
    label = {"direct": "Answering directly", "rag": "Will search documents", "attachment": "Will read the attached file(s)",
             "web": "Will search the web", "tool": "Will use inventory tools", "multi_step": f"Multi-step plan ({len(result['steps'])} steps)"}[result["route"]]
    await deps.step("plan", label, "done", " → ".join(s["tool"] for s in result["steps"]) or result.get("reason"), id=sid)
    return {"plan": result}


def _add_source(sources: list[dict], source: dict) -> None:
    key = source.get("chunk_id") or source.get("url")
    if any((s.get("chunk_id") or s.get("url")) == key for s in sources):
        return
    sources.append({"n": len(sources) + 1, **source})


async def execute(state: AgentState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    sources: list[dict] = []
    tool_results: list[dict] = []

    for step in state["plan"]["steps"]:
        tool, args = step["tool"], step["args"]
        query = str(args.get("query") or state["query"])
        try:
            if tool in ("search_documents", "search_attachments"):
                scopes = ["kb"] if tool == "search_documents" else [deps.conversation_id]
                label = "Searching documents" if tool == "search_documents" else "Searching attached files"
                sid = await deps.step("search", f"{label}...", detail=query)
                result = await deps.retriever.search(query, scopes)
                for r in result.hits:
                    _add_source(sources, {"kind": "doc", "chunk_id": r.hit.chunk_id, "doc_id": r.hit.doc_id,
                                          "doc_name": r.hit.doc_name, "page": r.hit.page, "text": r.hit.text,
                                          "scope": "attachment" if tool == "search_attachments" else "kb"})
                await deps.step("search", f"{label}: {len(result.hits)} passages found", "done", query, id=sid)
            elif tool == "web_search":
                sid = await deps.step("web", "Searching the web...", detail=query)
                results = await web_search(query, deps.settings)
                for r in results:
                    _add_source(sources, {"kind": "web", "url": r["url"], "title": r["title"], "text": r["content"]})
                await deps.step("web", f"Web search: {len(results)} results", "done", query, id=sid)
            else:
                sid = await deps.step("tool", f"Calling tool {tool}...", detail=json.dumps(args))
                output = await deps.toolbox.call(tool, args)
                tool_results.append({"tool": tool, "args": args, "output": output[:6000]})
                await deps.step("tool", f"Tool {tool} returned", "done", output[:300], id=sid)
        except Exception as exc:
            log.exception("step %s failed", tool)
            tool_results.append({"tool": tool, "args": args, "error": str(exc)[:300]})
            await deps.step("error", f"{tool} failed", "error", str(exc)[:300])

    return {"sources": sources, "tool_results": tool_results}


async def grade(state: AgentState, config: RunnableConfig) -> dict:
    """Checks that document search found something relevant; if not, the graph goes back
    to `rewrite` with a broader query (bounded by agent_max_retries)."""
    deps = _deps(config)
    searched_docs = any(s["tool"] in ("search_documents", "search_attachments") for s in state["plan"]["steps"])
    doc_sources = [s for s in state["sources"] if s["kind"] == "doc"]
    if not searched_docs or state.get("attempts", 0) >= deps.settings.agent_max_retries:
        return {"retry": False}
    relevant = bool(doc_sources)
    if doc_sources:
        sid = await deps.step("grade", "Checking relevance...")
        try:
            verdict = parse_json(await deps.llm.complete(prompts.GRADER_PROMPT.format(
                question=state["query"],
                excerpts="\n---\n".join(s["text"][:400] for s in doc_sources[:5]),
            ), json_mode=True))
            relevant = True if verdict is None else bool(verdict.get("relevant", True))
        except Exception:
            relevant = True
        await deps.step("grade", "Relevant passages found" if relevant else "Not relevant, retrying", "done", id=sid)
    if relevant:
        return {"retry": False}
    return {"retry": True, "attempts": state.get("attempts", 0) + 1, "sources": [], "tool_results": []}


def after_grade(state: AgentState) -> str:
    return "rewrite" if state.get("retry") else "answer"


def build_system_prompt(state: AgentState, deps: AgentDeps) -> str:
    parts = [deps.settings.system_prompt, prompts.ANSWER_INSTRUCTIONS]
    if state.get("sources"):
        parts.append("Sources:")
        for s in state["sources"]:
            where = f"{s['doc_name']}, page {s['page']}" if s["kind"] == "doc" and s.get("page") else s.get("doc_name") or s.get("url")
            parts.append(f"[{s['n']}] ({where})\n<untrusted>\n{s['text']}\n</untrusted>")
    if state.get("tool_results"):
        parts.append("Tool results (live data):\n" + json.dumps(state["tool_results"], indent=1)[:12000])
    inline = [a for a in deps.attachments if a.inline_text]
    if inline:
        parts.append("Files attached to this chat:")
        for a in inline:
            parts.append(f"File: {a.filename}\n<untrusted>\n{a.inline_text}\n</untrusted>")
    images = [a.filename for a in deps.attachments if a.image]
    if images:
        parts.append(f"Images attached to this chat (included with the message): {', '.join(images)}")
    return "\n\n".join(parts)


async def answer(state: AgentState, config: RunnableConfig) -> dict:
    deps = _deps(config)
    sources = state.get("sources") or []
    if sources:
        await deps.emit("citations", {"citations": [{k: v for k, v in s.items() if k != "text"} | {"snippet": s["text"][:300]} for s in sources]})
    sid = await deps.step("answer", "Writing the answer...")
    images = [a.image for a in deps.attachments if a.image]
    text = []
    async for token in deps.llm.stream(build_system_prompt(state, deps), state["history"], images or None):
        text.append(token)
        await deps.emit("token", {"text": token})
    await deps.step("answer", "Answer complete", "done", id=sid)
    return {"answer": "".join(text)}


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("rewrite", rewrite)
    graph.add_node("plan", plan)
    graph.add_node("execute", execute)
    graph.add_node("grade", grade)
    graph.add_node("answer", answer)
    graph.add_edge(START, "rewrite")
    graph.add_edge("rewrite", "plan")
    graph.add_conditional_edges("plan", lambda s: "answer" if s["plan"]["route"] == "direct" else "execute", ["answer", "execute"])
    graph.add_edge("execute", "grade")
    graph.add_conditional_edges("grade", after_grade, ["rewrite", "answer"])
    graph.add_edge("answer", END)
    return graph.compile()


GRAPH = build_graph()


async def run_agent(deps: AgentDeps, history: list[ChatTurn]) -> AgentState:
    state: AgentState = {"question": history[-1].content, "history": history, "attempts": 0, "sources": [], "tool_results": []}
    return await GRAPH.ainvoke(state, config={"configurable": {"deps": deps}, "recursion_limit": 30})
