"""
Challenge 11 (Exercise 12 / Challenge E & G): Haystack Enterprise Platform + Neo4j Aura Bundle.

Connects to the live Neo4j Aura cloud instance (`NEO4J_AURA_URI`, `NEO4J_AURA_USER`, `NEO4J_AURA_PASSWORD`),
verifies cloud access via the Neo4j CLI (`cypher-shell`) and `Neo4jDocumentStore`, and prepares the
Haystack Enterprise Platform (deepset AI Platform / Studio) deployment bundle:

1. Verifies live Neo4j Aura cloud connectivity via `cypher-shell` CLI and `Neo4jDocumentStore`
   (creates unique constraints and native HNSW vector index on Aura).
2. Serializes the GraphRAG Indexing & Query pipelines to YAML (`pipeline.dumps()`) and verifies
   lossless deserialization (`Pipeline.loads()`).
3. Audits the generated YAML to verify that no Neo4j Aura passwords or LLM API keys are serialized in
   plain text (all secrets use Haystack's `Secret.from_env_var(...)` specification).
4. Writes Studio-ready YAML files (`build/enterprise_graphrag_index.yaml`, `build/enterprise_graphrag_query.yaml`)
   and packages the custom Neo4j components into `build/neo4j_haystack_enterprise_components.zip`
   for `/api/v2/custom_components`.
"""
import argparse
import glob
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from haystack import Pipeline
from haystack.utils import Secret

from graphrag_pipelines import CHUNK_LABEL, DIM, build_indexing, build_query
from neo4j_document_store import Neo4jDocumentStore

HERE = Path(__file__).resolve().parent
BUILD_DIR = HERE.parent / "build"

COMPONENT_FILES = [
    "neo4j_document_store.py",
    "neo4j_embedding_retriever.py",
    "neo4j_graph.py",
    "llm.py",
]

PYPROJECT_TOML = """[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.build_meta"

[project]
name = "neo4j-haystack-enterprise-graphrag"
version = "0.1.0"
description = "Custom Neo4j DocumentStore, Vector Retriever, and Hybrid GraphRAG components for Haystack Enterprise Platform & Neo4j Aura"
requires-python = ">=3.10"
dependencies = [
    "haystack-ai>=2.0.0",
    "neo4j>=5.19.0",
    "sentence-transformers-haystack",
]
"""


def verify_secret_safety(yaml_text: str) -> None:
    """Ensure raw secret values from the environment never appear in the exported YAML."""
    for env_key in ("NEO4J_AURA_PASSWORD", "NEO4J_PASSWORD", "AZURE_OPENAI_API_KEY", "OPENAI_API_KEY"):
        val = os.getenv(env_key)
        if val and len(val) >= 4 and val in yaml_text:
            raise RuntimeError(f"Secret value for {env_key} leaked into serialized YAML!")


def run_aura_cli_check(uri: str, user: str, password_env: str) -> str:
    """Verify Neo4j Aura cloud connectivity using the cypher-shell CLI."""
    cypher_shell = shutil.which("cypher-shell") or "/opt/homebrew/bin/cypher-shell"
    if not os.path.exists(cypher_shell):
        return "cypher-shell not found on PATH (skipped CLI check)"

    env = dict(os.environ, JAVA_TOOL_OPTIONS="")
    for cand in glob.glob("/opt/homebrew/opt/openjdk*/libexec/openjdk.jdk/Contents/Home"):
        if os.path.exists(os.path.join(cand, "bin", "java")):
            env["JAVA_HOME"] = cand
            env["PATH"] = os.path.join(cand, "bin") + ":" + env.get("PATH", "")
            break

    r = subprocess.run(
        [
            cypher_shell,
            "-a",
            uri,
            "-u",
            user,
            "-p",
            os.environ[password_env],
            "CALL dbms.components() YIELD name, versions, edition RETURN name, versions[0] AS version, edition;",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"cypher-shell Aura check failed: {r.stderr.strip()}")
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    return " | ".join(lines[1:]) if len(lines) > 1 else r.stdout.strip()


def build_custom_component_zip(out_zip: Path) -> list[str]:
    """Package custom Neo4j components into a ZIP archive for Haystack Enterprise /api/v2/custom_components."""
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    members = []
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("pyproject.toml", PYPROJECT_TOML)
        members.append("pyproject.toml")
        init_lines = [
            '"""Custom Neo4j & GraphRAG components for Haystack Enterprise Platform."""',
            "from .neo4j_document_store import Neo4jDocumentStore",
            "from .neo4j_embedding_retriever import Neo4jEmbeddingRetriever",
            "from .neo4j_graph import LLMGraphExtractor, Neo4jGraphRetriever, Neo4jGraphWriter",
            "",
            '__all__ = [',
            '    "Neo4jDocumentStore",',
            '    "Neo4jEmbeddingRetriever",',
            '    "LLMGraphExtractor",',
            '    "Neo4jGraphWriter",',
            '    "Neo4jGraphRetriever",',
            ']',
        ]
        zf.writestr("neo4j_graphrag/__init__.py", "\n".join(init_lines) + "\n")
        members.append("neo4j_graphrag/__init__.py")
        for fname in COMPONENT_FILES:
            src = HERE / fname
            arcname = f"neo4j_graphrag/{fname}"
            zf.write(src, arcname)
            members.append(arcname)
    return members


def main() -> None:
    load_dotenv(HERE.parent / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--aura-uri",
        default=os.getenv("NEO4J_AURA_URI") or os.getenv("NEO4J_AURA_ENDPOINT"),
        help="Neo4j Aura URI (defaults to NEO4J_AURA_URI in .env).",
    )
    args = parser.parse_args()

    use_aura = bool(args.aura_uri and os.getenv("NEO4J_AURA_PASSWORD"))
    uri = args.aura_uri if use_aura else os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
    user = os.getenv("NEO4J_AURA_USER", "neo4j") if use_aura else os.getenv("NEO4J_USERNAME", "neo4j")
    pw_env = "NEO4J_AURA_PASSWORD" if use_aura else "NEO4J_PASSWORD"

    if use_aura:
        cli_info = run_aura_cli_check(uri, user, pw_env)
        print(f"0. Neo4j Aura CLI (cypher-shell) connected to {uri}: {cli_info}")

    store = Neo4jDocumentStore(
        uri=uri,
        username=user,
        password=Secret.from_env_var(pw_env),
        label=CHUNK_LABEL,
        embedding_dimension=DIM,
    )
    try:
        index_pipe = build_indexing(store)
        query_pipe = build_query(store, variant="graphrag", with_llm=True)

        index_yaml = index_pipe.dumps()
        query_yaml = query_pipe.dumps()

        verify_secret_safety(index_yaml)
        verify_secret_safety(query_yaml)

        # Round-trip deserialization check (Haystack 3.x requires explicit allowed_modules for custom components)
        allowed = ["neo4j_graph", "neo4j_embedding_retriever", "neo4j_document_store", "llm"]
        reloaded_index = Pipeline.loads(index_yaml, allowed_modules=allowed)
        reloaded_query = Pipeline.loads(query_yaml, allowed_modules=allowed)
        try:
            assert set(reloaded_index.graph.nodes) == set(index_pipe.graph.nodes)
            assert set(reloaded_query.graph.nodes) == set(query_pipe.graph.nodes)
        finally:
            reloaded_index.get_component("writer").document_store.close()
            reloaded_index.get_component("graph_writer").store.close()
            reloaded_query.get_component("retriever").document_store.close()
            reloaded_query.get_component("graph").store.close()

        BUILD_DIR.mkdir(parents=True, exist_ok=True)
        index_path = BUILD_DIR / "enterprise_graphrag_index.yaml"
        query_path = BUILD_DIR / "enterprise_graphrag_query.yaml"
        zip_path = BUILD_DIR / "neo4j_haystack_enterprise_components.zip"

        index_path.write_text(index_yaml)
        query_path.write_text(query_yaml)
        members = build_custom_component_zip(zip_path)

        print(f"1. Indexing pipeline YAML verified & written: {index_path.relative_to(HERE.parent)} ({len(index_yaml)} bytes)")
        print(f"   Components: {list(index_pipe.graph.nodes)}")
        print(f"2. Query pipeline YAML verified & written:    {query_path.relative_to(HERE.parent)} ({len(query_yaml)} bytes)")
        print(f"   Components: {list(query_pipe.graph.nodes)}")
        print(f"3. Secret audit: PASS ({pw_env} and API keys serialized as env_var references only)")
        print(f"4. Custom component package built:            {zip_path.relative_to(HERE.parent)} ({len(members)} files)")
    finally:
        store.close()


if __name__ == "__main__":
    main()
