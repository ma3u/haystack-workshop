"""
Challenge F: Custom DocumentStore backed by Neo4j.

Implements the Haystack DocumentStore protocol:
  to_dict / from_dict / count_documents / filter_documents / write_documents / delete_documents

Storage model (one node per Document):
  (:HaystackDocument {
      id, content, embedding,          # native properties
      `meta.<key>`: primitive value,   # flattened meta, queryable by filters
      meta_json, blob_json, ...        # lossless payload for exact round-trips
  })

Haystack filters are compiled to a Cypher WHERE clause, so filtering runs inside Neo4j.
Field names are passed as parameters (n[$f0]), so filters cannot inject Cypher.

Vector search (used by Neo4jEmbeddingRetriever): pass `embedding_dimension` and the store creates a
native Neo4j vector index on n.embedding (core Cypher, Neo4j >= 5.11, Community or Enterprise, no plugin).

Env vars (see .env.example): NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD
"""
import json
import os
import time
from dataclasses import replace
from typing import Any

from haystack import Document, default_from_dict, default_to_dict
from haystack.document_stores.errors import DuplicateDocumentError
from haystack.document_stores.types import DuplicatePolicy
from haystack.errors import FilterError
from haystack.utils import Secret, deserialize_secrets_inplace
from haystack.utils.filters import _looks_like_iso_date
from neo4j import GraphDatabase
from neo4j.exceptions import ClientError

_COMPARISON_OPS = {"==", "!=", ">", ">=", "<", "<=", "in", "not in"}
_LOGIC_OPS = {"AND", "OR", "NOT"}
_PRIMITIVES = (str, int, float, bool)
_SIMILARITIES = {"cosine", "euclidean"}


class Neo4jDocumentStore:
    def __init__(
        self,
        uri: str | None = None,
        username: str | None = None,
        password: Secret = Secret.from_env_var("NEO4J_PASSWORD"),
        database: str = "neo4j",
        label: str = "HaystackDocument",
        embedding_dimension: int | None = None,
        similarity: str = "cosine",
    ):
        self.uri = uri or os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
        self.username = username or os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = password
        self.database = database
        if not label.isidentifier():
            raise ValueError(f"Invalid label '{label}': must be a plain identifier.")
        self.label = label
        if similarity not in _SIMILARITIES:
            raise ValueError(f"similarity must be one of {sorted(_SIMILARITIES)}, got '{similarity}'")
        self.embedding_dimension = embedding_dimension
        self.similarity = similarity
        self.vector_index = f"{label.lower()}_embedding"

        self._driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password.resolve_value()))
        self._driver.verify_connectivity()
        # Unique constraint = fast id lookups + DB-level guard against duplicates.
        self._run(
            f"CREATE CONSTRAINT {self.label.lower()}_id IF NOT EXISTS "
            f"FOR (n:{self.label}) REQUIRE n.id IS UNIQUE"
        )
        if embedding_dimension:
            self._ensure_vector_index()

    # ---------- serialization ----------

    def to_dict(self) -> dict[str, Any]:
        return default_to_dict(
            self,
            uri=self.uri,
            username=self.username,
            password=self.password.to_dict(),
            database=self.database,
            label=self.label,
            embedding_dimension=self.embedding_dimension,
            similarity=self.similarity,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Neo4jDocumentStore":
        deserialize_secrets_inplace(data["init_parameters"], keys=["password"])
        return default_from_dict(cls, data)

    def close(self) -> None:
        self._driver.close()

    # ---------- protocol ----------

    def count_documents(self) -> int:
        return self._run(f"MATCH (n:{self.label}) RETURN count(n) AS c")[0]["c"]

    def filter_documents(self, filters: dict[str, Any] | None = None) -> list[Document]:
        params: dict[str, Any] = {}
        where = f"WHERE {_compile(filters, params)}" if filters else ""
        # Cypher gives no order guarantee; _seq keeps insertion order (like InMemoryDocumentStore).
        records = self._run(f"MATCH (n:{self.label}) {where} RETURN n ORDER BY n._seq", **params)
        return [self._to_document(r["n"]) for r in records]

    def write_documents(self, documents: list[Document], policy: DuplicatePolicy = DuplicatePolicy.NONE) -> int:
        if not isinstance(documents, list) or any(not isinstance(d, Document) for d in documents):
            raise ValueError("Please provide a list of Documents.")
        if policy == DuplicatePolicy.NONE:
            policy = DuplicatePolicy.FAIL

        if policy == DuplicatePolicy.OVERWRITE:
            # MERGE + `SET n = props` replaces all properties, so stale meta keys disappear.
            rows = [{"id": d.id, "props": self._to_properties(d, seq)} for seq, d in _sequenced(documents)]
            self._run(f"UNWIND $rows AS r MERGE (n:{self.label} {{id: r.id}}) SET n = r.props", rows=rows)
            return len(documents)

        existing = {
            r["id"]
            for r in self._run(
                f"MATCH (n:{self.label}) WHERE n.id IN $ids RETURN n.id AS id", ids=[d.id for d in documents]
            )
        }
        if existing and policy == DuplicatePolicy.FAIL:
            raise DuplicateDocumentError(f"IDs already exist: {sorted(existing)}")

        new_docs = list({d.id: d for d in documents if d.id not in existing}.values())
        self._run(
            f"UNWIND $rows AS props CREATE (n:{self.label}) SET n = props",
            rows=[self._to_properties(d, seq) for seq, d in _sequenced(new_docs)],
        )
        return len(new_docs)

    def delete_documents(self, document_ids: list[str]) -> None:
        self._run(f"MATCH (n:{self.label}) WHERE n.id IN $ids DETACH DELETE n", ids=document_ids)

    # ---------- vector search ----------

    def embedding_retrieval(
        self, query_embedding: list[float], filters: dict[str, Any] | None = None, top_k: int = 10
    ) -> list[Document]:
        """
        Return the top_k documents most similar to query_embedding, best first, with `score` set.

        - No filters: approximate nearest neighbours via the vector index (fast, scales).
          Uses the Cypher 25 SEARCH clause when the server has it, else db.index.vector.queryNodes.
        - With filters: filter in Cypher first, then score the matches exactly with
          vector.similarity.*(). Post-filtering ANN results would silently return fewer than top_k.

        Both paths return the same normalized score in [0, 1] (higher = more similar).
        """
        if not self.embedding_dimension:
            raise ValueError("Create the store with embedding_dimension=<int> to enable vector search.")
        if len(query_embedding) != self.embedding_dimension:
            raise ValueError(
                f"query_embedding has {len(query_embedding)} dims, index expects {self.embedding_dimension}"
            )

        params: dict[str, Any] = {"q": query_embedding, "k": top_k}
        if filters:
            query = (
                f"MATCH (n:{self.label}) WHERE n.embedding IS NOT NULL AND {_compile(filters, params)} "
                f"WITH n, vector.similarity.{self.similarity}(n.embedding, $q) AS score "
                "RETURN n, score ORDER BY score DESC LIMIT $k"
            )
        elif self._use_search_clause:
            # Index names can't be parameters in SEARCH; vector_index is built from a validated identifier.
            query = (
                f"CYPHER 25 MATCH (n:{self.label}) "
                f"SEARCH n IN (VECTOR INDEX {self.vector_index} FOR $q LIMIT $k) SCORE AS score "
                "RETURN n, score ORDER BY score DESC"
            )
        else:
            params["index"] = self.vector_index
            query = "CALL db.index.vector.queryNodes($index, $k, $q) YIELD node AS n, score RETURN n, score"

        return [replace(self._to_document(r["n"]), score=r["score"]) for r in self._run(query, **params)]

    def _ensure_vector_index(self) -> None:
        self._run(
            f"CREATE VECTOR INDEX {self.vector_index} IF NOT EXISTS FOR (n:{self.label}) ON (n.embedding) "
            "OPTIONS {indexConfig: {`vector.dimensions`: $dim, `vector.similarity_function`: $sim}}",
            dim=self.embedding_dimension,
            sim=self.similarity,
        )
        # IF NOT EXISTS keeps an old index silently, so check it matches what we asked for.
        cfg = self._run(
            "SHOW VECTOR INDEXES YIELD name, options WHERE name = $name RETURN options.indexConfig AS cfg",
            name=self.vector_index,
        )[0]["cfg"]
        if cfg["vector.dimensions"] != self.embedding_dimension or cfg["vector.similarity_function"].lower() != self.similarity:
            raise ValueError(
                f"Index '{self.vector_index}' exists with {cfg}. Drop it (DROP INDEX {self.vector_index}) "
                "or use another label."
            )
        self._run("CALL db.awaitIndexes(300)")
        self._use_search_clause = self._supports_search_clause()

    def _supports_search_clause(self) -> bool:
        """Neo4j 2026.x deprecates db.index.vector.queryNodes for the Cypher 25 SEARCH clause; 5.x lacks SEARCH."""
        try:
            self._run(
                f"CYPHER 25 MATCH (n:{self.label}) SEARCH n IN (VECTOR INDEX {self.vector_index} FOR $q LIMIT 1) "
                "SCORE AS score RETURN count(*)",
                q=[1.0] * self.embedding_dimension,
            )
            return True
        except ClientError:
            return False

    def run_cypher(self, query: str, **params: Any) -> list:
        """Run raw Cypher on this store's database. Used by the GraphRAG components to share one driver."""
        return self._run(query, **params)

    # ---------- helpers ----------

    def _run(self, query: str, **params: Any) -> list:
        records, _, _ = self._driver.execute_query(query, params, database_=self.database)
        return records

    @staticmethod
    def _to_properties(doc: Document, seq: int) -> dict[str, Any]:
        props: dict[str, Any] = {
            "id": doc.id,
            "_seq": seq,
            "content": doc.content,
            "embedding": doc.embedding,
            "meta_json": json.dumps(doc.meta),
            "blob_json": json.dumps(doc.blob.to_dict()) if doc.blob else None,
            "sparse_embedding_json": json.dumps(doc.sparse_embedding.to_dict()) if doc.sparse_embedding else None,
        }
        props.update(_flatten_meta(doc.meta, prefix="meta."))
        # Neo4j drops null properties anyway; skip them to keep the payload small.
        return {k: v for k, v in props.items() if v is not None}

    @staticmethod
    def _to_document(node) -> Document:
        def loads(key):
            return json.loads(node[key]) if node.get(key) else None

        return Document.from_dict(
            {
                "id": node["id"],
                "content": node.get("content"),
                "embedding": node.get("embedding"),
                "meta": loads("meta_json") or {},
                "blob": loads("blob_json"),
                "sparse_embedding": loads("sparse_embedding_json"),
            }
        )


def _sequenced(documents: list[Document]) -> list[tuple[int, Document]]:
    """Pair each document with a monotonically increasing insertion sequence number."""
    base = time.time_ns()
    return [(base + i, d) for i, d in enumerate(documents)]


def _flatten_meta(meta: dict[str, Any], prefix: str) -> dict[str, Any]:
    """Flatten nested meta into `meta.a.b` keys, keeping only values Neo4j can store as properties."""
    flat: dict[str, Any] = {}
    for key, value in meta.items():
        if isinstance(value, dict):
            flat.update(_flatten_meta(value, f"{prefix}{key}."))
        elif isinstance(value, _PRIMITIVES):
            flat[f"{prefix}{key}"] = value
        elif isinstance(value, list) and value and len({type(v) for v in value}) == 1 and isinstance(value[0], _PRIMITIVES):
            flat[f"{prefix}{key}"] = value
        # None, empty / mixed lists, objects: kept only in meta_json, not filterable.
    return flat


def _compile(node: dict[str, Any], params: dict[str, Any]) -> str:
    """Translate a Haystack filter dict into a Cypher boolean expression, collecting params."""
    if "operator" not in node:
        raise FilterError(f"'operator' key missing in {node}")

    if "field" not in node:
        op = node["operator"]
        if op not in _LOGIC_OPS:
            raise FilterError(f"Unknown logical operator '{op}'")
        if "conditions" not in node:
            raise FilterError(f"'conditions' key missing in {node}")
        parts = [f"({_compile(c, params)})" for c in node["conditions"]]
        if op == "NOT":
            return f"NOT ({' AND '.join(parts) or 'true'})"
        return f" {op} ".join(parts) or "true"

    op = node["operator"]
    if op not in _COMPARISON_OPS:
        raise FilterError(f"Unknown comparison operator '{op}'")
    if "value" not in node:
        raise FilterError(f"'value' key missing in {node}")
    # Cypher uses 3-valued logic: `missing = 1` is NULL, and NOT NULL is still NULL.
    # Haystack expects plain booleans (a missing field is simply "not equal"), so force NULL -> false.
    return f"coalesce({_comparison(op, node['field'], node['value'], params)}, false)"


def _comparison(op: str, field: str, value: Any, params: dict[str, Any]) -> str:
    f, v = f"f{len(params)}", f"v{len(params)}"
    params[f], params[v] = field, value
    prop = f"n[${f}]"

    if op == "==":
        return f"{prop} IS NULL" if value is None else f"{prop} = ${v}"
    if op == "!=":
        return f"{prop} IS NOT NULL" if value is None else f"({prop} IS NULL OR {prop} <> ${v})"
    if op in ("in", "not in"):
        if not isinstance(value, list):
            raise FilterError(f"'{op}' needs a list value, got {type(value).__name__}")
        return f"{prop} IN ${v}" if op == "in" else f"({prop} IS NULL OR NOT {prop} IN ${v})"

    # Ordering operators: >, >=, <, <=
    if value is None:
        return "false"
    if isinstance(value, list):
        raise FilterError(f"'{op}' does not support list values")
    if isinstance(value, str):
        if not _looks_like_iso_date(value):
            raise FilterError(f"'{op}' on strings only supports ISO 8601 dates, got '{value}'")
        # Compare as datetimes; the regex guard skips docs whose field is a non-date string.
        return f"({prop} =~ '\\\\d{{4}}-\\\\d{{2}}-\\\\d{{2}}.*' AND datetime({prop}) {op} datetime(${v}))"
    return f"{prop} {op} ${v}"
