# /// script
# requires-python = ">=3.10"
# dependencies = ["neo4j>=5.26.0", "litellm>=1.40.0"]
# ///
"""
Add embeddings to MKG :Learning and :Observation nodes that were written without one
(for example while no embedding model was reachable). MKG has no backfill command of its own.

Uses MKG's own credentials file and embedding code, so the vectors match what the hooks write:
learnings embed `text`, observations embed title + facts + narrative.

    uv run --script claude_memory/backfill_embeddings.py            # dry run: counts only
    uv run --script claude_memory/backfill_embeddings.py --apply
"""
import argparse
import sys
from pathlib import Path

MKG_HOOKS = Path.home() / ".claude/plugins/cache/mkg/meta-knowledge-graph/0.1.43/hooks"
sys.path.insert(0, str(MKG_HOOKS))

import project_common as pc  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

BATCH = 50


def observation_text(row: dict) -> str:
    """Same input as process_project.attach_observation_embeddings."""
    facts = row.get("facts") or []
    if isinstance(facts, str):
        facts = [facts]
    parts = (str(row.get("title") or ""), " ".join(facts), str(row.get("narrative") or ""))
    return "\n".join(p for p in parts if p.strip())


def backfill(driver, label: str, fetch: str, to_text, apply: bool) -> tuple[int, int]:
    rows = [r.data() for r in driver.execute_query(fetch).records]
    if not apply:
        return len(rows), 0
    written = 0
    for i in range(0, len(rows), BATCH):
        chunk = [r for r in rows[i:i + BATCH] if to_text(r)]
        vectors = pc.embed_texts([to_text(r) for r in chunk])
        updates = [{"id": r["id"], "embedding": v} for r, v in zip(chunk, vectors) if v]
        if updates:
            driver.execute_query(
                f"UNWIND $rows AS row MATCH (n:{label} {{id: row.id}}) WHERE n.embedding IS NULL "
                "SET n.embedding = row.embedding",
                rows=updates,
            )
        written += len(updates)
    return len(rows), written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write embeddings (default: dry run)")
    args = ap.parse_args()

    env = pc.load_mkg_env()
    print(f"config: {env}  model: {pc.embedding_model()}  dims: {pc.embedding_dimensions()}")
    import os
    with GraphDatabase.driver(os.environ["NEO4J_URI"],
                              auth=(os.environ.get("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"])) as d:
        for label, fetch, to_text in (
            ("Learning",
             "MATCH (n:Learning) WHERE n.embedding IS NULL AND n.text IS NOT NULL RETURN n.id AS id, n.text AS text",
             lambda r: (r.get("text") or "").strip()),
            ("Observation",
             "MATCH (n:Observation) WHERE n.embedding IS NULL "
             "RETURN n.id AS id, n.title AS title, n.facts AS facts, n.narrative AS narrative",
             observation_text),
        ):
            missing, written = backfill(d, label, fetch, to_text, args.apply)
            print(f"{label}: {missing} without embedding" + (f", {written} written" if args.apply else ""))


if __name__ == "__main__":
    main()
