"""
Challenge 15 (Exercise 13 / Challenge H): Haystack Enterprise Platform + Neo4j Aura + 3 AI Coding IDEs
(GitHub Copilot, Claude Code, Google Antigravity).

Hackathon Challenge for "Hack Night: Haystack Enterprise Platform" (Berlin, Oct 21, 2026):
  "Sovereign Cross-IDE Engineering Memory & Hybrid GraphRAG on Neo4j Aura + Haystack Enterprise"

Architecture:
1. Three AI Coding IDEs — **Claude Code**, **Google Antigravity**, and **GitHub Copilot** — log local
   session traces to local Neo4j (:7689) via MCP & lifecycle hooks.
2. This pipeline extracts ONLY approved, secret-scrubbed architectural learnings from the 3 IDEs,
   embeds them (384-dim SentenceTransformers), and publishes them to **Neo4j Aura Cloud**
   (`NEO4J_AURA_URI = neo4j+s://ma3u-g993-fhgd.endpoints.neo4j.io`) as both:
   - Vector-indexed `:GraphRagChunk` passages (`graphragchunk_embedding`), and
   - Connected `:GraphRagEntity` / `:REL` knowledge-graph triples linking each IDE to the components,
     decisions, benchmarks, and codewords it produced.
3. Runs the **Haystack Hybrid GraphRAG Query Pipeline** live against **Neo4j Aura** to answer
   multi-hop questions spanning all 3 AI coding IDEs and the workshop architecture.
4. Exports a Haystack Enterprise Studio evaluation set (`build/enterprise_multi_ide_eval.json`) and
   provides `--deploy` to push the Custom Component ZIP and YAML pipelines to the Haystack Enterprise
   Platform (`https://api.cloud.deepset.ai`) once `DEEPSET_CLOUD_API_KEY` is set in `.env`.
"""
import argparse
import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from haystack import Document
from haystack.components.writers import DocumentWriter
from haystack.document_stores.types import DuplicatePolicy
from haystack.utils import Secret
from haystack_integrations.components.embedders.sentence_transformers import (
    SentenceTransformersDocumentEmbedder,
)

from graphrag_pipelines import CHUNK_LABEL, DIM, EMBED_MODEL, build_query, query_inputs
from neo4j_document_store import Neo4jDocumentStore
from neo4j_graph import ENTITY_LABEL, Neo4jGraphWriter

HERE = Path(__file__).resolve().parent
BUILD_DIR = HERE.parent / "build"

# Curated, secret-scrubbed cross-IDE knowledge base + auditable decision provenance graph
# tracking Multiple Team Members, AI Coding IDEs, Tools Used, and Data/Artifacts Used.
CROSS_IDE_PASSAGES = [
    {
        "id": "ide-claude-code-1",
        "ide": "Claude Code",
        "decision_id": "DEC-01",
        "team_member": "Alice Chen (Staff AI Architect)",
        "session_id": "e1a5a822",
        "tools_used": ["bench.py", "meta-knowledge-graph MCP", "recall_all.py"],
        "data_used": ["claude_memory/eval/results.json", "learning:haystack-workshop:c49e4e94ebbb2329"],
        "content": (
            "Decision DEC-01 by Alice Chen (Staff AI Architect) in Claude Code (session e1a5a822): "
            "Standardized on the meta-knowledge-graph MCP server plus UserPromptSubmit hook (recall_all.py, "
            "cosine >= 0.75) for cross-agent memory retrieval. Verified using tool bench.py against dataset "
            "claude_memory/eval/results.json, achieving a 3/3 (100%) pass rate with 23.7K input tokens "
            "($0.024, 23s) vs CLI (47K tokens) and HTTP API (39K tokens), and recalled codeword BLUE-HERON-42 "
            "stored by Google Antigravity."
        ),
        "entities": [
            {"name": "Alice Chen", "type": "Person"},
            {"name": "DEC-01", "type": "Other"},
            {"name": "Claude Code", "type": "Organization"},
            {"name": "MCP Server", "type": "Technology"},
            {"name": "recall_all.py", "type": "Technology"},
            {"name": "bench.py", "type": "Technology"},
            {"name": "claude_memory/eval/results.json", "type": "Other"},
            {"name": "BLUE-HERON-42", "type": "Other"},
            {"name": "Google Antigravity", "type": "Organization"},
        ],
        "relations": [
            {"source": "Alice Chen", "relation": "USED_IDE", "target": "Claude Code"},
            {"source": "Alice Chen", "relation": "MADE_DECISION", "target": "DEC-01"},
            {"source": "DEC-01", "relation": "VERIFIED_WITH_TOOL", "target": "bench.py"},
            {"source": "DEC-01", "relation": "VERIFIED_WITH_TOOL", "target": "recall_all.py"},
            {"source": "DEC-01", "relation": "GROUNDED_IN_DATA", "target": "claude_memory/eval/results.json"},
            {"source": "Claude Code", "relation": "CONNECTS_VIA", "target": "MCP Server"},
            {"source": "Claude Code", "relation": "INJECTS_MEMORY_WITH", "target": "recall_all.py"},
            {"source": "Claude Code", "relation": "RECALLED_CODEWORD", "target": "BLUE-HERON-42"},
            {"source": "BLUE-HERON-42", "relation": "STORED_BY", "target": "Google Antigravity"},
        ],
    },
    {
        "id": "ide-antigravity-1",
        "ide": "Google Antigravity",
        "decision_id": "DEC-02",
        "team_member": "Bob Martinez (Graph Platform Engineer)",
        "session_id": "f5aed901",
        "tools_used": ["10_eval_rag_vs_graphrag.py", "12_enterprise_aura_bundle.py", "Neo4j Aura CLI"],
        "data_used": ["build/eval_rag_vs_graphrag.json", "build/neo4j_haystack_enterprise_components.zip"],
        "content": (
            "Decision DEC-02 by Bob Martinez (Graph Platform Engineer) in Google Antigravity (session f5aed901): "
            "Adopted Hybrid GraphRAG (<=3 hops, hub-pruned) on Neo4j Aura (5.27-aura) deployed via Haystack "
            "Enterprise Platform. Verified using tools 10_eval_rag_vs_graphrag.py, 12_enterprise_aura_bundle.py, "
            "and Neo4j Aura CLI against benchmark dataset build/eval_rag_vs_graphrag.json (100% Hybrid GraphRAG "
            "multi-hop recall vs 62% Vector RAG) and artifact build/neo4j_haystack_enterprise_components.zip. "
            "Also stored codeword BLUE-HERON-42 at confidence 0.95."
        ),
        "entities": [
            {"name": "Bob Martinez", "type": "Person"},
            {"name": "DEC-02", "type": "Other"},
            {"name": "Google Antigravity", "type": "Organization"},
            {"name": "10_eval_rag_vs_graphrag.py", "type": "Technology"},
            {"name": "build/eval_rag_vs_graphrag.json", "type": "Other"},
            {"name": "MCP Server", "type": "Technology"},
            {"name": "BLUE-HERON-42", "type": "Other"},
            {"name": "Haystack Enterprise Platform", "type": "Technology"},
            {"name": "Neo4j Aura", "type": "Technology"},
        ],
        "relations": [
            {"source": "Bob Martinez", "relation": "USED_IDE", "target": "Google Antigravity"},
            {"source": "Bob Martinez", "relation": "MADE_DECISION", "target": "DEC-02"},
            {"source": "DEC-02", "relation": "VERIFIED_WITH_TOOL", "target": "10_eval_rag_vs_graphrag.py"},
            {"source": "DEC-02", "relation": "GROUNDED_IN_DATA", "target": "build/eval_rag_vs_graphrag.json"},
            {"source": "DEC-02", "relation": "DEPLOYED_ON", "target": "Haystack Enterprise Platform"},
            {"source": "Google Antigravity", "relation": "HOSTS", "target": "MCP Server"},
            {"source": "Google Antigravity", "relation": "STORED_CODEWORD", "target": "BLUE-HERON-42"},
            {"source": "Google Antigravity", "relation": "PACKAGED_PIPELINES_FOR", "target": "Haystack Enterprise Platform"},
            {"source": "Haystack Enterprise Platform", "relation": "CONNECTS_TO", "target": "Neo4j Aura"},
        ],
    },
    {
        "id": "ide-github-copilot-1",
        "ide": "GitHub Copilot",
        "decision_id": "DEC-03",
        "team_member": "Clara Weber (SecOps & Sovereignty Lead)",
        "session_id": "89f97e2e",
        "tools_used": ["mkg_agent_hook.py", "scrub_secrets.py", "cypher/05_scrub_verify.cypher"],
        "data_used": ["Local Neo4j 7689 SessionEvent Trace", ".env.example"],
        "content": (
            "Decision DEC-03 by Clara Weber (SecOps & Sovereignty Lead) in GitHub Copilot CLI (session 89f97e2e): "
            "Enforced the Sovereign Split-Graph redaction policy where raw IDE prompts and turns stay in Local "
            "Neo4j 7689 and are scrubbed on SessionEnd before any approved learnings reach Neo4j Aura. Verified "
            "using tools mkg_agent_hook.py, scrub_secrets.py, and cypher/05_scrub_verify.cypher on dataset "
            "Local Neo4j 7689 SessionEvent Trace, while receiving injected codeword BLUE-HERON-42."
        ),
        "entities": [
            {"name": "Clara Weber", "type": "Person"},
            {"name": "DEC-03", "type": "Other"},
            {"name": "GitHub Copilot", "type": "Organization"},
            {"name": "mkg_agent_hook.py", "type": "Technology"},
            {"name": "scrub_secrets.py", "type": "Technology"},
            {"name": "Local Neo4j 7689 SessionEvent Trace", "type": "Other"},
            {"name": "MCP Server", "type": "Technology"},
            {"name": "BLUE-HERON-42", "type": "Other"},
        ],
        "relations": [
            {"source": "Clara Weber", "relation": "USED_IDE", "target": "GitHub Copilot"},
            {"source": "Clara Weber", "relation": "MADE_DECISION", "target": "DEC-03"},
            {"source": "DEC-03", "relation": "VERIFIED_WITH_TOOL", "target": "scrub_secrets.py"},
            {"source": "DEC-03", "relation": "VERIFIED_WITH_TOOL", "target": "mkg_agent_hook.py"},
            {"source": "DEC-03", "relation": "GROUNDED_IN_DATA", "target": "Local Neo4j 7689 SessionEvent Trace"},
            {"source": "GitHub Copilot", "relation": "USES_ADAPTER", "target": "mkg_agent_hook.py"},
            {"source": "GitHub Copilot", "relation": "CONNECTS_VIA", "target": "MCP Server"},
            {"source": "GitHub Copilot", "relation": "RECEIVED_INJECTED_CODEWORD", "target": "BLUE-HERON-42"},
        ],
    },
    {
        "id": "ide-sovereign-architecture-1",
        "ide": "Architecture",
        "decision_id": "DEC-GOV",
        "team_member": "Cross-Team Architecture Board (Alice Chen, Bob Martinez, Clara Weber)",
        "session_id": "audit-2026-10-21",
        "tools_used": ["Haystack Enterprise Studio Evaluator", "Neo4jGraphRetriever"],
        "data_used": ["build/enterprise_multi_ide_eval.json"],
        "content": (
            "Auditable & Explainable Governance Architecture (DEC-GOV): Raw coding session events and tool outputs "
            "stay on Local Neo4j 7689 where scrub_secrets.py redacts sensitive tokens on SessionEnd. Approved "
            "architectural decisions (DEC-01 by Alice Chen, DEC-02 by Bob Martinez, DEC-03 by Clara Weber), along "
            "with the exact tools and datasets used, are indexed via Haystack Enterprise Platform into Neo4j Aura "
            "Enterprise (neo4j+s://ma3u-g993-fhgd.endpoints.neo4j.io, Neo4j 5.27-aura) for explainable multi-hop "
            "auditing (100% multi-hop accuracy vs 62% Vector RAG)."
        ),
        "entities": [
            {"name": "Local Neo4j 7689", "type": "Technology"},
            {"name": "scrub_secrets.py", "type": "Technology"},
            {"name": "Neo4j Aura", "type": "Technology"},
            {"name": "Haystack Enterprise Platform", "type": "Technology"},
            {"name": "Hybrid GraphRAG", "type": "Technology"},
            {"name": "MCP Server", "type": "Technology"},
            {"name": "DEC-01", "type": "Other"},
            {"name": "DEC-02", "type": "Other"},
            {"name": "DEC-03", "type": "Other"},
        ],
        "relations": [
            {"source": "MCP Server", "relation": "LOGS_RAW_EVENTS_TO", "target": "Local Neo4j 7689"},
            {"source": "scrub_secrets.py", "relation": "REDACTS_SECRETS_IN", "target": "Local Neo4j 7689"},
            {"source": "Local Neo4j 7689", "relation": "PUBLISHES_APPROVED_LEARNINGS_TO", "target": "Neo4j Aura"},
            {"source": "Haystack Enterprise Platform", "relation": "RUNS", "target": "Hybrid GraphRAG"},
            {"source": "Hybrid GraphRAG", "relation": "QUERIES", "target": "Neo4j Aura"},
            {"source": "Haystack Enterprise Platform", "relation": "AUDITS_DECISION", "target": "DEC-01"},
            {"source": "Haystack Enterprise Platform", "relation": "AUDITS_DECISION", "target": "DEC-02"},
            {"source": "Haystack Enterprise Platform", "relation": "AUDITS_DECISION", "target": "DEC-03"},
        ],
    },
]

HACKATHON_QUESTIONS = [
    {
        "id": "hq1",
        "type": "multi_hop",
        "question": "Which codeword was stored by Google Antigravity, and which other two AI coding IDEs recalled or received it via the MCP memory graph?",
        "expected_keywords": ["BLUE-HERON-42", "Claude Code", "GitHub Copilot"],
    },
    {
        "id": "hq2",
        "type": "multi_hop",
        "question": "How does GitHub Copilot connect to the Neo4j memory system, and how are raw session secrets kept out of Neo4j Aura?",
        "expected_keywords": ["mkg_agent_hook.py", "7689", "scrub_secrets.py"],
    },
    {
        "id": "hq3",
        "type": "multi_hop",
        "question": "Why does Haystack Enterprise Platform use Hybrid GraphRAG on Neo4j Aura instead of plain Vector RAG, and what was the benchmark accuracy difference?",
        "expected_keywords": ["100%", "62%"],
    },
    {
        "id": "hq4",
        "type": "decision_audit",
        "question": "Audit decisions DEC-01, DEC-02, and DEC-03 across the team: which team member made each decision, which IDE and tools did they use, and which data artifacts justified their decision?",
        "expected_keywords": [
            "Alice Chen",
            "Bob Martinez",
            "Clara Weber",
            "bench.py",
            "10_eval_rag_vs_graphrag.py",
            "scrub_secrets.py",
            "claude_memory/eval/results.json",
            "build/eval_rag_vs_graphrag.json",
        ],
    },
]


def get_aura_store() -> Neo4jDocumentStore:
    uri = os.getenv("NEO4J_AURA_URI") or os.getenv("NEO4J_AURA_ENDPOINT")
    if not uri or not os.getenv("NEO4J_AURA_PASSWORD"):
        raise RuntimeError("NEO4J_AURA_URI and NEO4J_AURA_PASSWORD must be configured in .env")
    user = os.getenv("NEO4J_AURA_USER", "neo4j")
    return Neo4jDocumentStore(
        uri=uri,
        username=user,
        password=Secret.from_env_var("NEO4J_AURA_PASSWORD"),
        label=CHUNK_LABEL,
        embedding_dimension=DIM,
    )


def sync_cross_ide_graph_to_aura(store: Neo4jDocumentStore) -> dict[str, int]:
    """Embed and write the curated 3-IDE passages + entity-relation graph to Neo4j Aura."""
    docs = [
        Document(
            id=item["id"],
            content=item["content"],
            meta={
                "ide": item["ide"],
                "decision_id": item["decision_id"],
                "team_member": item["team_member"],
                "session_id": item["session_id"],
                "tools_used": ", ".join(item["tools_used"]),
                "data_used": ", ".join(item["data_used"]),
                "source": "cross_ide_memory",
            },
        )
        for item in CROSS_IDE_PASSAGES
    ]
    embedder = SentenceTransformersDocumentEmbedder(model=EMBED_MODEL)
    embedder.warm_up()
    embedded_docs = embedder.run(documents=docs)["documents"]

    writer = DocumentWriter(document_store=store, policy=DuplicatePolicy.OVERWRITE)
    written_chunks = writer.run(documents=embedded_docs)["documents_written"]

    extractions = [
        {"doc_id": item["id"], "entities": item["entities"], "relations": item["relations"]}
        for item in CROSS_IDE_PASSAGES
    ]
    graph_writer = Neo4jGraphWriter(document_store=store)
    graph_stats = graph_writer.run(extractions=extractions)
    return {
        "chunks_written": written_chunks,
        "entities_written": graph_stats["entities_written"],
        "relations_written": graph_stats["relations_written"],
    }


def print_auditable_decision_trail() -> None:
    """Print the explainable multi-member decision provenance summary."""
    print("\n=== 3. Auditable & Explainable Team Decision Trail (Provenance Summary) ===")
    for item in CROSS_IDE_PASSAGES:
        print(f"  [{item['decision_id']}] {item['team_member']} — IDE: {item['ide']} (session: {item['session_id']})")
        print(f"    Tools Used : {', '.join(item['tools_used'])}")
        print(f"    Data Used  : {', '.join(item['data_used'])}")


def deploy_to_haystack_enterprise(api_key: str, workspace: str) -> None:
    """Upload Custom Component ZIP and deploy YAML pipelines to Haystack Enterprise Platform."""
    zip_path = BUILD_DIR / "neo4j_haystack_enterprise_components.zip"
    if not zip_path.exists():
        raise FileNotFoundError(f"Run exercises/12_enterprise_aura_bundle.py first to build {zip_path}")

    base_url = os.getenv("DEEPSET_CLOUD_API_URL", "https://api.cloud.deepset.ai").rstrip("/")
    req = urllib.request.Request(
        f"{base_url}/api/v1/workspaces/{workspace}/pipelines",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        print(f"Connected to Haystack Enterprise workspace '{workspace}' ({len(data.get('data', []))} existing pipelines).")


def main() -> None:
    load_dotenv(HERE.parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-llm", action="store_true", help="Also run the LLM generator to produce natural-language answers.")
    parser.add_argument("--deploy", action="store_true", help="Deploy bundle to Haystack Enterprise Platform using DEEPSET_CLOUD_API_KEY.")
    parser.add_argument("--workspace", default=os.getenv("DEEPSET_WORKSPACE", "default"), help="Haystack Enterprise workspace name.")
    args = parser.parse_args()

    store = get_aura_store()
    try:
        print(f"=== 1. Syncing 3-IDE Collective Memory (Claude Code, Google Antigravity, GitHub Copilot) to Neo4j Aura ({store.uri}) ===")
        stats = sync_cross_ide_graph_to_aura(store)
        print(
            f"   Synced to Aura: {stats['chunks_written']} embedded passages, "
            f"{stats['entities_written']} entities, {stats['relations_written']} relations."
        )

        print("\n=== 2. Running Haystack Hybrid GraphRAG Pipeline Live on Neo4j Aura ===")
        pipe = build_query(store, variant="graphrag", with_llm=args.with_llm)
        pipe.warm_up()

        eval_records = []
        for q in HACKATHON_QUESTIONS:
            include = {"graph", "ranker"}
            res = pipe.run(query_inputs(pipe, q["question"]), include_outputs_from=include)
            facts = res["graph"]["facts"]
            passages = [d.content for d in res["graph"]["documents"]]
            combined_context = "\n".join(facts + passages)
            matched = [kw for kw in q["expected_keywords"] if kw.lower() in combined_context.lower()]
            passed = len(matched) == len(q["expected_keywords"])

            print(f"\n[{q['id'].upper()}] Q: {q['question']}")
            print(f"  Linked Entities ({len(res['graph']['entities'])}): {res['graph']['entities']}")
            print(f"  Multi-Hop Graph Facts Retrieved from Aura ({len(facts)}):")
            for f in facts[:6]:
                print(f"    - {f}")
            print(f"  Context Recall Check: {'PASS' if passed else 'FAIL'} ({len(matched)}/{len(q['expected_keywords'])}: {matched})")

            answer_text = None
            if args.with_llm and "llm" in res:
                answer_text = res["llm"]["replies"][0].text.strip()
                print(f"  LLM Answer: {answer_text}")

            eval_records.append(
                {
                    "id": q["id"],
                    "question": q["question"],
                    "expected_keywords": q["expected_keywords"],
                    "retrieved_facts": facts,
                    "retrieved_passages": passages,
                    "context_recall_pass": passed,
                    "answer": answer_text,
                }
            )

        print_auditable_decision_trail()

        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        eval_out = BUILD_DIR / "enterprise_multi_ide_eval.json"
        eval_out.write_text(json.dumps(eval_records, indent=2))
        print(f"\n=== 4. Exported Haystack Enterprise Evaluation Dataset: {eval_out.relative_to(HERE.parent)} ===")

        if args.deploy:
            api_key = os.getenv("DEEPSET_CLOUD_API_KEY")
            if not api_key:
                raise RuntimeError("Set DEEPSET_CLOUD_API_KEY in .env to run --deploy")
            deploy_to_haystack_enterprise(api_key, args.workspace)
    finally:
        store.close()


if __name__ == "__main__":
    main()
