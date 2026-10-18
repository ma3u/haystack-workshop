"""
Benchmark: Claude Code using the same Neo4j graph memory via MCP vs CLI vs HTTP API.

Each rep = two separate headless `claude -p` processes (so nothing survives in Claude's context):
  1. store facts      2. recall them in a fresh session + apply an update (Berlin -> Lisbon)
Then the graph is checked directly in Neo4j.

    venv/bin/python claude_memory/bench.py --reps 2 --model sonnet

Isolation per run: --strict-mcp-config, only the needed tools, --setting-sources project
(skips user hooks/settings), --no-session-persistence, --permission-mode dontAsk.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from _common import ROOT, Neo4jMemory  # noqa: F401  (loads .env)

PY = str(ROOT / "venv/bin/python")
HERE = Path(__file__).resolve().parent
API_PORT = 8765

CONTEXT = {
    "mcp": "You have long-term memory through the graph-memory MCP tools (remember, recall, forget).",
    "cli": "You have long-term memory through the CLI `venv/bin/python claude_memory/cli.py` (run it with --help for usage).",
    "api": f"You have long-term memory through an HTTP API at http://127.0.0.1:{API_PORT} (curl GET / for usage).",
}
STORE = ("{ctx} Store these facts about me in long-term memory, one fact per entry, referring to me as 'User': "
         "I'm Mara, I work at deepset in Berlin on the Haystack team, my team lead is Jonas, and I'm allergic "
         "to peanuts. Reply 'done' when stored.")
RECALL = ("{ctx} Use long-term memory to answer: who is my team lead, which company do I work for, and what am I "
          "allergic to? Then update memory: I moved from Berlin to Lisbon (remove the Berlin fact, store Lisbon). "
          "Finally reply with one line: LEAD=<name>; COMPANY=<name>; ALLERGY=<thing>")
ANSWER_MUST = ["jonas", "deepset", "peanut"]
GRAPH_MUST = ["mara", "deepset", "haystack", "jonas", "peanut", "lisbon"]
GRAPH_MUST_NOT = ["berlin"]


PERMISSIVE = False  # --permissive: allow all Bash for cli/api (Claude often batches calls in for-loops / && chains)


def claude(iface: str, prompt: str, user_id: str, model: str, mcp_cfg: str) -> dict:
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
           "--strict-mcp-config", "--mcp-config", mcp_cfg,
           "--setting-sources", "project", "--no-session-persistence",
           "--permission-mode", "dontAsk", "--max-budget-usd", "1"]
    if iface == "mcp":
        cmd += ["--tools", "", "--allowedTools", "mcp__graph-memory"]
    elif PERMISSIVE:
        cmd += ["--tools", "Bash", "--allowedTools", "Bash"]
    elif iface == "cli":
        cmd += ["--tools", "Bash", "--allowedTools", "Bash(venv/bin/python claude_memory/cli.py:*)"]
    else:
        cmd += ["--tools", "Bash", "--allowedTools", "Bash(curl:*)"]
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600,
                          env={**os.environ, "MEMORY_USER_ID": user_id})
    wall = time.time() - t0
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": (proc.stderr or proc.stdout)[-500:], "wall_s": wall}
    u = out.get("usage", {})
    return {
        "ok": not out.get("is_error"), "turns": out.get("num_turns"), "cost": out.get("total_cost_usd", 0.0),
        "wall_s": wall, "result": out.get("result", ""),
        "tokens_in": u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0),
        "tokens_out": u.get("output_tokens", 0), "denials": len(out.get("permission_denials", [])),
        "denied": [d.get("tool_input", {}).get("command", d.get("tool_name")) for d in out.get("permission_denials", [])],
    }


def run_rep(iface: str, rep: int, model: str) -> dict:
    user_id = f"bench-{iface}-{rep}"
    mem = Neo4jMemory(user_id=user_id)
    mem.reset()
    servers = {}
    if iface == "mcp":
        servers["graph-memory"] = {"command": PY, "args": [str(HERE / "mcp_server.py")], "env": {"MEMORY_USER_ID": user_id}}
    cfg = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"mcpServers": servers}, cfg); cfg.close()

    api = None
    if iface == "api":
        api = subprocess.Popen([PY, str(HERE / "api.py")], cwd=ROOT, stdout=subprocess.DEVNULL,
                               env={**os.environ, "MEMORY_USER_ID": user_id, "MEMORY_API_PORT": str(API_PORT)})
        time.sleep(3)
    try:
        s = claude(iface, STORE.format(ctx=CONTEXT[iface]), user_id, model, cfg.name)
        r = claude(iface, RECALL.format(ctx=CONTEXT[iface]), user_id, model, cfg.name)
    finally:
        if api:
            api.terminate()
        os.unlink(cfg.name)

    graph = mem.recall("", hops=1).lower()
    answer = r.get("result", "").lower()
    checks = {
        "answer": all(w in answer for w in ANSWER_MUST),
        "graph": all(w in graph for w in GRAPH_MUST) and not any(w in graph for w in GRAPH_MUST_NOT),
    }
    mem.reset()
    mem._run("MATCH (u:MemoryUser {id: $u}) DELETE u", u=user_id)
    mem.close()
    return {"iface": iface, "rep": rep, "store": s, "recall": r, "checks": checks, "graph": graph}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=2)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--ifaces", default="mcp,cli,api")
    ap.add_argument("--permissive", action="store_true", help="allow all Bash for cli/api")
    args = ap.parse_args()
    global PERMISSIVE
    PERMISSIVE = args.permissive

    runs = []
    for rep in range(1, args.reps + 1):
        for iface in args.ifaces.split(","):
            res = run_rep(iface, rep, args.model)
            runs.append(res)
            s, r = res["store"], res["recall"]
            print(f"{iface} rep{rep}: answer={res['checks']['answer']} graph={res['checks']['graph']} "
                  f"turns={s.get('turns')}+{r.get('turns')} cost=${s.get('cost', 0) + r.get('cost', 0):.3f} "
                  f"wall={s['wall_s'] + r['wall_s']:.0f}s denials={s.get('denials')}+{r.get('denials')}"
                  + (f" ERROR={s.get('error') or r.get('error')}" if 'error' in s or 'error' in r else ""), flush=True)
    (HERE / "results").mkdir(exist_ok=True)
    out = HERE / "results" / f"bench-{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(runs, indent=2))
    print(f"saved {out}")


if __name__ == "__main__":
    sys.exit(main())
