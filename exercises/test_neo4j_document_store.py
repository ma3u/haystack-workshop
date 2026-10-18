"""
Runs Haystack's official DocumentStore conformance suite against Neo4jDocumentStore.

Needs a running Neo4j (NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD in .env).
Uses a separate label, so workshop data is not touched.

    pytest test_neo4j_document_store.py -q
"""
import pytest
from dotenv import load_dotenv
from haystack import Document, Pipeline
from haystack.document_stores.errors import DuplicateDocumentError
from haystack.document_stores.types import FilterPolicy
from haystack.testing.document_store import DocumentStoreBaseTests

from neo4j_document_store import Neo4jDocumentStore
from neo4j_embedding_retriever import Neo4jEmbeddingRetriever

load_dotenv()


class TestNeo4jDocumentStore(DocumentStoreBaseTests):
    @pytest.fixture
    def document_store(self):
        store = Neo4jDocumentStore(label="HaystackTestDocument")
        store.delete_documents([d.id for d in store.filter_documents()])
        yield store
        store.delete_documents([d.id for d in store.filter_documents()])
        store.close()

    def test_to_dict_from_dict_roundtrip(self, document_store):
        data = document_store.to_dict()
        assert data["init_parameters"]["password"]["type"] == "env_var"  # secret never serialized
        restored = Neo4jDocumentStore.from_dict(data)
        assert restored.label == "HaystackTestDocument"
        restored.close()

    def test_write_documents(self, document_store):
        docs = [Document(content="a"), Document(content="b")]
        assert document_store.write_documents(docs) == 2
        self.assert_documents_are_equal(document_store.filter_documents(), docs)
        # Default policy (NONE) behaves like FAIL, same as InMemoryDocumentStore.
        with pytest.raises(DuplicateDocumentError):
            document_store.write_documents(docs[:1])


class TestNeo4jEmbeddingRetriever:
    DIM = 4

    @pytest.fixture
    def store(self):
        store = Neo4jDocumentStore(label="HaystackVectorTest", embedding_dimension=self.DIM)
        store.delete_documents([d.id for d in store.filter_documents()])
        store.write_documents(
            [
                Document(content="x-axis", embedding=[1.0, 0.0, 0.0, 0.0], meta={"group": "a"}),
                Document(content="near-x", embedding=[0.9, 0.1, 0.0, 0.0], meta={"group": "b"}),
                Document(content="y-axis", embedding=[0.2, 1.0, 0.0, 0.0], meta={"group": "a"}),
                Document(content="z-axis", embedding=[0.0, 0.0, 1.0, 0.0], meta={"group": "b"}),
                Document(content="no embedding", meta={"group": "a"}),
            ]
        )
        yield store
        store.delete_documents([d.id for d in store.filter_documents()])
        store.close()

    def test_index_search_ranks_by_similarity(self, store):
        docs = Neo4jEmbeddingRetriever(store, top_k=3).run(query_embedding=[1.0, 0.0, 0.0, 0.0])["documents"]
        assert [d.content for d in docs] == ["x-axis", "near-x", "y-axis"]
        assert docs[0].score == pytest.approx(1.0)
        assert all(0.0 <= d.score <= 1.0 for d in docs)
        assert docs[0].score >= docs[1].score >= docs[2].score

    def test_filtered_search_still_returns_top_k(self, store):
        # Best overall match (x-axis) is in group "a"; filter must not shrink the result below top_k.
        flt = {"field": "meta.group", "operator": "==", "value": "b"}
        docs = Neo4jEmbeddingRetriever(store, top_k=2).run(query_embedding=[1.0, 0.0, 0.0, 0.0], filters=flt)
        assert [d.content for d in docs["documents"]] == ["near-x", "z-axis"]

    def test_filtered_and_index_scores_agree(self, store):
        q = [0.5, 0.5, 0.0, 0.0]
        retriever = Neo4jEmbeddingRetriever(store, top_k=4)
        ann = retriever.run(query_embedding=q)["documents"]
        exact = retriever.run(query_embedding=q, filters={"field": "meta.group", "operator": "in", "value": ["a", "b"]})
        assert [(d.content, round(d.score, 5)) for d in ann] == [(d.content, round(d.score, 5)) for d in exact["documents"]]

    def test_init_filters_with_merge_policy(self, store):
        retriever = Neo4jEmbeddingRetriever(
            store,
            top_k=5,
            filters={"field": "meta.group", "operator": "==", "value": "a"},
            filter_policy=FilterPolicy.MERGE,
        )
        runtime = {"field": "content", "operator": "!=", "value": "x-axis"}
        docs = retriever.run(query_embedding=[1.0, 0.0, 0.0, 0.0], filters=runtime)["documents"]
        assert [d.content for d in docs] == ["y-axis"]

    def test_wrong_query_dimension_raises(self, store):
        with pytest.raises(ValueError, match="dims"):
            Neo4jEmbeddingRetriever(store).run(query_embedding=[1.0, 0.0])

    def test_existing_index_with_other_dimension_raises(self, store):
        with pytest.raises(ValueError, match="exists"):
            Neo4jDocumentStore(label="HaystackVectorTest", embedding_dimension=8)

    def test_pipeline_serialization_roundtrip(self, store):
        pipe = Pipeline()
        pipe.add_component("retriever", Neo4jEmbeddingRetriever(store, top_k=1))
        # Haystack only deserializes trusted modules; our local modules must be allowlisted explicitly.
        restored = Pipeline.loads(pipe.dumps(), allowed_modules=["neo4j_embedding_retriever", "neo4j_document_store"])
        docs = restored.run({"retriever": {"query_embedding": [0.0, 0.0, 1.0, 0.0]}})["retriever"]["documents"]
        assert [d.content for d in docs] == ["z-axis"]

    def test_query_nodes_fallback_matches_search_clause(self, store):
        # Neo4j 5.x has no SEARCH clause; the db.index.vector.queryNodes path must rank identically.
        q = [0.5, 0.5, 0.0, 0.0]
        retriever = Neo4jEmbeddingRetriever(store, top_k=4)
        primary = retriever.run(query_embedding=q)["documents"]
        store._use_search_clause = False
        fallback = retriever.run(query_embedding=q)["documents"]
        assert [(d.content, round(d.score, 5)) for d in primary] == [(d.content, round(d.score, 5)) for d in fallback]
