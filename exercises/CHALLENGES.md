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
Implement custom DocumentStore protocol targeting SQLite / Redis / etc.
- Required methods: `write_documents`, `delete_documents`, `count_documents`, `filter_documents`

## Tips
- Read `pipeline.show()` output to debug wiring
- Use `Pipeline(max_runs_per_component=100)` to break infinite loops
- `warm_up()` before first run to pre-load models
- `include_outputs_from={"component_name"}` to expose intermediate values
