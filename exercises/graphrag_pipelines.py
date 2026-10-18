"""
Pipeline builders shared by 09_graphrag.py (demo) and 10_eval_rag_vs_graphrag.py (evaluation).

Retrieval variants, all ending in the SAME prompt template so only the context differs:
  vector     embed -> Neo4j vector index (top 10) -> cross-encoder rerank (top 4, logit >= -8)   [exercise 08]
  vector_k8  embed -> Neo4j vector index (top 8), no reranker                                    [more-context control]
  graphrag   `vector` + Neo4jGraphRetriever facts (entity linking + 3-hop expansion, hubs pruned)
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
from neo4j_graph import ENTITY_LABEL, LLMGraphExtractor, Neo4jGraphRetriever, Neo4jGraphWriter

DATA = Path(__file__).parent.parent / "data"
CORPUS = DATA / "graphrag_corpus.txt"
EVAL_SET = DATA / "graphrag_eval.json"

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
DIM = 384
RANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RANK_THRESHOLD = -8.0
CHUNK_LABEL = "GraphRagChunk"
VARIANTS = ["vector", "vector_k8", "graphrag"]

TEMPLATE = [
    ChatMessage.from_user("""
Answer ONLY from the context. If the answer is not there, say "I don't know". Answer in one short sentence.
{% if facts %}
Knowledge graph facts:
{% for f in facts %}- {{ f }}
{% endfor %}{% endif %}
Passages:
{% for doc in documents %}- {{ doc.content }}
{% endfor %}
Question: {{ query }}
""")
]


def make_store() -> Neo4jDocumentStore:
    return Neo4jDocumentStore(label=CHUNK_LABEL, embedding_dimension=DIM)


def reset_graph(store: Neo4jDocumentStore) -> None:
    store.run_cypher(f"MATCH (n) WHERE n:{CHUNK_LABEL} OR n:{ENTITY_LABEL} DETACH DELETE n")


def build_indexing(store: Neo4jDocumentStore) -> Pipeline:
    """file -> passages -> embeddings -> chunk nodes, and in parallel -> LLM extraction -> entity graph."""
    p = Pipeline()
    p.add_component("converter", TextFileToDocument())
    p.add_component("splitter", DocumentSplitter(split_by="passage", split_length=1))
    p.add_component("embedder", SentenceTransformersDocumentEmbedder(model=EMBED_MODEL))
    p.add_component("writer", DocumentWriter(document_store=store, policy=DuplicatePolicy.OVERWRITE))
    p.add_component("extractor", LLMGraphExtractor())
    p.add_component("graph_writer", Neo4jGraphWriter(document_store=store))
    p.connect("converter.documents", "splitter.documents")
    p.connect("splitter.documents", "embedder.documents")
    p.connect("embedder.documents", "writer.documents")
    p.connect("splitter.documents", "extractor.documents")
    p.connect("extractor.extractions", "graph_writer.extractions")
    return p


def build_query(store: Neo4jDocumentStore, variant: str, with_llm: bool = True) -> Pipeline:
    """Retrieval for `variant` -> prompt (-> LLM). Inputs: text_embedder.text, prompt.query (+ ranker/graph query)."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    p = Pipeline()
    p.add_component("text_embedder", SentenceTransformersTextEmbedder(model=EMBED_MODEL))
    p.add_component("prompt", ChatPromptBuilder(template=TEMPLATE, required_variables=["query"]))

    if variant == "vector_k8":
        p.add_component("retriever", Neo4jEmbeddingRetriever(document_store=store, top_k=8))
        p.connect("text_embedder.embedding", "retriever.query_embedding")
        p.connect("retriever.documents", "prompt.documents")
    else:
        p.add_component("retriever", Neo4jEmbeddingRetriever(document_store=store, top_k=10))
        p.add_component(
            "ranker",
            SentenceTransformersSimilarityRanker(
                model=RANK_MODEL, top_k=4, scale_score=False, score_threshold=RANK_THRESHOLD
            ),
        )
        p.connect("text_embedder.embedding", "retriever.query_embedding")
        p.connect("retriever.documents", "ranker.documents")
        if variant == "vector":
            p.connect("ranker.documents", "prompt.documents")
        else:
            p.add_component("graph", Neo4jGraphRetriever(document_store=store, max_hops=3, max_facts=50))
            p.connect("ranker.documents", "graph.documents")
            p.connect("graph.documents", "prompt.documents")
            p.connect("graph.facts", "prompt.facts")

    if with_llm:
        p.add_component("llm", chat_generator())
        p.connect("prompt.prompt", "llm.messages")
    return p


def query_inputs(pipeline: Pipeline, question: str) -> dict:
    """Route the question to every component that needs it in this variant."""
    inputs = {"text_embedder": {"text": question}, "prompt": {"query": question}}
    for name in ("ranker", "graph"):
        if name in pipeline.graph.nodes:
            inputs[name] = {"query": question}
    return inputs
