"""
Import Claude Code history (~/.claude/projects/**/*.jsonl) into the meta-knowledge-graph (MKG) memory graph.

Reuses MKG's own hook code (log_event.log_event), so imported sessions get exactly the same graph shape as
sessions the plugin captures live: (:Session)-[:FIRST_EVENT|NEXT|LATEST_EVENT]->(:SessionEvent), projects, users,
subagents. Steps:

  scan      gitleaks over the raw transcripts + exact-match check for every secret value in ~/projects/**/.env*
  sanitize  filter + redact into a staging dir, then re-scan the staged copy with gitleaks (must be clean)
  load      replay the staged records as hook events (original timestamps) into Neo4j
  enrich    run MKG's enrich_events.py (tool-call graph, no LLM) for every imported session

Filtering (sanitize):
  kept     user prompts, assistant text, tool calls (name + input), tool results (truncated)
  dropped  thinking blocks, images, attachments, API request/blob/meta records, compact summaries,
           <system-reminder> blocks and other injected context, sessions shorter than --min-prompts

Usage (only needs the neo4j driver; gitleaks on PATH):
  RUN="uv run --no-project --with neo4j python claude_memory/import_claude_history.py"
  $RUN scan
  $RUN sanitize
  $RUN load   --uri bolt://127.0.0.1:7689
  $RUN enrich --uri bolt://127.0.0.1:7689
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

HOME = Path.home()
SOURCE = HOME / ".claude" / "projects"
STAGE = HOME / ".mkg-import"
CLEAN = STAGE / "clean"
MKG_HOOKS = HOME / ".claude" / "plugins" / "marketplaces" / "mkg" / "hooks"
WORKSHOP_ENV = Path(__file__).resolve().parents[1] / ".env"

MAX_TOOL_RESULT = 4000  # same cap MKG applies to live tool responses
MAX_TOOL_INPUT = 4000
WORKSHOP_LABELS = ("GraphRagChunk", "GraphRagEntity", "HaystackRagDocument", "HaystackDocument")

# Token formats. Known .env values are redacted separately (exact match), these catch everything else.
SECRET_PATTERNS = [
    ("google_api_key", r"AIza[0-9A-Za-z_\-]{35}"),
    ("google_auth_key", r"\bAQ\.[0-9A-Za-z_\-]{30,}"),
    ("anthropic_key", r"sk-ant-[0-9A-Za-z_\-]{20,}"),
    ("openai_key", r"\bsk-(?:proj-|svcacct-)?[0-9A-Za-z_\-]{20,}"),
    ("github_token", r"\b(?:gh[pousr]_[0-9A-Za-z]{30,}|github_pat_[0-9A-Za-z_]{40,})"),
    ("slack_token", r"\bxox[abprs]-[0-9A-Za-z\-]{10,}"),
    ("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("elevenlabs_key", r"\bsk_[0-9a-f]{40,}\b"),
    ("jwt", r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]{10,}"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("url_credentials", r"(?<=://)[^/\s:@'\"]+:[^/\s@'\"]+(?=@)"),
    # KEY=value / "password": "value" assignments with a secret-looking name: only the value (group v) is replaced
    ("assignment", r"(?i)(?:api[_-]?key|secret|token|password|passwd|pwd)[\"']?\s{0,3}[:=]\s{0,3}[\"']?"
                   r"(?P<v>(?!\[REDACTED)(?!your-|<|\$\{?|os\.|Secret\.|None\b|null\b|true\b|false\b)[^\s\"',;)}\]]{8,})"),
]
COMPILED = [(name, re.compile(p)) for name, p in SECRET_PATTERNS]

INJECTED_BLOCKS = re.compile(
    r"<(system-reminder|local-command-stdout|local-command-caveat|command-message|command-args)>[\s\S]*?</\1>"
)


def known_secret_values() -> dict[str, str]:
    """Every secret-looking value from .env files under ~/projects (names returned, values never printed)."""
    values = {}
    for env in list((HOME / "projects").glob("**/.env*"))[:200]:
        if not env.is_file() or "node_modules" in env.parts or env.name.endswith(".example"):
            continue
        for key, val in re.findall(r"^\s*(?:export\s+)?([A-Za-z0-9_]+)\s*=\s*(.*)$", env.read_text(errors="ignore"), re.M):
            val = val.strip().strip("\"'")
            if not re.search(r"(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASS|PWD)(?:$|_)", key, re.I):
                continue  # e.g. KEYCLOAK_INTERNAL_URL is not a secret
            if len(val) < 8 or val.startswith(("your", "<", "${", "http")) or val.isalpha():
                continue  # plain words ("password") are caught by the assignment pattern, not by global replace
            values[val] = f"{env.parent.name}/{key}"
    return values


def redact(text: str, known: dict[str, str], stats: Counter) -> str:
    for val, name in known.items():
        if val in text:
            stats[f"env:{name}"] += text.count(val)
            text = text.replace(val, f"[REDACTED:{name.split('/')[-1]}]")
    for name, rx in COMPILED:
        def sub(m, name=name):
            if "v" in m.re.groupindex:
                return m.group(0)[: m.start("v") - m.start()] + f"[REDACTED:{name}]"
            return f"[REDACTED:{name}]"
        text, n = rx.subn(sub, text)
        if n:
            stats[f"pattern:{name}"] += n
    return text


def clean_text(text: str) -> str:
    return INJECTED_BLOCKS.sub("", text).strip()


def gitleaks(path: Path, report: Path) -> list[dict]:
    """Run gitleaks with --redact (secrets never written to the report). The anthropic-beta header
    `oauth-2025-04-20` and a quote attribution in the TwoBreath app ("Authentic PD Thoreau, Walden/...") are
    reviewed false positives of the generic-api-key rule and are allowlisted."""
    cfg = STAGE / "gitleaks.toml"
    cfg.write_text('[extend]\nuseDefault = true\n\n[[allowlists]]\nregexes = [\'\'\'oauth-2025-04-20\'\'\', \'\'\'\\[REDACTED:[a-z_A-Z0-9]+\\]\'\'\']\n\n'
        '[[allowlists]]\nregexTarget = "match"\nregexes = [\'\'\'^Authentic PD Thoreau, \'\'\']\n')
    subprocess.run(
        ["gitleaks", "dir", str(path), "--config", str(cfg), "--redact", "--no-banner", "--log-level", "error",
         "--report-format", "json", "--report-path", str(report), "--exit-code", "0"],
        check=True,
    )
    return json.loads(report.read_text() or "[]")


def summarize(findings: list[dict], root: Path) -> None:
    print(f"  gitleaks findings: {len(findings)}")
    for (rule, f), n in Counter((x["RuleID"], str(Path(x["File"]).relative_to(root))[:90]) for x in findings).most_common(20):
        print(f"    {n:>4}  {rule:<18} {f}")


def cmd_scan(_args) -> None:
    STAGE.mkdir(exist_ok=True)
    files = sorted(SOURCE.rglob("*.jsonl"))
    print(f"{len(files)} transcripts, {sum(f.stat().st_size for f in files) / 1e6:.1f} MB in {SOURCE}")
    summarize(gitleaks(SOURCE, STAGE / "scan_raw.json"), SOURCE)
    known = known_secret_values()
    print(f"  known secret values from .env files: {len(known)}")
    hits = Counter()
    for f in files:
        text = f.read_text(errors="ignore")
        for val, name in known.items():
            if val in text:
                hits[(name, f.parent.name if f.parent.name != "subagents" else f.parents[1].name)] += text.count(val)
    for (name, proj), n in sorted(hits.items()):
        print(f"    {n:>4}x  {name:<40} in {proj}")


def iter_events(path: Path, stats: Counter, known: dict[str, str]):
    """Yield sanitized, filtered records: {kind, ts, ...}. kind in prompt|assistant|tool_use|tool_result."""
    for line in path.open(errors="ignore"):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rtype = rec.get("type")
        if rtype not in ("user", "assistant") or rec.get("isMeta") or rec.get("isCompactSummary") or rec.get("isApiErrorMessage"):
            stats[f"drop:{rtype}"] += 1
            continue
        base = {"ts": rec.get("timestamp"), "cwd": rec.get("cwd"), "session_id": rec.get("sessionId"),
                "agent_id": rec.get("agentId") if rec.get("isSidechain") else None}
        content = rec.get("message", {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        for b in blocks:
            bt = b.get("type")
            if bt == "text":
                text = clean_text(b.get("text") or "")
                if not text or text.startswith("[Request interrupted"):
                    stats["drop:empty_text"] += 1
                    continue
                kind = "prompt" if rtype == "user" else "assistant"
                yield {**base, "kind": kind, "text": redact(text, known, stats)}
            elif bt == "tool_use":
                inp = json.dumps(b.get("input"), ensure_ascii=False)
                if len(inp) > MAX_TOOL_INPUT:
                    inp = inp[:MAX_TOOL_INPUT] + "...[truncated]"
                yield {**base, "kind": "tool_use", "tool_use_id": b.get("id"), "tool_name": b.get("name"),
                       "tool_input": redact(inp, known, stats)}
            elif bt == "tool_result":
                raw = b.get("content")
                if isinstance(raw, list):
                    raw = "\n".join(x.get("text", "") for x in raw if x.get("type") == "text")
                raw = clean_text(raw or "")
                if len(raw) > MAX_TOOL_RESULT:
                    raw = raw[:MAX_TOOL_RESULT] + f"...[truncated {len(raw) - MAX_TOOL_RESULT} chars]"
                yield {**base, "kind": "tool_result", "tool_use_id": b.get("tool_use_id"),
                       "is_error": bool(b.get("is_error")), "text": redact(raw, known, stats)}
            else:  # thinking, image, ...
                stats[f"drop:{bt}"] += 1


def cmd_sanitize(args) -> None:
    known = known_secret_values()
    stats = Counter()
    CLEAN.mkdir(parents=True, exist_ok=True)
    for old in CLEAN.rglob("*.jsonl"):
        old.unlink()
    kept = skipped = 0
    for src in sorted(SOURCE.rglob("*.jsonl")):
        events = list(iter_events(src, stats, known))
        prompts = sum(e["kind"] == "prompt" for e in events)
        is_subagent = src.parent.name == "subagents"
        if not events or (not is_subagent and prompts < args.min_prompts):
            skipped += 1
            continue
        dst = CLEAN / src.relative_to(SOURCE)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
        kept += 1
    print(f"staged {kept} transcripts in {CLEAN} (skipped {skipped} with < {args.min_prompts} prompts)")
    print("  redactions:", dict(sorted((k, v) for k, v in stats.items() if not k.startswith("drop:"))))
    print("  dropped:   ", dict(sorted((k, v) for k, v in stats.items() if k.startswith("drop:"))))
    findings = gitleaks(CLEAN, STAGE / "scan_clean.json")
    summarize(findings, CLEAN)
    leftovers = sum(v in p.read_text() for p in CLEAN.rglob("*.jsonl") for v in known)
    print(f"  known .env values left in staged copy: {leftovers}")
    if findings or leftovers:
        sys.exit("NOT CLEAN: inspect ~/.mkg-import/scan_clean.json, extend SECRET_PATTERNS, re-run sanitize.")
    print("CLEAN: staged copy passes gitleaks and the .env exact-match check.")


def connect_env(uri: str) -> None:
    """MKG reads NEO4J_* / MKG_USER_ID from the environment. Password comes from the workshop .env."""
    env = dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", WORKSHOP_ENV.read_text(), re.M))
    os.environ.update(
        NEO4J_URI=uri,
        NEO4J_USERNAME=env.get("NEO4J_USERNAME", "neo4j").strip(),
        NEO4J_PASSWORD=env["NEO4J_PASSWORD"].strip(),
        NEO4J_DATABASE="neo4j",
    )
    os.environ.setdefault("MKG_USER_ID", os.environ.get("USER", "local-user"))
    from neo4j import GraphDatabase

    with GraphDatabase.driver(uri, auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])) as d:
        labels = {r["label"] for r in d.execute_query("CALL db.labels() YIELD label RETURN label").records}
        n = d.execute_query("MATCH (n) WHERE any(l IN labels(n) WHERE l IN $w) RETURN count(n) AS c",
                            w=list(WORKSHOP_LABELS)).records[0]["c"]
    if n:
        sys.exit(f"{uri} holds workshop data ({n} nodes with {sorted(labels & set(WORKSHOP_LABELS))}); "
                 "refusing to import. Point --uri at the agent-memory instance.")


def hook_payloads(path: Path, parent_session: str):
    """Turn staged records into Claude Code hook payloads, in transcript order."""
    events = [json.loads(line) for line in path.open()]
    agent_id = next((e["agent_id"] for e in events if e.get("agent_id")), None)
    common = {"session_id": parent_session, "transcript_path": None}
    if agent_id:
        common |= {"agent_id": agent_id, "is_subagent": True}
    pending, last_text = {}, None
    cwd = next((e["cwd"] for e in events if e.get("cwd")), None)
    if not agent_id:
        yield events[0]["ts"], {**common, "hook_event_name": "SessionStart", "source": "import", "cwd": cwd}
    for e in events:
        base = {**common, "cwd": e.get("cwd") or cwd}
        if e["kind"] == "prompt":
            if last_text is not None:
                yield e["ts"], {**base, "hook_event_name": "Stop", "last_assistant_message": last_text}
                last_text = None
            yield e["ts"], {**base, "hook_event_name": "UserPromptSubmit", "prompt": e["text"]}
        elif e["kind"] == "assistant":
            last_text = e["text"]
        elif e["kind"] == "tool_use":
            pending[e["tool_use_id"]] = e
            yield e["ts"], {**base, "hook_event_name": "PreToolUse", "tool_name": e["tool_name"],
                            "tool_use_id": e["tool_use_id"], "tool_input": json.loads(e["tool_input"])
                            if not e["tool_input"].endswith("[truncated]") else {"truncated": e["tool_input"]}}
        elif e["kind"] == "tool_result" and e["tool_use_id"] in pending:
            call = pending.pop(e["tool_use_id"])
            payload = {**base, "hook_event_name": "PostToolUse", "tool_name": call["tool_name"],
                       "tool_use_id": e["tool_use_id"], "tool_response": e["text"]}
            if e["is_error"]:
                payload["tool_error"] = e["text"][:500]
            yield e["ts"], payload
    end_ts = events[-1]["ts"]
    # The final Stop carries the staged (sanitized, dialog-filtered by MKG) transcript for learning extraction.
    yield end_ts, {**common, "cwd": cwd, "hook_event_name": "SubagentStop" if agent_id else "Stop",
                   "last_assistant_message": last_text or "", "transcript_path": str(path)}
    if not agent_id:
        yield end_ts, {**common, "hook_event_name": "SessionEnd", "reason": "import", "cwd": cwd}


def cmd_load(args) -> None:
    connect_env(args.uri)
    sys.path.insert(0, str(MKG_HOOKS))
    import log_event as mkg  # MKG's own hook module

    class ReplayClock(dt.datetime):
        current = None

        @classmethod
        def now(cls, tz=None):
            return cls.current

    mkg.datetime = ReplayClock  # events keep their original timestamps
    files = sorted(CLEAN.rglob("*.jsonl"))
    total = 0
    for f in files:
        parent = f.parents[1].name if f.parent.name == "subagents" else f.stem
        n = 0
        for ts, payload in hook_payloads(f, parent):
            ReplayClock.current = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            payload = {k: v for k, v in payload.items() if v is not None}
            mkg.log_event(payload, client="claude_code")
            n += 1
        total += n
        print(f"  {n:>5} events  {f.relative_to(CLEAN)}", flush=True)
    print(f"loaded {total} events from {len(files)} transcripts into {args.uri}")


def cmd_enrich(args) -> None:
    connect_env(args.uri)
    sessions = []
    for f in sorted(CLEAN.rglob("*.jsonl")):
        first = json.loads(f.open().readline())
        # cwd lets MKG resolve the same :Project the events were linked to (else it falls back to the plugin dir)
        sid = (first.get("agent_id") or f.stem) if f.parent.name == "subagents" else f.stem
        sessions.append((sid, first.get("cwd")))
    for sid, cwd in sessions:
        subprocess.run(["uv", "run", "--script", str(MKG_HOOKS / "enrich_events.py"), "--mode", "session",
                        "--session-id", sid, "--limit", "100000"], input=json.dumps({"session_id": sid, "cwd": cwd}),
                       text=True, check=False, env=os.environ)
        print(f"  enriched {sid}", flush=True)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("scan")
    s = sub.add_parser("sanitize")
    s.add_argument("--min-prompts", type=int, default=1)
    for name in ("load", "enrich"):
        x = sub.add_parser(name)
        x.add_argument("--uri", required=True, help="agent-memory instance, e.g. bolt://127.0.0.1:7689")
    args = p.parse_args()
    {"scan": cmd_scan, "sanitize": cmd_sanitize, "load": cmd_load, "enrich": cmd_enrich}[args.cmd](args)


if __name__ == "__main__":
    main()
