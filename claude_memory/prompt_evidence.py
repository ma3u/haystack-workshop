"""
Evidence chain of human interaction: list every prompt of the project with its proof in the MKG memory graph.

Runs query Q14 from cypher/questions.cypher through the MKG MCP server (tool neo4j_read_cypher, over stdio, the
same way Claude Code calls it) and writes a Markdown report. Read-only. Prompt text is redacted with the same
rules as the history importer before it is written.

Chain per prompt:
  (:User)-[:HAS_SESSION]->(:Session)-[:HAS_EVENT]->(:SessionEvent {UserPromptSubmit})   who, where, when, which client
  -> PostToolUse / PostToolUseFailure events until the next prompt                     what the agent did
  -> (:Learning)-[:FROM_SESSION]->(:Session)                                           what the session produced

    uv run --no-project --with mcp --with neo4j python claude_memory/prompt_evidence.py
    uv run --no-project --with mcp --with neo4j python claude_memory/prompt_evidence.py --out results/x.md
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from import_claude_history import known_secret_values, redact
from mcp_probe import ROOT as MKG_ROOT

HERE = Path(__file__).resolve().parent


def q14() -> str:
    text = (HERE / "cypher" / "questions.cypher").read_text()
    start = text.index("// Q14:")
    end = text.index(";", text.index("RETURN", start))
    return text[start:end]


async def run_cypher(query: str) -> list[dict]:
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=MKG_ROOT, CLAUDE_PROJECT_DIR=str(HERE.parent))
    params = StdioServerParameters(command="bash", args=[f"{MKG_ROOT}/scripts/mcp-launcher.sh", MKG_ROOT],
                                   env=env, cwd=str(HERE.parent))
    async with stdio_client(params, errlog=open(os.devnull, "w")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("neo4j_read_cypher", {"query": query})
            text = "".join(getattr(c, "text", "") for c in res.content)
            if getattr(res, "is_error", getattr(res, "isError", False)):
                raise RuntimeError(text[:500])
            return json.loads(text)


def cell(text: str) -> str:
    return re.sub(r"\s+", " ", text).replace("|", "\\|").replace("<", "&lt;").strip()


def render(rows: list[dict]) -> str:
    known, stats = known_secret_values(), Counter()
    for r in rows:
        r["prompt"] = redact(r["prompt"], known, stats)
    dev = [r for r in rows if r["kind"] == "developer"]
    mach = [r for r in rows if r["kind"] == "machine"]
    sessions = defaultdict(lambda: {"developer": 0, "machine": 0, "client": "", "first": ""})
    for r in rows:
        s = sessions[r["session"]]
        s[r["kind"]] += 1
        s["client"], s["first"] = r["client"], s["first"] or r["ts"]
        s["learnings"], s["approved"] = r["session_learnings"], r["session_approved"]

    def table(items):
        out = ["| # | Time (UTC) | Client | User | Session | Event | Prompt | Tool calls (failed) | Tools |",
               "|---|---|---|---|---|---|---|---|---|"]
        for i, r in enumerate(items, 1):
            tools = ", ".join(t.replace("mcp__plugin_meta-knowledge-graph_meta-knowledge-graph__", "mkg:")
                              for t in r["tools"])
            out.append(f"| {i} | {r['ts'][:19].replace('T', ' ')} | {r['client']} | {r['user']} | `{r['session']}` | "
                       f"`{r['event']}` | {cell(r['prompt'])} | {r['tool_calls']} ({r['failed']}) | {cell(tools)} |")
        return "\n".join(out)

    srows = ["| Session | Client | First prompt (UTC) | Developer | Machine | Learnings (approved) |",
             "|---|---|---|---|---|---|"]
    for sid, s in sorted(sessions.items(), key=lambda kv: kv[1]["first"]):
        srows.append(f"| `{sid}` | {s['client']} | {s['first'][:16].replace('T', ' ')} | {s['developer']} | "
                     f"{s['machine']} | {s['learnings']} ({s['approved']}) |")

    return f"""# Prompt evidence chain: haystack-workshop

Generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC by `claude_memory/prompt_evidence.py`: query Q14 from
`cypher/questions.cypher`, run through the MKG MCP server (`neo4j_read_cypher`). Read-only.
{len(dev)} developer prompts and {len(mach)} machine-written prompts in {len(sessions)} sessions; noise
(task notifications, `!` shell input, slash commands, system reminders, side-session reports) is excluded.
Redacted in this report: {sum(stats.values())} secret value(s).

## How to read the chain

Each row is one `(:SessionEvent {{event_name: 'UserPromptSubmit'}})`, reached from
`(:User)-[:HAS_SESSION]->(:Session)-[:HAS_EVENT]->`. **Event** is the last 16 characters of its `event_id`; the full
id is `<client>:<session uuid>:UserPromptSubmit:<event>`. Look one up:

```cypher
MATCH (u:User)-[:HAS_SESSION]->(s:Session)-[:HAS_EVENT]->(e:SessionEvent)
WHERE e.event_id ENDS WITH $event
RETURN u.id, s.session_id, e.client, e.cwd, e.timestamp, e.prompt
```

**Tool calls** are the `PostToolUse` / `PostToolUseFailure` events of the same session between this prompt and the
next one; **failed** counts failures. **Learnings** come from the session, see the table at the end.

What the graph proves: which client submitted which text, when, in which directory, under which user id, and what
the agent did next. What it cannot prove: that a person typed it. That rests on the classification below.

| Class | Rule |
|---|---|
| developer | everything not matched below; typed in an interactive client |
| machine | brief a session hands to a session it starts (`Task/Background (from the session that started this one)`), headless eval prompts (`Answer ONLY from the meta-knowledge-graph MCP tool…`, `Without using any tools:`), `adapter-*` cross-agent test sessions |
| noise (excluded) | `<task-notification>`, `<bash-input>`, `<command-name>`, `<system-reminder>`, `<side-sessions-event>`, `<agent-message>`, compaction summaries, `[Request interrupted` |

## Developer prompts

{table(dev)}

## Machine-written prompts

{table(mach)}

## Sessions and what they produced

{chr(10).join(srows)}

## Known gaps

- **Prompts typed while the agent is working are missing.** Claude Code queues them into the running turn without a
  `UserPromptSubmit` hook, so no event exists. `prompt_audit.py` compares the graph with the raw transcripts and lists them.
- **Tool calls are assigned by time window, not by link.** Each session has one `:Turn` node
  (`turn:<session>:unknown`), so `(:Turn)-[:ISSUED]->(:ToolCall)` cannot tell prompts apart. Tool calls of noise turns
  (background notifications) count towards the developer prompt before them.
- **Learnings link to the session, not to a prompt.** There is no `(:SessionEvent)-[:PRODUCED]->(:Learning)` edge.
- **Not every non-Claude test is caught.** Single-prompt sessions from other clients (e.g. `copilot_cli` with
  "Use only the meta-knowledge-graph MCP tool …") count as developer unless they run in an `adapter-*` session.
- A session is listed when any of its events ran in the repo, so sessions started in `~/projects` that later worked
  here appear with all their prompts, including off-topic ones.
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(HERE / "results" / "prompt_evidence_chain.md"))
    args = ap.parse_args()
    rows = asyncio.run(run_cypher(q14()))
    Path(args.out).write_text(render(rows))
    kinds = Counter(r["kind"] for r in rows)
    print(f"{len(rows)} prompts ({dict(kinds)}) -> {args.out}")


if __name__ == "__main__":
    main()
