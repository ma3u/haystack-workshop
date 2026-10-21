# Exercises & Challenges (01 → 13)

Ordered progression from core Haystack 2.x/3.x pipelines (**01–06**), to Neo4j `DocumentStore` and hybrid GraphRAG (**07–10**), to Neo4j agent memory and cross-agent MCP integration (**11–13**).

| # | File / Directory | Challenge Topic | Final Outcome / Key Capability |
|---|---|---|---|
| **01** | [`01_basic_pipeline.py`](01_basic_pipeline.py) | Basic RAG Pipeline | `InMemoryBM25Retriever` + `ChatPromptBuilder` + `ChatGenerator` |
| **02** | [`02_rag_with_embeddings.py`](02_rag_with_embeddings.py) | Dense Embedding RAG | Separate index & query pipelines with SentenceTransformers |
| **03** | [`03_custom_component.py`](03_custom_component.py) | Custom `@component` | Typed inputs/outputs integrated into `Pipeline` |
| **04** | [`04_agent_tool_use.py`](04_agent_tool_use.py) | Multi-Step `Agent` + `Tool` *(Challenge B)* | Function-calling agent loop replacing legacy 1.x agents |
| **05** | [`05_hybrid_retrieval.py`](05_hybrid_retrieval.py) | Hybrid Retrieval + RRF *(Challenge A)* | BM25 + Dense joined via `DocumentJoiner(reciprocal_rank_fusion)` |
| **06** | [`06_streaming_chat.py`](06_streaming_chat.py) | Streaming Chat *(Challenge C)* | `ChatMessage` history + token streaming callback |
| **07** | [`07_neo4j_document_store.py`](07_neo4j_document_store.py) | Custom `Neo4jDocumentStore` *(Challenge F)* | Filter → Cypher compiler; passes `DocumentStoreBaseTests` ([`test_neo4j_document_store.py`](test_neo4j_document_store.py)) |
| **08** | [`08_neo4j_vector_rag.py`](08_neo4j_vector_rag.py) | Neo4j Vector RAG + Reranker *(Challenge A/F)* | Native HNSW vector index + `Neo4jEmbeddingRetriever` + cross-encoder reranker (100% single-hop) |
| **09** | [`09_graphrag.py`](09_graphrag.py) | Hybrid GraphRAG on Neo4j *(Challenge B)* | `LLMGraphExtractor` + `Neo4jGraphWriter` + `Neo4jGraphRetriever` ($\le 3$ hops) ([`GRAPHRAG.md`](GRAPHRAG.md)) |
| **10** | [`10_eval_rag_vs_graphrag.py`](10_eval_rag_vs_graphrag.py) | Vector RAG vs. GraphRAG Eval *(Challenge D)* | 22-question $\times$ 3-run LLM-judge benchmark: **62% Vector RAG → 100% Hybrid GraphRAG** |
| **11** | [`11_neo4j_memory_agent.py`](11_neo4j_memory_agent.py) | Haystack Agent with Neo4j Fact Memory *(Challenge C)* | `remember` / `recall` / `forget` tools with temporal `valid_to` retirement (11/11 tests in [`test_neo4j_memory.py`](test_neo4j_memory.py)) |
| **12** | [`12_enterprise_aura_bundle.py`](12_enterprise_aura_bundle.py) | **Haystack Enterprise Platform + Neo4j Aura Bundle** *(Challenge E/G)* | Full YAML round-trip (`dumps`/`loads`) for GraphRAG Index & Query pipelines, secret-leak audit, and Custom Component ZIP (`build/neo4j_haystack_enterprise_components.zip`) for deepset Studio + Neo4j Aura (`neo4j+s://...`) |
| **13** | [`13_enterprise_aura_multi_ide.py`](13_enterprise_aura_multi_ide.py) | **Sovereign 3-IDE Collective Memory & Auditable Decision Provenance on Haystack Enterprise + Neo4j Aura** *(Challenge H)* | Syncs approved learnings, team decisions (`DEC-01`–`DEC-03`), tools, and data artifacts from **Claude Code**, **Google Antigravity**, and **GitHub Copilot** to Neo4j Aura (`neo4j+s://ma3u-g993-fhgd.endpoints.neo4j.io`) and runs live multi-hop Hybrid GraphRAG & decision audit queries ([`ENTERPRISE_HACKATHON.md`](ENTERPRISE_HACKATHON.md)) |
| **14** | [`../claude_memory/bench.py`](../claude_memory/README.md) | Agent Memory Interface Benchmark | MCP vs. CLI vs. HTTP API: **MCP passes 3/3 under narrow permissions at ~50% fewer tokens** |
| **15** | [`../claude_memory/`](../claude_memory/README.md) | Cross-Agent Long-Term Graph Memory (`meta-knowledge-graph`) | **10/10** recall (`memory_eval.py`), **51/51** prompt audit, and `BLUE-HERON-42` codeword verified across **Google Antigravity**, **Claude Code**, and **GitHub Copilot CLI** |

## Run

```bash
source ../venv/bin/activate
export OPENAI_API_KEY=...
python 01_basic_pipeline.py
```

## Hackathon Prep Checklist

- [ ] Pipeline/component mental model solid
- [ ] Custom @component with typed i/o
- [ ] Agent + Tool for function calling
- [ ] Hybrid retrieval + RRF
- [ ] Streaming + chat history
- [ ] Vector store choice: Qdrant/Weaviate/Elasticsearch
- [ ] Serialization: `pipeline.dumps()` / `Pipeline.loads()`
- [ ] Error handling: `.warm_up()`, retries

## GraphRAG

See [GRAPHRAG.md](GRAPHRAG.md): vector RAG vs GraphRAG results, when to use which, and why Haystack fits GraphRAG.

## Aura dashboard

"Haystack GraphRAG on Neo4j" in Aura Console -> Dashboards: RAG vs GraphRAG accuracy, the entity graph,
the agent's memory and the document stores. Queries: [dashboard.cypher](dashboard.cypher).

```bash
# point the exercises at Aura (env vars override .env), then fill it
export NEO4J_URI=$NEO4J_AURA_URI NEO4J_USERNAME=$NEO4J_AURA_USER NEO4J_PASSWORD=$NEO4J_AURA_PASSWORD
python 07_neo4j_document_store.py && python 08_neo4j_vector_rag.py && python 09_graphrag.py --reset && python 11_neo4j_memory_agent.py
python eval_to_neo4j.py      # results/graphrag_eval_results.json -> (:EvalVariant)-[:ANSWERED]->(:EvalQuestion)
python build_dashboard.py    # dashboard.cypher -> results/aura_dashboard.json, paste into Dashboards -> Import
```
