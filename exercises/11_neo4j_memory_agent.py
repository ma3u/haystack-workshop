"""
Exercise 11: Agent with Neo4j memory
Goal: Give a Haystack Agent memory that survives restarts and sessions.

Two kinds of memory (see neo4j_memory.py):
  - Conversation memory: chat history per session, stored as (:MemorySession)-[:HAS_MESSAGE]->(:MemoryMessage)
  - Long-term memory: facts as a knowledge graph (:MemoryEntity)-[:MEM_REL {type}]->(:MemoryEntity),
    written and read by the agent itself through remember / recall / forget tools

Tasks:
1. Run the demo: session 1 tells the agent facts, session 2 starts with EMPTY chat history
   and still answers, because the agent recalls from the graph.
2. Look at the graph in Neo4j Browser:
     MATCH (u:MemoryUser {id: 'demo-user'})-[:REMEMBERS]->(e)-[r:MEM_REL]->(o) RETURN e, r, o
3. Chat yourself, then restart and continue the same session:
     python exercises/11_neo4j_memory_agent.py --chat --user me --session s1
4. Run the demo a few times and compare the graphs: relation names drift (LIVES_IN vs WORKS_IN,
   TEAM vs WORKS_ON_TEAM) and the LLM sometimes skips facts. Fix it: constrain `relation` to a fixed
   vocabulary (an enum in the tool's JSON schema) and add an eval that checks expected triples.
5. Stretch: inject recalled facts into the prompt before the LLM runs (no recall tool call needed),
   add a tool that answers multi-hop questions with a Cypher path query, or store an embedding per
   MemoryEntity and recall by vector similarity (see 08_neo4j_vector_rag.py).

Note: repeated `recall` calls in one turn are blocked in neo4j_memory.py. gpt-5-mini looped on
recall until max_agent_steps in about half the runs; a prompt rule alone did not stop it.

Prereq: Neo4j running, NEO4J_* set in .env.
"""
import argparse

from haystack.components.agents import Agent
from haystack.dataclasses import ChatMessage

from llm import chat_generator
from neo4j_memory import Neo4jMemory

SYSTEM_PROMPT = """You are a personal assistant with long-term memory.
- When the user shares a durable fact about themselves or their world (including their name),
  store it with `remember`, one call per fact. Refer to the user as 'User'.
- Before answering anything personal or about earlier conversations, call `recall` once.
  Use an empty query to see everything you know. Do not call `recall` again in the same turn.
- If a stored fact is wrong or outdated, `forget` it (use the exact relation shown by recall)
  and `remember` the correction.
- Never invent facts that are not in memory. Be concise."""


def build_agent(memory: Neo4jMemory) -> Agent:
    return Agent(
        chat_generator=chat_generator(),
        tools=memory.tools(),
        system_prompt=SYSTEM_PROMPT,
        max_agent_steps=10,
    )


def chat_turn(agent: Agent, memory: Neo4jMemory, session_id: str, text: str, verbose: bool = True) -> str:
    """One turn: load session history from Neo4j, run agent, persist the new turn."""
    history = memory.load_history(session_id)
    user_msg = ChatMessage.from_user(text)
    memory.new_turn()
    result = agent.run(messages=[*history, user_msg])
    new_messages = result["messages"][len(history) + 1:]  # +1: the system prompt the agent prepends

    if verbose:
        for m in new_messages:
            for call in m.tool_calls:
                print(f"    [tool] {call.tool_name}({', '.join(f'{k}={v!r}' for k, v in call.arguments.items())})")

    reply = result["messages"][-1]
    if not reply.text:  # e.g. max_agent_steps hit mid tool loop: don't persist a half turn
        return "(no answer: agent stopped before replying, try again)"
    memory.save_messages(session_id, [user_msg, reply])
    return reply.text


def demo() -> None:
    memory = Neo4jMemory(user_id="demo-user")
    memory.reset()  # clean slate so the demo is repeatable
    agent = build_agent(memory)

    print("=== Session 1: tell the agent about yourself ===")
    q1 = ("Hi! I'm Mara. I work at deepset in Berlin on the Haystack team, my team lead is Jonas, "
          "and I'm allergic to peanuts.")
    print(f"> {q1}\n{chat_turn(agent, memory, 'session-1', q1)}\n")

    print("=== Session 2: new session, empty chat history ===")
    print(f"    chat history loaded for session-2: {len(memory.load_history('session-2'))} messages")
    q2 = "Who is my team lead, and which city do I work in? Also suggest a snack I can safely eat."
    print(f"> {q2}\n{chat_turn(agent, memory, 'session-2', q2)}\n")

    print("=== Session 2: correct a fact ===")
    q3 = "Update: I moved to the Lisbon office last month."
    print(f"> {q3}\n{chat_turn(agent, memory, 'session-2', q3)}\n")

    print("=== Long-term memory graph (straight from Neo4j) ===")
    print(memory.recall(""))
    memory.close()


def interactive(user_id: str, session_id: str) -> None:
    memory = Neo4jMemory(user_id=user_id)
    agent = build_agent(memory)
    print(f"user={user_id} session={session_id}  "
          f"({len(memory.load_history(session_id))} earlier messages loaded). Type 'quit' to exit.")
    while True:
        q = input("\n> ").strip()
        if q.lower() in ("exit", "quit"):
            break
        if q:
            print(chat_turn(agent, memory, session_id, q))
    memory.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chat", action="store_true", help="interactive chat instead of the scripted demo")
    parser.add_argument("--user", default="me")
    parser.add_argument("--session", default="default")
    args = parser.parse_args()
    interactive(args.user, args.session) if args.chat else demo()
