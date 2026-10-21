# /// script
# requires-python = ">=3.10"
# dependencies = ["neo4j>=5.26.0"]
# ///
"""
Audit: are all prompts of a project's Claude Code sessions in the MKG memory graph?

Compares the prompts in the raw transcripts (~/.claude/projects/<project>/*.jsonl) with the UserPromptSubmit
events in the graph, per session. Read-only on both sides.

Each prompt is classified with the same rules as the "Audit" query in cypher/questions.cypher:
  noise      injected by Claude Code, not typed: <task-notification>, <bash-input>/<bash-stdout>,
             <system-reminder>, <agent-message>, <command-name>, <side-sessions-event>, compaction summaries, "[Request interrupted"
  machine    written by a program or another session: headless runs, "Task/Background (from the session
             that started this one)"
  developer  everything else, including queued prompts typed while the agent was working
Developer and machine prompts both count as prompts of the session; noise is reported separately.

    uv run --script claude_memory/prompt_audit.py
    uv run --script claude_memory/prompt_audit.py --exclude 24ebda85 --show-missing
"""
import argparse
import json
import re
from collections import Counter
from pathlib import Path

from neo4j import GraphDatabase

from import_claude_history import known_secret_values, redact  # same redaction as the importer

HOME = Path.home()
MKG_ENV = HOME / ".config" / "meta-knowledge-graph" / ".env"
DEFAULT_DIR = HOME / ".claude" / "projects" / "-Users-ma3u-projects-haystack-workshop"

INJECTED = re.compile(
    r"<(system-reminder|local-command-stdout|local-command-caveat|command-message|command-args)>[\s\S]*?</\1>")
NOISE = re.compile(r"^(<task-notification>|<bash-input>|<bash-stdout>|<bash-stderr>|<system-reminder>|"
                   r"<agent-message|<local-command-stdout>|<command-name>|<side-sessions-event|\[Request interrupted|"
                   r"This session is being continued from a previous conversation)")
MACHINE = re.compile(r"^(Task|Background) \(from the session that started this one\)|"
                     r"^Answer ONLY from the meta-knowledge-graph MCP tool|^Without using any tools:")


def classify(raw: str) -> str:
    text = raw.lstrip()
    if NOISE.match(text):
        return "noise"
    return "machine" if MACHINE.search(text) else "developer"


def key(text: str) -> str:
    """Comparable form: injected blocks removed (the importer drops them, live hooks keep them), whitespace
    collapsed, the first 60 characters (redaction rewrites secrets later in a prompt, rarely its start)."""
    return re.sub(r"\s+", " ", INJECTED.sub("", text)).strip().lower()[:60]


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def transcript_prompts(path: Path) -> list[dict]:
    out = []
    for line in path.open(errors="ignore"):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        att = r.get("attachment") or {}
        if r.get("type") == "user" and not r.get("isMeta") and not r.get("isCompactSummary"):
            text = text_of(r.get("message", {}).get("content"))
        elif r.get("type") == "attachment" and att.get("type") == "queued_command" and att.get("commandMode") == "prompt":
            text = text_of(att.get("prompt"))  # typed while the agent was working, consumed mid-turn
        else:
            continue
        if not text.strip() or not key(text):
            continue  # tool results only, or only injected context
        out.append({"ts": r.get("timestamp"), "text": text, "kind": classify(INJECTED.sub("", text))})
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="Claude Code project transcript directory")
    p.add_argument("--exclude", nargs="*", default=[], help="session id prefixes to leave out (e.g. this one)")
    p.add_argument("--show-missing", action="store_true", help="print the prompts the graph does not have")
    args = p.parse_args()

    env = {k: v.strip().strip("\"'") for k, v in re.findall(r"^([A-Z0-9_]+)=(.*)$", MKG_ENV.read_text(), re.M)}
    driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))
    sessions = [f for f in sorted(args.dir.glob("*.jsonl")) if not any(f.stem.startswith(x) for x in args.exclude)]

    total = Counter()
    missing = []
    print(f"{'session':<10} {'dev':>4} {'mach':>4} {'noise':>5} | {'in graph':>8} {'missing':>7} | graph extra noise")
    for f in sessions:
        raw = transcript_prompts(f)
        graph = [r["prompt"] for r in driver.execute_query(
            "MATCH (:Session {session_id: $sid})-[:HAS_EVENT]->(e:SessionEvent {event_name: 'UserPromptSubmit'}) "
            "RETURN e.prompt AS prompt", sid=f.stem, database_=env.get("NEO4J_DATABASE", "neo4j")).records]
        have = Counter(key(t) for t in graph if t)
        c = Counter(x["kind"] for x in raw)
        found = 0
        for x in raw:
            if x["kind"] == "noise":
                continue
            if have[key(x["text"])] > 0:
                have[key(x["text"])] -= 1
                found += 1
            else:
                missing.append((f.stem[:8], x))
        graph_noise = sum(classify(INJECTED.sub("", t or "")) == "noise" for t in graph)
        wanted = c["developer"] + c["machine"]
        print(f"{f.stem[:8]:<10} {c['developer']:>4} {c['machine']:>4} {c['noise']:>5} | {found:>8} "
              f"{wanted - found:>7} | {graph_noise}")
        total.update(developer=c["developer"], machine=c["machine"], noise=c["noise"], found=found,
                     graph_noise=graph_noise)
    wanted = total["developer"] + total["machine"]
    print(f"\n{len(sessions)} sessions: {wanted} prompts in the transcripts ({total['developer']} developer, "
          f"{total['machine']} machine-written), {total['found']} in the graph, {wanted - total['found']} missing. "
          f"Noise not counted: {total['noise']} in the transcripts, {total['graph_noise']} stored as prompts.")
    if args.show_missing:
        known = known_secret_values()  # raw transcripts are not redacted: never print a prompt unredacted
        for sid, x in missing:
            print(f"  missing {sid} {x['ts']} {x['kind']:<9} {redact(key(x['text']), known, Counter())!r}")
    driver.close()


if __name__ == "__main__":
    main()
