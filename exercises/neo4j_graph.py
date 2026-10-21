"""
GraphRAG components on Neo4j (plain Cypher, no APOC / GDS needed).

Indexing:
  LLMGraphExtractor  chunk text -> {entities, relations} via the LLM (JSON mode)
  Neo4jGraphWriter   (:GraphRagEntity)-[:REL {type}]->(:GraphRagEntity),  (chunk)-[:MENTIONS]->(entity)

Query:
  Neo4jGraphRetriever  seed chunks + question -> linked entities -> k-hop expansion -> facts as text

Graph model: relation types are stored as a property (r.type), because Cypher 5 cannot create a
dynamic relationship type without APOC. Entities are merged on a normalized `key` (lowercased name).
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from haystack import Document, component, default_from_dict, default_to_dict, logging
from haystack.dataclasses import ChatMessage

from llm import chat_generator
from neo4j_document_store import Neo4jDocumentStore

logger = logging.getLogger(__name__)

ENTITY_LABEL = "GraphRagEntity"
ENTITY_TYPES = ["Person", "Organization", "Project", "Team", "Technology", "Location", "Other"]

EXTRACTION_PROMPT = """Extract a knowledge graph from the text below.

Return JSON: {{"entities": [{{"name": str, "type": str}}], "relations": [{{"source": str, "relation": str, "target": str}}]}}
Rules:
- type is one of: {types}
- Use full canonical names exactly as written (e.g. "Project Halcyon", "Lumen DB", "Lisbon office").
- Resolve pronouns (he/she/it/the team) to the named entity.
- relation is a short UPPER_SNAKE_CASE verb phrase, e.g. LEADS, REPORTS_TO, WORKS_IN, USES, DEVELOPS, MANUFACTURES, BASED_IN.
- Every relation source and target must also appear in entities.
- Only facts stated in the text. No inference.

Text:
{text}"""


def entity_key(name: str) -> str:
    """Normalize an entity name so 'The Lumen DB' and 'lumen db' merge into one node."""
    key = re.sub(r"\s+", " ", name).strip().lower()
    return key[4:] if key.startswith("the ") else key


@component
class LLMGraphExtractor:
    """Extract entities and relations from each document with an LLM, in parallel."""

    def __init__(self, max_workers: int = 8):
        self.max_workers = max_workers
        # max_retries: the OpenAI client backs off on 429 rate limits when we fan out calls.
        self._llm = chat_generator(max_retries=10, generation_kwargs={"response_format": {"type": "json_object"}})

    def to_dict(self) -> dict[str, Any]:
        return default_to_dict(self, max_workers=self.max_workers)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMGraphExtractor":
        return default_from_dict(cls, data)

    @component.output_types(extractions=list[dict])
    def run(self, documents: list[Document]) -> dict[str, list[dict]]:
        with ThreadPoolExecutor(self.max_workers) as pool:
            extractions = list(pool.map(self._extract, documents))
        return {"extractions": extractions}

    def _extract(self, doc: Document) -> dict:
        prompt = EXTRACTION_PROMPT.format(types=", ".join(ENTITY_TYPES), text=doc.content)
        reply = self._llm.run(messages=[ChatMessage.from_user(prompt)])["replies"][0].text
        try:
            data = json.loads(reply)
        except json.JSONDecodeError:
            logger.warning("Extraction for chunk {id} is not valid JSON, skipping", id=doc.id)
            data = {}
        return {"doc_id": doc.id, "entities": data.get("entities", []), "relations": data.get("relations", [])}


@component
class Neo4jGraphWriter:
    """Merge extracted entities/relations into Neo4j and link them to their source chunk."""

    def __init__(self, document_store: Neo4jDocumentStore):
        self.store = document_store
        self.store.run_cypher(
            f"CREATE CONSTRAINT {ENTITY_LABEL.lower()}_key IF NOT EXISTS "
            f"FOR (e:{ENTITY_LABEL}) REQUIRE e.key IS UNIQUE"
        )

    def to_dict(self) -> dict[str, Any]:
        return default_to_dict(self, document_store=self.store.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Neo4jGraphWriter":
        data = dict(data, init_parameters=dict(data["init_parameters"]))
        data["init_parameters"]["document_store"] = Neo4jDocumentStore.from_dict(
            data["init_parameters"]["document_store"]
        )
        return default_from_dict(cls, data)

    @component.output_types(entities_written=int, relations_written=int)
    def run(self, extractions: list[dict]) -> dict[str, int]:
        entities, mentions, relations = {}, [], []
        for ex in extractions:
            for e in ex["entities"]:
                if not e.get("name"):
                    continue
                key = entity_key(e["name"])
                entities.setdefault(key, {"key": key, "name": e["name"].strip(), "type": e.get("type", "Other")})
                mentions.append({"doc_id": ex["doc_id"], "key": key})
            for r in ex["relations"]:
                if not all(r.get(k) for k in ("source", "relation", "target")):
                    continue
                src, dst = entity_key(r["source"]), entity_key(r["target"])
                for key, name in ((src, r["source"]), (dst, r["target"])):  # LLM sometimes skips the entity entry
                    entities.setdefault(key, {"key": key, "name": name.strip(), "type": "Other"})
                    mentions.append({"doc_id": ex["doc_id"], "key": key})
                rel_type = re.sub(r"[^A-Z0-9_]", "_", r["relation"].upper())
                relations.append({"src": src, "dst": dst, "type": rel_type, "doc_id": ex["doc_id"]})

        chunk = self.store.label
        self.store.run_cypher(
            f"UNWIND $rows AS e MERGE (n:{ENTITY_LABEL} {{key: e.key}}) "
            "ON CREATE SET n.name = e.name, n.type = e.type",
            rows=list(entities.values()),
        )
        # MERGE the chunk by id, so this works whether or not DocumentWriter has run yet.
        self.store.run_cypher(
            f"UNWIND $rows AS m MERGE (c:{chunk} {{id: m.doc_id}}) "
            f"WITH c, m MATCH (e:{ENTITY_LABEL} {{key: m.key}}) MERGE (c)-[:MENTIONS]->(e)",
            rows=mentions,
        )
        self.store.run_cypher(
            f"UNWIND $rows AS r MATCH (a:{ENTITY_LABEL} {{key: r.src}}), (b:{ENTITY_LABEL} {{key: r.dst}}) "
            "MERGE (a)-[rel:REL {type: r.type}]->(b) SET rel.source_chunk = r.doc_id",
            rows=relations,
        )
        return {"entities_written": len(entities), "relations_written": len(relations)}


@component
class Neo4jGraphRetriever:
    """
    Turn a question plus seed chunks into graph facts.

    1. Entity linking: entities whose name appears in the question, plus entities the seed chunks MENTION.
    2. Expansion: all REL edges on paths up to `max_hops` from those entities. Paths may not pass
       THROUGH hub entities (degree > hub_degree, e.g. the company node): a hub links everything to
       everything, so expanding it floods the context with unrelated facts.
    3. Ranking: facts closer to a question entity come first, capped at `max_facts`.

    Seed documents pass through unchanged, so the prompt gets passages + facts.
    """

    def __init__(
        self, document_store: Neo4jDocumentStore, max_hops: int = 3, max_facts: int = 50, hub_degree: int = 12
    ):
        if not 1 <= max_hops <= 4:
            raise ValueError("max_hops must be between 1 and 4")
        self.store = document_store
        self.max_hops = max_hops
        self.max_facts = max_facts
        self.hub_degree = hub_degree

    def to_dict(self) -> dict[str, Any]:
        return default_to_dict(
            self,
            document_store=self.store.to_dict(),
            max_hops=self.max_hops,
            max_facts=self.max_facts,
            hub_degree=self.hub_degree,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Neo4jGraphRetriever":
        data = dict(data, init_parameters=dict(data["init_parameters"]))
        data["init_parameters"]["document_store"] = Neo4jDocumentStore.from_dict(
            data["init_parameters"]["document_store"]
        )
        return default_from_dict(cls, data)

    @component.output_types(documents=list[Document], facts=list[str], entities=list[str])
    def run(self, query: str, documents: list[Document]) -> dict[str, Any]:
        # Short keys ("ai", "ceo") would match inside random words, so require 4+ chars.
        q_keys = [
            r["key"]
            for r in self.store.run_cypher(
                f"MATCH (e:{ENTITY_LABEL}) WHERE size(e.key) >= 4 AND toLower($q) CONTAINS e.key RETURN e.key AS key",
                q=query,
            )
        ]
        seed_keys = [
            r["key"]
            for r in self.store.run_cypher(
                f"MATCH (c:{self.store.label})-[:MENTIONS]->(e:{ENTITY_LABEL}) WHERE c.id IN $ids RETURN DISTINCT e.key AS key",
                ids=[d.id for d in documents],
            )
        ]
        start = list(dict.fromkeys(q_keys + seed_keys))
        if not start:
            return {"documents": documents, "facts": [], "entities": []}

        # Variable-length bounds can't be parameters, so max_hops (validated int) goes into the query text.
        records = self.store.run_cypher(
            f"""
            MATCH (s:{ENTITY_LABEL}) WHERE s.key IN $start
            MATCH p = (s)-[:REL*1..{self.max_hops}]-(:{ENTITY_LABEL})
            WHERE all(n IN nodes(p)[1..-1] WHERE COUNT {{ (n)-[:REL]-() }} <= $hub_degree)
            WITH p, s, CASE WHEN s.key IN $q_keys THEN 0 ELSE 1 END AS origin
            UNWIND range(0, length(p) - 1) AS i
            WITH relationships(p)[i] AS r, origin * 10 + i AS rank
            WITH r, min(rank) AS rank
            RETURN startNode(r).name AS src, r.type AS rel, endNode(r).name AS dst, rank
            ORDER BY rank, src, rel, dst
            LIMIT $limit
            """,
            start=start,
            q_keys=q_keys,
            hub_degree=self.hub_degree,
            limit=self.max_facts,
        )
        facts = [f"{r['src']} -[{r['rel']}]-> {r['dst']}" for r in records]
        return {"documents": documents, "facts": facts, "entities": start}
