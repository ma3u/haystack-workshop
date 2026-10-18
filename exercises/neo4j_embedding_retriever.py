"""
Neo4jEmbeddingRetriever: dense retrieval over Neo4jDocumentStore's native vector index.

Drop-in counterpart of InMemoryEmbeddingRetriever:
  input  query_embedding: list[float]  (from a TextEmbedder)
  output documents: list[Document]     (best first, with .score)
"""
from typing import Any

from haystack import Document, component, default_from_dict, default_to_dict
from haystack.document_stores.types import FilterPolicy
from haystack.document_stores.types.filter_policy import apply_filter_policy

from neo4j_document_store import Neo4jDocumentStore


@component
class Neo4jEmbeddingRetriever:
    def __init__(
        self,
        document_store: Neo4jDocumentStore,
        filters: dict[str, Any] | None = None,
        top_k: int = 10,
        filter_policy: FilterPolicy = FilterPolicy.REPLACE,
    ):
        if not isinstance(document_store, Neo4jDocumentStore):
            raise ValueError("document_store must be a Neo4jDocumentStore")
        if top_k <= 0:
            raise ValueError(f"top_k must be > 0, got {top_k}")
        self.document_store = document_store
        self.filters = filters
        self.top_k = top_k
        self.filter_policy = filter_policy

    def to_dict(self) -> dict[str, Any]:
        return default_to_dict(
            self,
            document_store=self.document_store.to_dict(),
            filters=self.filters,
            top_k=self.top_k,
            filter_policy=self.filter_policy.value,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Neo4jEmbeddingRetriever":
        params = data["init_parameters"]
        params["document_store"] = Neo4jDocumentStore.from_dict(params["document_store"])
        params["filter_policy"] = FilterPolicy.from_str(params["filter_policy"])
        return default_from_dict(cls, data)

    @component.output_types(documents=list[Document])
    def run(
        self, query_embedding: list[float], filters: dict[str, Any] | None = None, top_k: int | None = None
    ) -> dict[str, list[Document]]:
        """
        :param query_embedding: Embedding of the query, same model/dimension as the indexed documents.
        :param filters: Runtime filters; combined with init filters according to filter_policy.
        :param top_k: Overrides the init top_k.
        """
        filters = apply_filter_policy(self.filter_policy, self.filters, filters)
        docs = self.document_store.embedding_retrieval(
            query_embedding=query_embedding, filters=filters, top_k=top_k or self.top_k
        )
        return {"documents": docs}
