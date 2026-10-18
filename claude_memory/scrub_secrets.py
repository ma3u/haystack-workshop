# /// script
# requires-python = ">=3.10"
# dependencies = ["neo4j>=5.26.0"]
# ///
"""
Scrub secrets that MKG's live hooks wrote into the memory graph. Dry run by default.

MKG's live logging (hooks/log_event.py) stores prompts, tool inputs and tool responses as they are; only
import_claude_history.py redacts. This script applies the importer's redaction (every secret value from
~/projects/**/.env* by exact match, plus the token patterns in SECRET_PATTERNS) to the text the hooks and
the background services store, without changing the plugin:

  :SessionEvent  prompt, tool_input, tool_response, tool_error, last_assistant_message, transcript
  :Learning      text
  :Observation   title, narrative, facts

Output never contains a secret: per hit it prints the node, the property, the rule that matched and the
redacted text around the [REDACTED:...] marker.

    uv run --script claude_memory/scrub_secrets.py                    # dry run: report what would change
    uv run --script claude_memory/scrub_secrets.py --since 2026-10-18 # only events from that day on
    uv run --script claude_memory/scrub_secrets.py --apply            # write the redacted text back

--apply only rewrites the matching properties of the matching nodes. event_id is not touched (MKG hashes the
original hook payload once, at write time), so redacted events are not logged again.
"""
import argparse
import re
from collections import Counter
from pathlib import Path

from neo4j import GraphDatabase

import import_claude_history as importer
from import_claude_history import known_secret_values  # same rules as the importer

MKG_ENV = Path.home() / ".config" / "meta-knowledge-graph" / ".env"
TARGETS = [
    ("SessionEvent", "event_id", "timestamp",
     ["prompt", "tool_input", "tool_response", "tool_error", "last_assistant_message", "transcript"]),
    ("Learning", "id", "created_at", ["text"]),
    ("Observation", "id", "started_at", ["title", "narrative", "facts"]),
]
MARKER = re.compile(r"\[REDACTED:[A-Za-z0-9_]+\]")


def redact(text: str, known: dict[str, str], stats: Counter) -> str:
    """importer.redact (same .env values, same SECRET_PATTERNS) with two guards for text already in the graph:
    - existing [REDACTED:x] markers are left alone (url_credentials would read "[REDACTED:x]@" as user:password@),
      so a second run finds nothing
    - a url_credentials match spanning an escaped newline (JSON "\\n" in a tool input) is not a URL: skipped
    - an assignment value ends at a backslash (JSON-escaped text: "KEY=value\\nNEXT=..."), and a value that is
      masked or code ("****", "key['x']", "{...}", backticks) is left as it is. On 2026-10-18 these were
      all 106 assignment hits in the graph."""
    text = MARKER.sub(lambda m: m.group(0).replace(":", "-", 1), text)
    for val, name in known.items():
        if val in text:
            stats[f"env:{name}"] += text.count(val)
            text = text.replace(val, f"[REDACTED:{name.split('/')[-1]}]")
    for name, rx in importer.COMPILED:
        def sub(m, name=name):
            if name == "url_credentials" and "\\n" in m.group(0):
                return m.group(0)
            if name == "assignment":
                v = m.group("v")
                value = v.split("\\")[0]
                if len(value) < 8 or re.search(r"[*\[{`]", value):
                    return m.group(0)
                stats[f"pattern:{name}"] += 1
                return m.group(0)[: m.start("v") - m.start()] + f"[REDACTED:{name}]" + v[len(value):]
            stats[f"pattern:{name}"] += 1
            if "v" in m.re.groupindex:
                return m.group(0)[: m.start("v") - m.start()] + f"[REDACTED:{name}]"
            return f"[REDACTED:{name}]"
        text = rx.sub(sub, text)
    return re.sub(r"\[REDACTED-([A-Za-z0-9_]+)\]", r"[REDACTED:\1]", text)


def context(redacted: str) -> str:
    m = MARKER.search(redacted)
    return redacted[max(0, m.start() - 40): m.end() + 10].replace("\n", " ") if m else ""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="write the redacted text back (default: dry run)")
    p.add_argument("--since", help="only nodes with a timestamp at or after this date/time, e.g. 2026-10-18")
    p.add_argument("--verbose", action="store_true", help="one line per node instead of one per group")
    args = p.parse_args()

    env = {k: v.strip().strip("\"'") for k, v in re.findall(r"^([A-Z0-9_]+)=(.*)$", MKG_ENV.read_text(), re.M)}
    db = env.get("NEO4J_DATABASE", "neo4j")
    known = known_secret_values()
    rules = Counter()
    groups = {}  # (label.prop, session, rules) -> [count, sample context]
    changes = []  # (label, key_prop, key, {prop: new_value})
    with GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"])) as driver:
        for label, key_prop, ts_prop, props in TARGETS:
            since = "AND n[$ts] >= datetime($since)" if args.since else ""
            rows = driver.execute_query(
                f"MATCH (n:{label}) WHERE n[$k] IS NOT NULL {since} RETURN n[$k] AS key, n {{.*}} AS p",
                k=key_prop, ts=ts_prop, since=args.since, database_=db).records
            scanned = 0
            for r in rows:
                new = {}
                for prop in props:
                    old = r["p"].get(prop)
                    if old is None:
                        continue
                    scanned += 1
                    stats = Counter()
                    if isinstance(old, list):
                        value = [redact(x, known, stats) if isinstance(x, str) else x for x in old]
                    elif isinstance(old, str):
                        value = redact(old, known, stats)
                    else:
                        continue
                    if stats:
                        new[prop] = value
                        rules.update(stats)
                        shown = context(" ".join(value) if isinstance(value, list) else value)
                        if args.verbose:
                            print(f"  {label}.{prop:<22} {str(r['key'])[:60]:<60} {dict(stats)}\n      ...{shown}...")
                        owner = str(r["p"].get("session_id") or r["key"]).replace("claude_code:", "")[:8]
                        g = groups.setdefault((f"{label}.{prop}", owner, ",".join(sorted(stats))), [0, shown])
                        g[0] += 1
                if new:
                    changes.append((label, key_prop, r["key"], new))
            print(f"{label}: {len(rows)} nodes, {scanned} text properties scanned, "
                  f"{sum(c[0] == label for c in changes)} nodes with secrets")
        if not args.verbose:
            for (where, owner, rule), (n, shown) in sorted(groups.items()):
                print(f"  {n:>4}x {where:<28} {owner:<9} {rule}\n         e.g. ...{shown}...")
        print(f"\nrules matched: {dict(sorted(rules.items()))}")
        if not changes:
            print("graph is clean: nothing to redact")
            return
        if not args.apply:
            print(f"dry run: {len(changes)} nodes would be redacted. Re-run with --apply to write.")
            return
        for label, key_prop, key, new in changes:
            driver.execute_query(f"MATCH (n:{label}) WHERE n[$k] = $key SET n += $new",
                                 k=key_prop, key=key, new=new, database_=db)
        print(f"redacted {len(changes)} nodes")


if __name__ == "__main__":
    main()
