"""
Load the exercise 10 eval results into Neo4j, so a dashboard can chart vector RAG vs GraphRAG next to the graph.

  (:EvalVariant {name})-[:ANSWERED {accuracy, context_hit, ctx_tokens, runs, answer, reason}]->(:EvalQuestion)
  (:EvalQuestion {id, hops, question, answer})-[:ABOUT]->(:GraphRagEntity)   # entities named in must_include

Idempotent (MERGE). Target is NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD, like the other exercises.

Run:  python eval_to_neo4j.py    # after 09_graphrag.py (for the ABOUT links) and 10_eval_rag_vs_graphrag.py
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

from graphrag_pipelines import ENTITY_LABEL, EVAL_SET

load_dotenv()
RESULTS = Path(__file__).parent / "results" / "graphrag_eval_results.json"


def main() -> None:
    questions = json.loads(EVAL_SET.read_text())
    data = json.loads(RESULTS.read_text())
    answered = [
        {
            "variant": variant,
            "id": r["id"],
            "accuracy": float(r["accuracy"]),
            "context_hit": r["context_hit"],
            "ctx_tokens": r["ctx_tokens"],
            "runs": len(r["answers"]),
            "answer": r["answers"][0],
            "reason": r["verdicts"][0]["reason"],
        }
        for variant, rows in data["results"].items()
        for r in rows
    ]
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687"),
        auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.environ["NEO4J_PASSWORD"]),
    )
    with driver.session() as s:
        s.run("CREATE CONSTRAINT eval_question_id IF NOT EXISTS FOR (n:EvalQuestion) REQUIRE n.id IS UNIQUE")
        s.run("CREATE CONSTRAINT eval_variant_name IF NOT EXISTS FOR (n:EvalVariant) REQUIRE n.name IS UNIQUE")
        s.run(
            "UNWIND $qs AS q MERGE (n:EvalQuestion {id: q.id}) "
            "SET n.hops = q.hops, n.question = q.question, n.answer = q.answer, n.must_include = q.must_include",
            qs=questions,
        )
        s.run(
            f"MATCH (q:EvalQuestion) UNWIND q.must_include AS k MATCH (e:{ENTITY_LABEL} {{key: k}}) MERGE (q)-[:ABOUT]->(e)"
        )
        s.run(
            "UNWIND $rows AS r MERGE (v:EvalVariant {name: r.variant}) WITH v, r "
            "MATCH (q:EvalQuestion {id: r.id}) MERGE (v)-[a:ANSWERED]->(q) "
            "SET a.accuracy = r.accuracy, a.context_hit = r.context_hit, a.ctx_tokens = r.ctx_tokens, "
            "a.runs = r.runs, a.answer = r.answer, a.reason = r.reason",
            rows=answered,
        )
        stats = s.run(
            "MATCH (v:EvalVariant)-[a:ANSWERED]->(q:EvalQuestion) "
            "RETURN v.name AS variant, round(100 * avg(a.accuracy)) AS accuracy, count(q) AS questions ORDER BY variant"
        ).data()
        about = s.run("MATCH (:EvalQuestion)-[r:ABOUT]->() RETURN count(r) AS c").single()["c"]
    driver.close()
    print(f"Loaded {len(questions)} questions x {data['runs']} run(s), {about} ABOUT links")
    for row in stats:
        print(f"  {row['variant']:<10} {row['accuracy']:.0f}%  ({row['questions']} questions)")


if __name__ == "__main__":
    main()
