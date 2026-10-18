"""
Exercise 2: RAG with Dense Embeddings
Goal: Replace BM25 with embedding-based retrieval.

Tasks:
1. Index docs with SentenceTransformersDocumentEmbedder
2. Query with SentenceTransformersTextEmbedder + InMemoryEmbeddingRetriever
3. Compare recall vs BM25
"""
from haystack import Pipeline, Document
from haystack.document_stores.in_memory import InMemoryDocumentStore
from haystack_integrations.components.embedders.sentence_transformers import (
    SentenceTransformersDocumentEmbedder,
    SentenceTransformersTextEmbedder,
)
from haystack.components.retrievers.in_memory import InMemoryEmbeddingRetriever
from haystack.components.writers import DocumentWriter

MODEL = "sentence-transformers/all-MiniLM-L6-v2"

docs = [
    Document(content="Dense retrieval uses vector embeddings for semantic similarity."),
    Document(content="BM25 is a lexical ranking function based on term frequency."),
    Document(content="Hybrid retrieval combines dense + sparse scores for better recall."),
    Document(content="RAG = Retrieval Augmented Generation. LLM grounded on retrieved context."),
]

store = InMemoryDocumentStore()

# Indexing pipeline
idx = Pipeline()
idx.add_component("embedder", SentenceTransformersDocumentEmbedder(model=MODEL))
idx.add_component("writer", DocumentWriter(document_store=store))
idx.connect("embedder.documents", "writer.documents")
idx.run({"embedder": {"documents": docs}})

# Query pipeline
qp = Pipeline()
qp.add_component("text_embedder", SentenceTransformersTextEmbedder(model=MODEL))
qp.add_component("retriever", InMemoryEmbeddingRetriever(document_store=store, top_k=2))
qp.connect("text_embedder.embedding", "retriever.query_embedding")

if __name__ == "__main__":
    result = qp.run({"text_embedder": {"text": "semantic search methods"}})
    for d in result["retriever"]["documents"]:
        print(f"[{d.score:.3f}] {d.content}")
