"""Shared setup for the three Claude Code interfaces (MCP, CLI, HTTP API) over the same Neo4j graph memory."""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT / "exercises"))

from neo4j_memory import Neo4jMemory  # noqa: E402


def open_memory() -> Neo4jMemory:
    """One user namespace per agent. Override with MEMORY_USER_ID."""
    return Neo4jMemory(user_id=os.getenv("MEMORY_USER_ID", "claude-code"))
