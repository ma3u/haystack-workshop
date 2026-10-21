# Hackathon Challenges

Realistic scenarios likely at hackathon. Build pipelines solving these end-to-end.

## Challenge A: Document Q&A over PDFs
Ingest PDFs → chunk → embed → index → RAG query.
- Components: `PyPDFToDocument`, `DocumentSplitter`, `SentenceTransformersDocumentEmbedder`, `DocumentWriter`, retriever+generator
- Bonus: metadata filters, hybrid retrieval, reranker

## Challenge B: Multi-Hop Agent
Agent answers complex questions needing multiple tool calls.
- Tools: web search, calculator, document lookup
- Track intermediate steps, return citations

## Challenge C: Streaming Chatbot with Memory
Multi-turn chat with pruned history + RAG grounding.
- Components: `OpenAIChatGenerator` streaming, `ChatMessage` history, context window pruning

## Challenge D: Evaluation Pipeline
Measure RAG quality (faithfulness, answer relevance, context precision).
- Use `haystack.evaluation` metrics
- A/B test retrievers (BM25 vs dense vs hybrid)

## Challenge E: Production Deployment
Serialize pipeline to YAML. FastAPI endpoint. Dockerize.
- `pipeline.dumps()` / `Pipeline.loads()`
- Hayhooks for REST serving

## Challenge F: Custom DocumentStore
Implement custom DocumentStore protocol targeting Neo4j (`Neo4jDocumentStore` in `07_neo4j_document_store.py`).
- Required methods: `write_documents`, `delete_documents`, `count_documents`, `filter_documents`, `to_dict`, `from_dict`
- Bonus: Native HNSW vector index (`08_neo4j_vector_rag.py`), Hybrid GraphRAG (`09_graphrag.py`), Automated A/B Evaluation (`10_eval_rag_vs_graphrag.py`), Temporal Agent Memory (`11_neo4j_memory_agent.py`)

## Challenge G: Haystack Enterprise Platform + Neo4j Aura Deployment (`12_enterprise_aura_bundle.py`)
Package the custom Neo4j DocumentStore and Hybrid GraphRAG pipelines for **Haystack Enterprise Platform (deepset AI Platform / Studio)** backed by **Neo4j Aura (`neo4j+s://...`)**:
- Implement lossless `to_dict()` / `from_dict()` YAML serialization on `LLMGraphExtractor`, `Neo4jGraphWriter`, and `Neo4jGraphRetriever`
- Export Studio-ready Indexing and Query YAML pipelines (`pipeline.dumps()`) and verify round-trip loading (`Pipeline.loads()`)
- Audit serialized YAML to guarantee zero secret leakage (`NEO4J_AURA_PASSWORD` and API keys referenced strictly via `Secret.from_env_var(...)`)
- Package custom components into an installable ZIP bundle (`build/neo4j_haystack_enterprise_components.zip`) for `/api/v2/custom_components`

## Challenge H: Sovereign 3-IDE Collective Memory & Auditable Decision Provenance on Haystack Enterprise + Neo4j Aura (`13_enterprise_aura_multi_ide.py`)
Hackathon flagship challenge for **Hack Night: Haystack Enterprise Platform (Berlin, Oct 21, 2026)** ([`ENTERPRISE_HACKATHON.md`](ENTERPRISE_HACKATHON.md)):
- Keep raw IDE session events (`SessionEvent`, `Turn`, `ToolCall`) on local Neo4j (`:7689`) with `scrub_secrets.py` redaction
- Sync approved, secret-scrubbed learnings, architectural decisions (`DEC-01`–`DEC-03`), team members (`Alice Chen`, `Bob Martinez`, `Clara Weber`), tools used, and data artifacts from **Claude Code**, **Google Antigravity**, and **GitHub Copilot** into **Neo4j Aura Enterprise (`neo4j+s://ma3u-g993-fhgd.endpoints.neo4j.io`)**
- Run the Haystack Hybrid GraphRAG pipeline (`Neo4jEmbeddingRetriever` + `TransformersSimilarityRanker` + 3-hop `Neo4jGraphRetriever`) live against Neo4j Aura to deliver **auditable, explainable decision provenance** (who decided what, in which IDE, using which tools and datasets) across all 3 AI coding IDEs
- Export `build/enterprise_multi_ide_eval.json` and deploy to Haystack Enterprise Studio (`--deploy`)

## Tips
- Read `pipeline.show()` output to debug wiring
- Use `Pipeline(max_runs_per_component=100)` to break infinite loops
- `warm_up()` before first run to pre-load models
- `include_outputs_from={"component_name"}` to expose intermediate values
