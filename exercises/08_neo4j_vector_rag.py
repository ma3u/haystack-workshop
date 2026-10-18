"""
Dense RAG on Neo4j: Neo4jEmbeddingRetriever + native vector index.

Indexing:  TextFileToDocument -> DocumentSplitter -> SentenceTransformersDocumentEmbedder -> DocumentWriter
Query:     SentenceTransformersTextEmbedder -> Neo4jEmbeddingRetriever -> SimilarityRanker -> ChatPromptBuilder -> LLM

Two-stage retrieval: the bi-encoder + vector index recall candidates fast (top_k=10), then a cross-encoder
reads query and passage together and re-scores them. Its raw logits separate relevant from irrelevant
far better than cosine (measured on sample.txt: relevant -4..+3, irrelevant about -11), so RANK_THRESHOLD
can drop junk before it reaches the LLM. Sigmoid-scaled scores squash paraphrased-but-relevant hits to
~0.03, which makes a [0, 1] threshold fragile, so we keep the logits (scale_score=False).

Same embedding model on both sides (384 dims), which is also the vector index dimension.
Inspect afterwards in Neo4j Browser:  SHOW VECTOR INDEXES;  MATCH (n:HaystackRagDocument) RETURN n
"""
from pathlib import Path

from haystack import Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.components.converters import TextFileToDocument
from haystack.components.preprocessors import DocumentSplitter
from haystack.components.writers import DocumentWriter
from haystack.dataclasses import ChatMessage
from haystack.document_stores.types import DuplicatePolicy
from haystack_integrations.components.embedders.sentence_transformers import (
    SentenceTransformersDocumentEmbedder,
    SentenceTransformersTextEmbedder,
)
from haystack_integrations.components.rankers.sentence_transformers import SentenceTransformersSimilarityRanker

from llm import chat_generator
from neo4j_document_store import Neo4jDocumentStore
from neo4j_embedding_retriever import Neo4jEmbeddingRetriever

MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
RANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RANK_THRESHOLD = -8.0  # raw cross-encoder logit; below this a passage is treated as irrelevant
SAMPLE = Path(__file__).parent.parent / "data" / "sample.txt"

store = Neo4jDocumentStore(label="HaystackRagDocument", embedding_dimension=DIM, similarity="cosine")

# Indexing pipeline. Split by blank lines; OVERWRITE keeps re-runs idempotent (chunk ids are content hashes).
idx = Pipeline()
idx.add_component("converter", TextFileToDocument())
idx.add_component("splitter", DocumentSplitter(split_by="passage", split_length=1))
idx.add_component("embedder", SentenceTransformersDocumentEmbedder(model=MODEL))
idx.add_component("writer", DocumentWriter(document_store=store, policy=DuplicatePolicy.OVERWRITE))
idx.connect("converter.documents", "splitter.documents")
idx.connect("splitter.documents", "embedder.documents")
idx.connect("embedder.documents", "writer.documents")

template = [ChatMessage.from_user("""
Answer ONLY from the context. If the answer is not there, say "I don't know".

Context:
{% for doc in documents %}- {{ doc.content }}
{% endfor %}

Question: {{ query }}
""")]

qp = Pipeline()
qp.add_component("text_embedder", SentenceTransformersTextEmbedder(model=MODEL))
qp.add_component("retriever", Neo4jEmbeddingRetriever(document_store=store, top_k=10))
qp.add_component(
    "ranker",
    SentenceTransformersSimilarityRanker(model=RANK_MODEL, top_k=2, scale_score=False, score_threshold=RANK_THRESHOLD),
)
qp.add_component("prompt", ChatPromptBuilder(template=template, required_variables=["query"]))
qp.add_component("llm", chat_generator())
qp.connect("text_embedder.embedding", "retriever.query_embedding")
qp.connect("retriever.documents", "ranker.documents")
qp.connect("ranker.documents", "prompt.documents")
qp.connect("prompt.prompt", "llm.messages")

if __name__ == "__main__":
    written = idx.run({"converter": {"sources": [SAMPLE]}})["writer"]["documents_written"]
    print(f"Indexed {written} chunks -> {store.count_documents()} nodes, vector index '{store.vector_index}'\n")

    # Phrased without the doc's keywords ("multi-step", "tool-calling") to show semantic matching.
    queries = [
        "How do I let the model call functions to solve a task in several steps?",
        "Which document stores does Haystack support?",
        "Who won the 2022 World Cup?",
    ]
    for query in queries:
        result = qp.run(
            {"text_embedder": {"text": query}, "ranker": {"query": query}, "prompt": {"query": query}},
            include_outputs_from={"retriever", "ranker"},
        )
        print(f"Q: {query}")
        print("  vector index (cosine, top 3 of 10):")
        for d in result["retriever"]["documents"][:3]:
            print(f"    [{d.score:.3f}] {" ".join(d.content.split())[:80]}")
        print(f"  reranked (cross-encoder logit, >= {RANK_THRESHOLD}):")
        for d in result["ranker"]["documents"]:
            print(f"    [{d.score:+.1f}] {" ".join(d.content.split())[:80]}")
        if not result["ranker"]["documents"]:
            print("    (nothing relevant -> LLM gets empty context)")
        print(f"A: {result['llm']['replies'][0].text}\n")

    store.close()
