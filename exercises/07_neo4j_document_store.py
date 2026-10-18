"""
Challenge F demo: plug the custom Neo4jDocumentStore into normal Haystack pipelines.

Any component typed against the DocumentStore protocol works unchanged:
  - DocumentWriter   -> write_documents()
  - FilterRetriever  -> filter_documents()  (filters compiled to Cypher, run inside Neo4j)

Prereq: Neo4j Desktop instance running, NEO4J_* set in .env.
Look at the data afterwards in Neo4j Browser:  MATCH (n:HaystackDocument) RETURN n
"""
from haystack import Document, Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.components.retrievers import FilterRetriever
from haystack.components.writers import DocumentWriter
from haystack.dataclasses import ChatMessage
from haystack.document_stores.types import DuplicatePolicy

from llm import chat_generator
from neo4j_document_store import Neo4jDocumentStore

docs = [
    Document(content="Haystack 2.x pipelines are DAGs of typed components.", meta={"topic": "haystack", "year": 2024}),
    Document(content="Haystack 1.x used nodes and a different pipeline API.", meta={"topic": "haystack", "year": 2021}),
    Document(content="Neo4j is a graph database queried with Cypher.", meta={"topic": "neo4j", "year": 2024}),
    Document(content="Neo4j 5 added native vector indexes for embeddings.", meta={"topic": "neo4j", "year": 2023}),
]

store = Neo4jDocumentStore()

# Indexing pipeline. OVERWRITE makes the script safe to re-run.
idx = Pipeline()
idx.add_component("writer", DocumentWriter(document_store=store, policy=DuplicatePolicy.OVERWRITE))
idx.run({"writer": {"documents": docs}})

template = [ChatMessage.from_user("""
Answer ONLY from the context. If the answer is not there, say "I don't know".

Context:
{% for doc in documents %}- ({{ doc.meta.year }}) {{ doc.content }}
{% endfor %}

Question: {{ query }}
""")]

qp = Pipeline()
qp.add_component("retriever", FilterRetriever(document_store=store))
qp.add_component("prompt", ChatPromptBuilder(template=template, required_variables=["query"]))
qp.add_component("llm", chat_generator())
qp.connect("retriever.documents", "prompt.documents")
qp.connect("prompt.prompt", "llm.messages")

if __name__ == "__main__":
    print(f"Documents in Neo4j: {store.count_documents()}\n")

    # Metadata filter -> Cypher WHERE, executed by Neo4j.
    recent_neo4j = {
        "operator": "AND",
        "conditions": [
            {"field": "meta.topic", "operator": "==", "value": "neo4j"},
            {"field": "meta.year", "operator": ">=", "value": 2023},
        ],
    }
    query = "What can Neo4j do?"
    result = qp.run(
        {"retriever": {"filters": recent_neo4j}, "prompt": {"query": query}},
        include_outputs_from={"retriever"},
    )

    print("Retrieved by filter:")
    for d in result["retriever"]["documents"]:
        print(f"  [{d.meta['topic']}/{d.meta['year']}] {d.content}")
    print(f"\nQ: {query}\nA: {result['llm']['replies'][0].text}")

    store.close()
