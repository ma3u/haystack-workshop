"""
Exercise 10: Measure answer accuracy, vector RAG vs GraphRAG.

Eval set: data/graphrag_eval.json, 22 questions with reference answers
  - 8 single-hop  (answer stated in one passage)
  - 14 multi-hop  (answer needs 2-3 facts from different passages, incl. 2 list questions)

Variants (see graphrag_pipelines.py), all with the same LLM and the same prompt template:
  vector     exercise 08 setup: vector top 10 -> cross-encoder top 4
  vector_k8  vector top 8, no reranker: "does just giving more passages help?"
  graphrag   vector + graph facts

Metrics per variant:
  accuracy        LLM judge: does the answer match the reference? (list questions need every item)
  context_hit     every must_include string occurs in the prompt context (retrieval recall, no LLM)
  ctx_tokens      average prompt size (chars / 4), the cost side of the trade-off

Run:  python 09_graphrag.py               # once, builds the graph
      python 10_eval_rag_vs_graphrag.py   # add --runs 3 to average out LLM randomness
"""
import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from statistics import mean

from haystack.dataclasses import ChatMessage
from openai import RateLimitError

from graphrag_pipelines import ENTITY_LABEL, EVAL_SET, VARIANTS, build_query, make_store, query_inputs
from llm import chat_generator

RESULTS = Path(__file__).parent / "results" / "graphrag_eval_results.json"

JUDGE_PROMPT = """You grade answers to questions about a fictional company.

Question: {question}
Reference answer: {reference}
Candidate answer: {candidate}

The candidate is correct if it gives the same answer as the reference. Extra correct detail is fine.
For list answers, every item of the reference must be present. "I don't know" is incorrect.
Return JSON: {{"correct": true or false, "reason": "<one short sentence>"}}"""


def retrieve(store, variant: str, questions: list[dict]) -> list[dict]:
    """Run retrieval + prompt building (no LLM) sequentially; local models + Neo4j are fast."""
    pipe = build_query(store, variant, with_llm=False)
    rows = []
    for q in questions:
        out = pipe.run(query_inputs(pipe, q["question"]))
        messages = out["prompt"]["prompt"]
        context = messages[0].text
        rows.append(
            {
                "id": q["id"],
                "messages": messages,
                "context_hit": all(s in context.lower() for s in q["must_include"]),
                "ctx_tokens": len(context) // 4,
            }
        )
    return rows


def ask(messages: list[ChatMessage], json_mode: bool = False) -> str:
    """One LLM call. Azure deployments often have low RPM/TPM quotas, so back off on 429 for longer
    than the OpenAI client's built-in retries (which honour short Retry-After headers only)."""
    kwargs = {"generation_kwargs": {"response_format": {"type": "json_object"}}} if json_mode else {}
    llm = chat_generator(max_retries=3, **kwargs)
    for attempt in range(8):
        try:
            return llm.run(messages=messages)["replies"][0].text or ""
        except RateLimitError:
            time.sleep(min(15 * (attempt + 1), 60))
    raise RuntimeError("Still rate limited after 8 attempts; lower --workers.")


def answer(messages: list[ChatMessage]) -> str:
    return ask(messages)


def judge(q: dict, candidate: str) -> dict:
    prompt = JUDGE_PROMPT.format(question=q["question"], reference=q["answer"], candidate=candidate)
    reply = ask([ChatMessage.from_user(prompt)], json_mode=True)
    try:
        verdict = json.loads(reply)
        return {"correct": bool(verdict["correct"]), "reason": verdict.get("reason", "")}
    except (json.JSONDecodeError, KeyError):
        return {"correct": False, "reason": f"unparseable judge reply: {reply[:80]}"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=1, help="repeat generation+judging to average LLM randomness")
    parser.add_argument("--workers", type=int, default=8, help="parallel LLM calls; lower it on 429 rate limits")
    args = parser.parse_args()

    questions = json.loads(EVAL_SET.read_text())
    by_id = {q["id"]: q for q in questions}
    store = make_store()
    if not store.run_cypher(f"MATCH (e:{ENTITY_LABEL}) RETURN count(e) AS c")[0]["c"]:
        raise SystemExit("Graph is empty. Run `python 09_graphrag.py` first.")

    results = {}
    for variant in VARIANTS:
        print(f"[{variant}] retrieving ...", flush=True)
        rows = retrieve(store, variant, questions)
        # LLM calls are network-bound: answer and judge in parallel, throttled by --workers.
        jobs = [(row, run) for run in range(args.runs) for row in rows]
        with ThreadPoolExecutor(args.workers) as pool:
            answers = list(pool.map(lambda job: answer(job[0]["messages"]), jobs))
            verdicts = list(pool.map(lambda ja: judge(by_id[ja[0][0]["id"]], ja[1]), zip(jobs, answers)))
        for row in rows:
            row["answers"], row["verdicts"] = [], []
            del row["messages"]
        for (row, _), ans, ver in zip(jobs, answers, verdicts):
            row["answers"].append(ans)
            row["verdicts"].append(ver)
        for row in rows:
            row["accuracy"] = mean(v["correct"] for v in row["verdicts"])
        results[variant] = rows
    store.close()

    def acc(rows, pred=lambda q: True):
        sel = [r for r in rows if pred(by_id[r["id"]])]
        return 100 * mean(r["accuracy"] for r in sel)

    print(f"\nAccuracy over {len(questions)} questions x {args.runs} run(s) (LLM judge)\n")
    print(f"{'variant':<10} {'overall':>8} {'1-hop':>7} {'multi-hop':>10} {'ctx_hit':>8} {'ctx_tokens':>11}")
    for variant, rows in results.items():
        print(
            f"{variant:<10} {acc(rows):>7.0f}% {acc(rows, lambda q: q['hops'] == 1):>6.0f}% "
            f"{acc(rows, lambda q: q['hops'] > 1):>9.0f}% {100 * mean(r['context_hit'] for r in rows):>7.0f}% "
            f"{mean(r['ctx_tokens'] for r in rows):>11.0f}"
        )

    print("\nPer question (accuracy across runs):")
    print(f"{'id':<4} {'hops':>4}  " + "  ".join(f"{v:>9}" for v in results) + "  question")
    for i, q in enumerate(questions):
        cells = "  ".join(f"{results[v][i]['accuracy'] * 100:>8.0f}%" for v in results)
        print(f"{q['id']:<4} {q['hops']:>4}  {cells}  {q['question'][:60]}")

    RESULTS.parent.mkdir(exist_ok=True)
    RESULTS.write_text(json.dumps({"runs": args.runs, "results": results}, indent=2))
    print(f"\nDetails (answers + judge reasons): {RESULTS}")


if __name__ == "__main__":
    main()
