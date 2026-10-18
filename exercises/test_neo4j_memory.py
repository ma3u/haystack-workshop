"""
Tests for Neo4jMemory (no LLM needed). Needs a running Neo4j with NEO4J_* in .env.

    pytest exercises/test_neo4j_memory.py -v
"""
import uuid

import pytest
from dotenv import load_dotenv
from haystack.dataclasses import ChatMessage, ToolCall

from neo4j_memory import Neo4jMemory, entity_key, relation_type

load_dotenv()


@pytest.fixture
def memory():
    mem = Neo4jMemory(user_id=f"test-{uuid.uuid4().hex[:8]}")
    yield mem
    mem.reset()
    mem._run("MATCH (u:MemoryUser {id: $u}) DELETE u", u=mem.user_id)
    mem.close()


def test_normalizers():
    assert entity_key("  The   Haystack ") == "haystack"
    assert relation_type("is allergic to") == "IS_ALLERGIC_TO"
    assert relation_type("!!") == "RELATED_TO"


def test_remember_recall_merges_entities(memory):
    memory.remember("User", "works at", "deepset")
    memory.remember("the Deepset", "builds", "Haystack")  # merges with 'deepset'
    facts = memory.recall("user")  # 2 hops: User -> deepset -> Haystack
    assert "User -WORKS_AT-> deepset" in facts
    assert "deepset -BUILDS-> Haystack" in facts


def test_recall_hops_are_clamped(memory):
    memory.remember("A", "to", "B")
    memory.remember("B", "to", "C")
    assert "B -TO-> C" not in memory.recall("a", hops=1)
    assert "B -TO-> C" in memory.recall("a", hops=99)  # clamped to 3, no Cypher injection via hops


def test_forget(memory):
    memory.remember("User", "lives in", "Berlin")
    assert memory.forget("User", "LIVES_IN", "berlin").startswith("Forgot 1")
    assert memory.recall("") == "Memory is empty."
    assert memory.forget("User", "LIVES_IN", "berlin").startswith("Forgot 0")  # already retired
    assert "User -LIVES_IN-> Berlin  (forgotten " in memory.recall("", history=True)


def test_remember_revives_forgotten_fact(memory):
    memory.remember("User", "lives in", "Berlin")
    memory.forget("User", "lives in", "Berlin")
    memory.remember("User", "lives in", "Berlin")
    assert memory.recall("") == "User -LIVES_IN-> Berlin"


def test_forget_retires_detached_entities(memory):
    # Real case from the live test: the workshop switched Azure OpenAI resources
    memory.remember("User", "prepares for", "hackathon")
    memory.remember("hackathon", "uses", "haystack-workshop")
    memory.remember("haystack-workshop", "LLM provider", "oai-old")
    memory.remember("oai-old", "belongs to", "EHDS project")
    result = memory.forget("haystack-workshop", "LLM provider", "oai-old")
    assert "Also retired 1 fact(s) only reachable through it: oai-old -BELONGS_TO-> EHDS project" in result
    assert memory.recall("oai") == "No memories found for 'oai'."
    assert "oai-old -BELONGS_TO-> EHDS project  (forgotten " in memory.recall("oai", history=True)
    assert "User -PREPARES_FOR-> hackathon" in memory.recall("user")


def test_forget_keeps_entities_still_connected(memory):
    memory.remember("User", "works at", "deepset")
    memory.remember("User", "uses", "Haystack")
    memory.remember("deepset", "builds", "Haystack")
    memory.remember("Haystack", "written in", "Python")
    result = memory.forget("User", "uses", "Haystack")  # Haystack still reachable via deepset
    assert "Also retired" not in result
    assert "Haystack -WRITTEN_IN-> Python" in memory.recall("haystack")


def test_forget_never_retires_the_user_side(memory):
    memory.remember("User", "likes", "tea")
    memory.remember("User", "lives in", "Berlin")
    memory.remember("Berlin", "is in", "Germany")
    memory.remember("Germany", "is in", "Europe")  # Berlin side (3 entities) is bigger than the User side
    memory.forget("User", "lives in", "Berlin")
    assert memory.recall("user") == "User -LIKES-> tea"
    assert "Germany -IS_IN-> Europe" in memory.recall("berlin")


def test_users_are_isolated(memory):
    memory.remember("User", "likes", "tea")
    other = Neo4jMemory(user_id=f"{memory.user_id}-other")
    try:
        assert other.recall("") == "Memory is empty."
    finally:
        other._run("MATCH (u:MemoryUser {id: $u}) DELETE u", u=other.user_id)
        other.close()


def test_recall_tool_guards_repeat_calls_per_turn(memory):
    memory.remember("User", "likes", "tea")
    recall = next(t for t in memory.tools() if t.name == "recall")
    assert "tea" in recall.invoke(query="")
    assert recall.invoke(query="").startswith("Already recalled")
    assert "tea" in recall.invoke(query="user")  # different query is allowed
    memory.new_turn()
    assert "tea" in recall.invoke(query="")


def test_history_roundtrip_skips_tool_messages(memory):
    memory.save_messages("s1", [
        ChatMessage.from_user("hi"),
        ChatMessage.from_assistant(tool_calls=[ToolCall(tool_name="recall", arguments={"query": ""})]),
        ChatMessage.from_assistant("hello"),
    ])
    memory.save_messages("s1", [ChatMessage.from_user("again"), ChatMessage.from_assistant("yes")])
    hist = memory.load_history("s1")
    assert [(m.role.value, m.text) for m in hist] == [
        ("user", "hi"), ("assistant", "hello"), ("user", "again"), ("assistant", "yes"),
    ]
    assert [m.text for m in memory.load_history("s1", last_n=2)] == ["again", "yes"]
    assert memory.load_history("other-session") == []
