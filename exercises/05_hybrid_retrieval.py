"""
Exercise 5: Hybrid Retrieval (BM25 + Dense) with Reciprocal Rank Fusion
Goal: Combine sparse + dense retrieval, merge via joiner.

Tasks:
1. Build parallel BM25 + Embedding retrievers
2. Merge with DocumentJoiner (reciprocal_rank_fusion)
3. Compare top-k vs single retriever
"""
from haystack import Pipeline, Document
from haystack.document_stores.in_memory import InMemoryDocumentStore
from haystack_integrations.components.embedders.sentence_transformers import (
    SentenceTransformersDocumentEmbedder,
    SentenceTransformersTextEmbedder,
)
from haystack.components.retrievers.in_memory import (
    InMemoryBM25Retriever,
    InMemoryEmbeddingRetriever,
)
from haystack.components.joiners import DocumentJoiner
from haystack.components.writers import DocumentWriter

MODEL = "sentence-transformers/all-MiniLM-L6-v2"

docs = [
    Document(content="Python is a high-level programming language."),
    Document(content="JavaScript runs in browsers and Node.js."),
    Document(content="Rust emphasizes memory safety without GC."),
    Document(content="Go has built-in concurrency via goroutines."),
    Document(content="TypeScript adds static types to JavaScript."),
]

store = InMemoryDocumentStore()
idx = Pipeline()
idx.add_component("embed", SentenceTransformersDocumentEmbedder(model=MODEL))
idx.add_component("write", DocumentWriter(document_store=store))
idx.connect("embed.documents", "write.documents")
idx.run({"embed": {"documents": docs}})

qp = Pipeline()
qp.add_component("text_embed", SentenceTransformersTextEmbedder(model=MODEL))
qp.add_component("bm25", InMemoryBM25Retriever(document_store=store, top_k=3))
qp.add_component("dense", InMemoryEmbeddingRetriever(document_store=store, top_k=3))
qp.add_component("joiner", DocumentJoiner(join_mode="reciprocal_rank_fusion", top_k=3))

qp.connect("text_embed.embedding", "dense.query_embedding")
qp.connect("bm25.documents", "joiner.documents")
qp.connect("dense.documents", "joiner.documents")

if __name__ == "__main__":
    query = "memory-safe systems language"
    result = qp.run({
        "text_embed": {"text": query},
        "bm25": {"query": query},
    })
    for d in result["joiner"]["documents"]:
        print(f"[{d.score:.3f}] {d.content}")
