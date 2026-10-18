# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.10", "neo4j>=5.26.0", "litellm>=1.40.0"]
# ///
"""
Ten questions to the long-term memory, each checked against the real source (repo, .env, git, Neo4j plugins,
raw event trace), plus one off-topic control that must return nothing.

Two recall paths are scored on the same questions:
  mcp      MKG MCP server, project_get_context(project_id=haystack-workshop), top 3 learnings
           (the call an agent in this repo makes; called over stdio like Claude Code does)
  recall   recall_all.py: vector search over every project, cosine score, "no match" below the threshold

A question passes when every expected pattern appears in the returned texts. The truth column re-checks the
expected answer against the source at run time, so a stale expectation shows up as a truth failure.

    uv run --script claude_memory/memory_eval.py            # prints the table
    uv run --script claude_memory/memory_eval.py --md       # also writes results/memory_eval.md (local only)

Only reads. Raw-trace checks ignore events after CUTOFF, so the queries this eval itself logs through the
live hooks cannot answer its own questions.
"""
import argparse
import asyncio
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from recall_all import MIN_SCORE, pc, recall  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

MKG_ROOT = Path.home() / ".claude/plugins/cache/mkg/meta-knowledge-graph/0.1.43"
NEO4J_HOME = Path.home() / ("Library/Application Support/neo4j-desktop/Application/Data/dbmss/"
                            "dbms-329920e9-dda8-4f8f-9a55-10bbb1386859")
PROJECT = "haystack-workshop"
CUTOFF = "2026-10-18T20:00:00Z"  # first memory test against the live graph; later events are test traffic


def env_value(path: Path, key: str) -> str:
    m = re.search(rf"^{key}=(.*)$", path.read_text(), re.M)
    return m.group(1).strip() if m else ""


def trace(driver, cypher: str) -> bool:
    return bool(driver.execute_query(cypher, cutoff=CUTOFF, routing_="r").records[0][0])


def eval_accuracy() -> dict:
    res = json.loads((REPO / "exercises/results/graphrag_eval_results.json").read_text())["results"]
    acc = lambda qs: sum(sum(v["correct"] for v in q["verdicts"]) for q in qs) / sum(len(q["verdicts"]) for q in qs)
    return {k: round(acc(v) * 100) for k, v in res.items()}


def bench_mcp_pass() -> tuple[int, int]:
    reps = [r for f in glob.glob(str(HERE / "results/bench-*.json")) for r in json.loads(Path(f).read_text())
            if r.get("iface") == "mcp"]
    return sum(all(r["checks"].values()) for r in reps), len(reps)


# (id, question, expected regexes (all must match), truth check against the real source)
QUESTIONS = [
    ("Q1", "Which bolt port does the GraphRAG-agent-memory Neo4j instance listen on?", [r"7689"],
     lambda d: "7689" in env_value(REPO / ".env", "NEO4J_MEMORY_URI")),
    ("Q2", "How accurate was GraphRAG compared to vector RAG in the evaluation?", [r"100\s?%", r"62"],
     lambda d: eval_accuracy() == {"vector": 62, "vector_k8": 68, "graphrag": 100}),
    ("Q3", "Which LLM and embedding model does the MKG extraction use?", [r"gpt-5-mini", r"text-embedding-3-small"],
     lambda d: env_value(Path(os.environ["MKG_ENV"]), "LLM_MODEL") == "azure/gpt-5-mini"
     and env_value(Path(os.environ["MKG_ENV"]), "EMBEDDING_MODEL") == "azure/text-embedding-3-small"),
    ("Q4", "The push to origin main was blocked by the auto-mode classifier. How was it resolved?", [r"34c915e"],
     lambda d: subprocess.run(["git", "-C", str(REPO), "merge-base", "--is-ancestor", "34c915e", "origin/main"]).returncode == 0),
    ("Q5", "Which Neo4j AuthError did I hit on 2026-10-17 and how was it fixed?", [r"AuthError"],
     lambda d: trace(d, "MATCH (e:SessionEvent) WHERE e.timestamp < datetime($cutoff) "
                        "AND e.tool_response CONTAINS 'neo4j.exceptions.AuthError' RETURN count(e) > 0")),
    ("Q6", "Why were the neo4j_* Cypher tools missing from the MKG MCP server?", [r"APOC"],
     lambda d: any(NEO4J_HOME.glob("plugins/apoc-*-core.jar"))),
    ("Q7", "How many events and transcripts were imported from the Claude Code history?", [r"2,?963", r"\b19\b"],
     lambda d: "2,963 events, 19 transcripts" in (HERE / "README.md").read_text()),
    ("Q8", "Which leaked API keys had to be rotated?", [r"AI Studio", r"Google"],
     lambda d: "9 Google API keys" in (HERE / "README.md").read_text()),
    ("Q9", "Graph memory benchmark MCP vs CLI vs HTTP API: which interface is recommended?", [r"MCP", r"3/3"],
     lambda d: bench_mcp_pass()[0] >= 3),
    ("Q10", "How was the gpt-5-mini deployment capacity changed?", [r"\b50\b", r"1,?000"],
     lambda d: trace(d, "MATCH (e:SessionEvent {event_name: 'PreToolUse'}) WHERE e.timestamp < datetime($cutoff) "
                        "AND e.tool_input CONTAINS 'gpt-5-mini' AND e.tool_input CONTAINS 'capacity 1000' "
                        "RETURN count(e) > 0")),
]
CONTROL = ("C1", "kubernetes helm chart deployment for the ingress controller")


async def mcp_answers(questions: list[str]) -> list[list[str]]:
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(MKG_ROOT), CLAUDE_PROJECT_DIR=str(REPO))
    params = StdioServerParameters(command="bash", args=[f"{MKG_ROOT}/scripts/mcp-launcher.sh", str(MKG_ROOT)],
                                   env=env, cwd=str(REPO))
    out = []
    async with stdio_client(params, errlog=open(os.devnull, "w")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            for q in questions:
                res = await s.call_tool("project_get_context", {"project_id": PROJECT, "query": q, "limit": 3})
                data = json.loads(res.content[0].text)
                if isinstance(data.get("result"), str):  # some clients wrap the payload once more
                    data = json.loads(data["result"])
                out.append([l["text"] for l in data.get("learnings", [])])
    return out


def hit(texts: list[str], patterns: list[str]) -> bool:
    blob = "\n".join(texts)
    return all(re.search(p, blob, re.I) for p in patterns)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--md", action="store_true", help="also write results/memory_eval.md")
    args = ap.parse_args()
    os.environ["MKG_ENV"] = str(pc.load_mkg_env())
    mcp = asyncio.run(mcp_answers([q for _, q, _, _ in QUESTIONS] + [CONTROL[1]]))

    rows, score = [], {"truth": 0, "mcp": 0, "recall": 0}
    with GraphDatabase.driver(os.environ["NEO4J_URI"],
                              auth=(os.environ.get("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"])) as d:
        for (qid, q, pats, truth), mcp_texts in zip(QUESTIONS, mcp):
            rec = recall(q, top=3, driver=d)
            t, m, r = truth(d), hit(mcp_texts, pats), hit([x["text"] for x in rec], pats)
            score["truth"] += t; score["mcp"] += m; score["recall"] += r
            top = f"{rec[0]['score']:.2f} {rec[0]['project']}" if rec else "no match"
            rows.append((qid, q, t, m, r, top))
        rec = recall(CONTROL[1], top=3, driver=d)
        control_ok = not rec  # recall must say "no match"; MCP has no way to say it
        rows.append((CONTROL[0], CONTROL[1], True, not mcp[-1], control_ok,
                     "no match" if not rec else f"{rec[0]['score']:.2f} {rec[0]['project']}"))

    mark = lambda b: "pass" if b else "FAIL"
    lines = ["| # | Question | Truth | MCP | recall_all | recall_all top |", "|---|---|---|---|---|---|"]
    lines += [f"| {i} | {q} | {mark(t)} | {mark(m)} | {mark(r)} | {top} |" for i, q, t, m, r, top in rows]
    lines += ["", f"Truth {score['truth']}/10, MCP project_get_context {score['mcp']}/10, "
                  f"recall_all {score['recall']}/10 (min score {MIN_SCORE}). "
                  f"Control: MCP returned {len(mcp[-1])} learnings, recall_all {'no match' if control_ok else 'a match'}."]
    print("\n".join(lines))
    if args.md:
        (HERE / "results").mkdir(exist_ok=True)
        (HERE / "results/memory_eval.md").write_text("# Memory eval\n\n" + "\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
