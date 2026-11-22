import json
from dataclasses import dataclass, field


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def tokens(events) -> str:
    return "".join(d["text"] for e, d in events if e == "token")


@dataclass
class SpyLLM:
    """Scriptable model: `plan` is returned for planner prompts, `grades` (in order) for
    grader prompts. Records the system prompt and images passed to the answer."""

    plan: dict | None = None
    grades: list[bool] = field(default_factory=list)
    reply: str = "ok"
    systems: list[str] = field(default_factory=list)
    turns: list = field(default_factory=list)
    images: list = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)

    async def stream(self, system, turns, images=None):
        self.systems.append(system)
        self.turns.append([(t.role, t.content) for t in turns])
        self.images.append(images or [])
        yield self.reply

    async def complete(self, prompt, *, json_mode=False):
        self.prompts.append(prompt)
        if "planner" in prompt:
            return json.dumps(self.plan) if self.plan else ""
        if "excerpts" in prompt.lower():
            return json.dumps({"relevant": self.grades.pop(0) if self.grades else True})
        if "standalone search query" in prompt:
            return "rewritten query"
        return "Title"
