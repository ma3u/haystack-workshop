# /// script
# requires-python = ">=3.10"
# dependencies = ["neo4j>=5.26.0", "litellm>=1.40.0"]
# ///
"""
Recall from the MKG memory across all projects, with a real relevance score and a "no match" answer.

MKG's project_get_context searches one project and ranks by reciprocal-rank fusion (0.015-0.033 for hits and
misses alike), and always returns its 5 latest observations. This script embeds the question with MKG's own
embedding config and runs the :Learning and :Observation vector indexes over every project. The score is
Neo4j's cosine similarity mapped to 0..1; results below --min-score are dropped, so an empty result means
"not in memory".

    uv run --script claude_memory/recall_all.py "Which port does the memory Neo4j use?"
    uv run --script claude_memory/recall_all.py --min-score 0.8 --top 3 --json "..."

Only reads the graph. Blocked, rejected and superseded learnings are never returned.
"""
import argparse
import json
import os
import sys
from pathlib import Path

MKG_HOOKS = Path.home() / ".claude/plugins/cache/mkg/meta-knowledge-graph/0.1.43/hooks"
sys.path.insert(0, str(MKG_HOOKS))

import project_common as pc  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

# Calibrated with text-embedding-3-small on 2026-10-18: correct hits scored 0.78-0.84, the best match for
# off-topic questions ("kubernetes helm chart", "capital of Peru") 0.55-0.69.
MIN_SCORE = 0.75
STATUSES = ["approved", "candidate"]

QUERY = """
CALL () {
  MATCH (node:Learning)
  SEARCH node IN (VECTOR INDEX project_learning_vector FOR $vector LIMIT $k) SCORE AS score
  WHERE node.status IN $statuses
  RETURN 'learning' AS kind, node.id AS id, node.project_id AS project, node.status AS status,
         node.text AS text, score
  UNION ALL
  MATCH (node:Observation)
  SEARCH node IN (VECTOR INDEX project_observation_vector FOR $vector LIMIT $k) SCORE AS score
  RETURN 'observation' AS kind, node.id AS id, node.project_id AS project, node.type AS status,
         node.title + ': ' + reduce(s = '', f IN coalesce(node.facts, []) | s + f + '; ')
         + coalesce(node.narrative, '') AS text, score
}
WITH * WHERE score >= $min_score
ORDER BY score DESC LIMIT $top
// Graph step: a problem episode is usually resolved in the next episode of the timeline. Add the :NEXT
// neighbour of each observation hit, ranked just below it.
CALL (kind, id, score) {
  OPTIONAL MATCH (:Observation {id: id})-[:NEXT]->(n:Observation) WHERE kind = 'observation'
  RETURN n, score - 0.001 AS next_score
}
WITH collect({kind: kind, id: id, project: project, status: status, text: text, score: score}) +
     collect(CASE WHEN n IS NOT NULL THEN {kind: 'next', id: n.id, project: n.project_id, status: n.type,
       text: n.title + ': ' + reduce(s = '', f IN coalesce(n.facts, []) | s + f + '; ') + coalesce(n.narrative, ''),
       score: next_score} END) AS rows
UNWIND rows AS r
WITH r ORDER BY r.score DESC
WITH r.id AS id, max(r.score) AS score, collect(r)[0] AS r
RETURN r.kind AS kind, id, r.project AS project, r.status AS status, r.text AS text, score
ORDER BY score DESC LIMIT $top
"""


def recall(question: str, *, top: int = 5, min_score: float = MIN_SCORE, driver=None) -> list[dict]:
    """Rows sorted by cosine score; empty list when nothing in memory is close enough."""
    vector = pc.embed_texts([question])[0]
    if vector is None:
        raise RuntimeError(f"embedding failed for model {pc.embedding_model()}")
    own = driver is None
    if own:
        pc.load_mkg_env()
        driver = GraphDatabase.driver(os.environ["NEO4J_URI"],
                                      auth=(os.environ.get("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]))
    try:
        records = driver.execute_query(QUERY, vector=vector, k=max(50, top * 4), statuses=STATUSES,
                                       min_score=min_score, top=top, routing_="r").records
        return [r.data() for r in records]
    finally:
        if own:
            driver.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--min-score", type=float, default=MIN_SCORE)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    pc.load_mkg_env()
    rows = recall(args.question, top=args.top, min_score=args.min_score)
    if args.json:
        print(json.dumps(rows, indent=2))
    elif not rows:
        print(f"no match (nothing scores >= {args.min_score})")
    for r in [] if args.json else rows:
        print(f"{r['score']:.3f}  {r['project']:<18} {r['kind']:<11} {r['text'][:160]}")


if __name__ == "__main__":
    main()
