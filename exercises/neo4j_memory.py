"""
Agent memory backed by Neo4j: short-term conversation memory + long-term graph memory.

Graph model (all labels prefixed `Memory`, so they never collide with other data in the same DB):

  Conversation memory (per session, survives process restarts)
    (:MemoryUser {id})-[:HAS_SESSION]->(:MemorySession {id})-[:HAS_MESSAGE]->(:MemoryMessage {seq, role, text, ts})

  Long-term memory (per user, shared across sessions) as a knowledge graph
    (:MemoryUser {id})-[:REMEMBERS]->(:MemoryEntity {uid, user_id, key, name})
    (:MemoryEntity)-[:MEM_REL {type, ts, valid_to?}]->(:MemoryEntity)

`forget` does not delete a fact, it sets `valid_to` (the fact is retired). Recall shows live facts only, unless
`history=True`. If the forgotten fact was the only link to a group of entities, the facts of that group are retired
too, so they no longer show up in a name search (never a group that contains the 'User' entity).

Relation types are stored as a property (r.type) because Cypher cannot create a dynamic
relationship type without APOC (same choice as neo4j_graph.py).

The agent gets remember / recall / forget as tools (see `tools()`), so the LLM decides
what is worth storing and when to look things up.

Env vars (see .env.example): NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD
"""
import os
import re
import time
from typing import Annotated

from haystack.dataclasses import ChatMessage, ChatRole
from haystack.tools import Tool, create_tool_from_function
from haystack.utils import Secret
from neo4j import GraphDatabase


def entity_key(name: str) -> str:
    """Normalize names so 'The Haystack' and 'haystack' merge into one node."""
    key = re.sub(r"\s+", " ", name).strip().lower()
    return key[4:] if key.startswith("the ") else key


def relation_type(relation: str) -> str:
    """'works at' -> 'WORKS_AT'."""
    return re.sub(r"[^A-Z0-9]+", "_", relation.strip().upper()).strip("_") or "RELATED_TO"


class Neo4jMemory:
    def __init__(
        self,
        user_id: str,
        uri: str | None = None,
        username: str | None = None,
        password: Secret = Secret.from_env_var("NEO4J_PASSWORD"),
        database: str = "neo4j",
    ):
        self.user_id = user_id
        self.database = database
        uri = uri or os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
        username = username or os.getenv("NEO4J_USERNAME", "neo4j")
        self._driver = GraphDatabase.driver(uri, auth=(username, password.resolve_value()))
        self._driver.verify_connectivity()
        for stmt in (
            "CREATE CONSTRAINT memory_user_id IF NOT EXISTS FOR (n:MemoryUser) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT memory_session_id IF NOT EXISTS FOR (n:MemorySession) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT memory_entity_uid IF NOT EXISTS FOR (n:MemoryEntity) REQUIRE n.uid IS UNIQUE",
        ):
            self._run(stmt)
        self._run("MERGE (:MemoryUser {id: $u})", u=user_id)
        self._recalled_this_turn: set[str] = set()

    def new_turn(self) -> None:
        """Call before each agent run. Resets the per-turn recall guard used by the recall tool."""
        self._recalled_this_turn.clear()

    def close(self) -> None:
        self._driver.close()

    def _run(self, query: str, **params) -> list:
        records, _, _ = self._driver.execute_query(query, params, database_=self.database)
        return records

    # ---------- conversation memory ----------

    def load_history(self, session_id: str, last_n: int = 20) -> list[ChatMessage]:
        """Last N user/assistant messages of a session, oldest first."""
        records = self._run(
            "MATCH (:MemoryUser {id: $u})-[:HAS_SESSION]->(:MemorySession {id: $s})-[:HAS_MESSAGE]->(m) "
            "WITH m ORDER BY m.seq DESC LIMIT $n RETURN m.role AS role, m.text AS text ORDER BY m.seq",
            u=self.user_id, s=session_id, n=last_n,
        )
        build = {"user": ChatMessage.from_user, "assistant": ChatMessage.from_assistant}
        return [build[r["role"]](r["text"]) for r in records]

    def save_messages(self, session_id: str, messages: list[ChatMessage]) -> None:
        """Append user/assistant text messages. Tool calls/results are skipped: they only make
        sense next to their paired call, and the facts they produced already live in the graph."""
        rows = [
            {"role": m.role.value, "text": m.text}
            for m in messages
            if m.role in (ChatRole.USER, ChatRole.ASSISTANT) and m.text and not m.tool_calls
        ]
        if not rows:
            return
        self._run(
            "MATCH (u:MemoryUser {id: $u}) "
            "MERGE (u)-[:HAS_SESSION]->(s:MemorySession {id: $s}) "
            "WITH s, COUNT { (s)-[:HAS_MESSAGE]->() } AS start "
            "UNWIND range(0, size($rows) - 1) AS i "
            "CREATE (s)-[:HAS_MESSAGE]->(:MemoryMessage {seq: start + i, role: $rows[i].role, "
            "text: $rows[i].text, ts: datetime()})",
            u=self.user_id, s=session_id, rows=rows,
        )

    # ---------- long-term graph memory ----------

    def remember(self, subject: str, relation: str, obj: str) -> str:
        rel = relation_type(relation)
        self._run(
            "MATCH (u:MemoryUser {id: $u}) "
            "MERGE (a:MemoryEntity {uid: $u + '::' + $ak}) ON CREATE SET a.user_id = $u, a.key = $ak, a.name = $an "
            "MERGE (b:MemoryEntity {uid: $u + '::' + $bk}) ON CREATE SET b.user_id = $u, b.key = $bk, b.name = $bn "
            "MERGE (u)-[:REMEMBERS]->(a) MERGE (u)-[:REMEMBERS]->(b) "
            "MERGE (a)-[r:MEM_REL {type: $rel}]->(b) SET r.ts = datetime() REMOVE r.valid_to",
            u=self.user_id, ak=entity_key(subject), an=subject.strip(), bk=entity_key(obj), bn=obj.strip(), rel=rel,
        )
        return f"Stored: {subject} -{rel}-> {obj}"

    def recall(self, query: str = "", hops: int = 2, limit: int = 50, history: bool = False) -> str:
        """Facts around entities whose name contains `query`. Empty query = everything known.
        history=True also returns retired facts, marked with the time they were forgotten."""
        hops = max(1, min(int(hops), 3))  # path length can't be a parameter; clamp before formatting
        records = self._run(
            "MATCH (e:MemoryEntity {user_id: $u}) WHERE $q = '' OR e.key CONTAINS $q "
            f"MATCH p = (e)-[:MEM_REL*1..{hops}]-() "
            "WHERE $history OR all(r IN relationships(p) WHERE r.valid_to IS NULL) "
            "UNWIND relationships(p) AS r "
            "WITH DISTINCT r RETURN startNode(r).name AS s, r.type AS t, endNode(r).name AS o, "
            "toString(r.valid_to) AS until "
            "ORDER BY r.ts LIMIT $limit",
            u=self.user_id, q=entity_key(query), limit=limit, history=history,
        )
        if not records:
            return f"No memories found for '{query}'." if query else "Memory is empty."
        return "\n".join(
            f"{r['s']} -{r['t']}-> {r['o']}" + (f"  (forgotten {r['until']})" if r["until"] else "")
            for r in records
        )

    def forget(self, subject: str, relation: str, obj: str) -> str:
        ak, bk = entity_key(subject), entity_key(obj)
        records = self._run(
            "MATCH (a:MemoryEntity {uid: $u + '::' + $ak})-[r:MEM_REL {type: $rel}]->"
            "(b:MemoryEntity {uid: $u + '::' + $bk}) WHERE r.valid_to IS NULL "
            "SET r.valid_to = datetime() RETURN count(r) AS c",
            u=self.user_id, ak=ak, bk=bk, rel=relation_type(relation),
        )
        n = records[0]["c"]
        result = f"Forgot {n} fact(s): {subject} -{relation_type(relation)}-> {obj}"
        retired = self._retire_detached(ak, bk) if n else []
        if retired:
            result += f"\nAlso retired {len(retired)} fact(s) only reachable through it: " + "; ".join(retired)
        return result

    def _component(self, key: str) -> set[str]:
        """Keys of all entities connected to `key` through live facts (either direction, up to 10 hops)."""
        records = self._run(
            "MATCH (e:MemoryEntity {uid: $u + '::' + $k}) "
            "OPTIONAL MATCH p = (e)-[:MEM_REL*1..10]-(x:MemoryEntity) "
            "WHERE all(r IN relationships(p) WHERE r.valid_to IS NULL) "
            "RETURN e.key AS key, collect(DISTINCT x.key) AS keys",
            u=self.user_id, k=key,
        )
        return {records[0]["key"], *records[0]["keys"]} if records else set()

    def _retire_detached(self, ak: str, bk: str) -> list[str]:
        """After a, b were split apart: retire the facts of the smaller side, unless it holds the 'User' entity."""
        side_a, side_b = self._component(ak), self._component(bk)
        if bk in side_a:
            return []  # still connected through other facts
        detached = side_b if len(side_b) <= len(side_a) else side_a
        if entity_key("User") in detached or len(detached) < 2:
            return []
        records = self._run(
            "MATCH (x:MemoryEntity {user_id: $u})-[r:MEM_REL]->(y:MemoryEntity {user_id: $u}) "
            "WHERE x.key IN $keys AND r.valid_to IS NULL "
            "SET r.valid_to = datetime() RETURN x.name AS s, r.type AS t, y.name AS o",
            u=self.user_id, keys=sorted(detached),
        )
        return [f"{r['s']} -{r['t']}-> {r['o']}" for r in records]

    def reset(self) -> None:
        """Delete everything stored for this user (only Memory* nodes of this user)."""
        self._run(
            "MATCH (u:MemoryUser {id: $u}) "
            "OPTIONAL MATCH (u)-[:HAS_SESSION]->(s)-[:HAS_MESSAGE]->(m) "
            "OPTIONAL MATCH (e:MemoryEntity {user_id: $u}) "
            "DETACH DELETE m, s, e",
            u=self.user_id,
        )

    # ---------- agent tools ----------

    def tools(self) -> list[Tool]:
        """remember / recall / forget bound to this user, ready for haystack Agent(tools=...)."""

        def remember(
            subject: Annotated[str, "Entity the fact is about. Use 'User' for the person you are talking to."],
            relation: Annotated[str, "Short verb phrase, e.g. 'works at', 'lives in', 'is allergic to', 'likes'."],
            object: Annotated[str, "The other entity or value, e.g. 'deepset', 'Berlin', 'peanuts'."],
        ) -> str:
            """Store one durable fact in long-term memory as subject -relation-> object.
            Call once per fact. Use for stable facts about the user and their world, not small talk."""
            return self.remember(subject, relation, object)

        def recall(
            query: Annotated[str, "Entity name to look up, e.g. 'User', 'deepset'. Empty string returns all memories."],
        ) -> str:
            """Look up facts in long-term memory, including facts connected to the entity (2 hops)."""
            # Guard: some models loop on recall. Prompts alone don't stop it, so enforce it here.
            key = entity_key(query)
            if key in self._recalled_this_turn:
                return (f"Already recalled '{query}' this turn; memory has not changed except for your own "
                        "remember/forget calls. Do not call recall again. Use the facts above and reply now.")
            self._recalled_this_turn.add(key)
            return self.recall(query)

        def forget(
            subject: Annotated[str, "Subject of the fact to delete."],
            relation: Annotated[str, "Relation of the fact to delete."],
            object: Annotated[str, "Object of the fact to delete."],
        ) -> str:
            """Retire a fact that is wrong or outdated. Store the corrected fact with remember."""
            return self.forget(subject, relation, object)

        return [create_tool_from_function(f) for f in (remember, recall, forget)]
