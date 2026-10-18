"""
Graph memory as an MCP server (stdio). Claude Code sees remember / recall / forget as native tools.

Register (project scope, writes .mcp.json):
    claude mcp add graph-memory -s project -- venv/bin/python claude_memory/mcp_server.py
"""
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from _common import open_memory

memory = open_memory()
server = MCPServer(
    name="graph-memory",
    instructions=(
        "Long-term memory stored as a knowledge graph in Neo4j. Store durable facts as "
        "subject -relation-> object triples, one per call; refer to the user as 'User'. "
        "Call recall('') to see everything before answering personal questions."
    ),
)


@server.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
def remember(
    subject: Annotated[str, "Entity the fact is about. Use 'User' for the person you are talking to."],
    relation: Annotated[str, "Short verb phrase, e.g. 'works at', 'lives in', 'is allergic to'."],
    object: Annotated[str, "The other entity or value."],
) -> str:
    """Store one durable fact in long-term graph memory."""
    return memory.remember(subject, relation, object)


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
def recall(
    query: Annotated[str, "Entity name to look up. Empty string returns all memories."] = "",
    hops: Annotated[int, "How many relationship hops to expand (1-3)."] = 2,
) -> str:
    """Look up facts in long-term graph memory, including connected facts."""
    return memory.recall(query, hops=hops)


@server.tool(annotations=ToolAnnotations(destructiveHint=True))
def forget(
    subject: Annotated[str, "Subject of the fact to delete."],
    relation: Annotated[str, "Relation exactly as shown by recall, e.g. LIVES_IN."],
    object: Annotated[str, "Object of the fact to delete."],
) -> str:
    """Delete a wrong or outdated fact. Store the correction with remember."""
    return memory.forget(subject, relation, object)


if __name__ == "__main__":
    server.run("stdio")
