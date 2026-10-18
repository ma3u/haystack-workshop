# Exercises

Progression from basic to advanced Haystack 2.x patterns.

| # | File | Topic | New in 2.x |
|---|------|-------|------------|
| 1 | 01_basic_pipeline.py | PromptBuilder + Generator + BM25 | Pipeline API |
| 2 | 02_rag_with_embeddings.py | Dense retrieval, embedders | Component split |
| 3 | 03_custom_component.py | @component decorator | New typing system |
| 4 | 04_agent_tool_use.py | Agent + Tool abstraction | **NEW** - replaces old agents |
| 5 | 05_hybrid_retrieval.py | BM25+Dense + RRF joiner | DocumentJoiner |
| 6 | 06_streaming_chat.py | Streaming callback + ChatGenerator | ChatMessage dataclass |
| 7 | 07_neo4j_document_store.py | Custom DocumentStore on Neo4j (Challenge F) | DocumentStore protocol, filters -> Cypher |
| 8 | 08_neo4j_vector_rag.py | Dense RAG with Neo4jEmbeddingRetriever | Native vector index, custom retriever component |
| 9 | 09_graphrag.py | GraphRAG: LLM entity extraction + graph expansion on Neo4j | Custom components: extractor, graph writer, graph retriever |
| 10 | 10_eval_rag_vs_graphrag.py | Accuracy eval: vector RAG vs GraphRAG (LLM judge) | Eval set, context recall, A/B variants |
| 11 | 11_neo4j_memory_agent.py | Agent with Neo4j memory: chat history per session + long-term knowledge graph | Agent tools that write/read the graph (remember / recall / forget) |

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
