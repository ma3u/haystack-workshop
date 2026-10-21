"""
Build a NeoDash 2.4 dashboard JSON from dashboard.cypher, for Aura Console -> Dashboards -> Import
(Aura migrates NeoDash reports to its own cards). Queries live only in dashboard.cypher; this file adds layout.

Run:  python build_dashboard.py   # writes results/aura_dashboard.json, then paste it into the Import dialog

After import, one manual step: NeoDash bar charts take one value column, so open the "Accuracy % by variant and
question type" card -> Settings -> Value and add `1-hop` next to `multi-hop`.
"""
import json
import re
import uuid
from pathlib import Path

HERE = Path(__file__).parent
CYPHER = HERE / "dashboard.cypher"
OUT = HERE / "results" / "aura_dashboard.json"

INTRO = """**Haystack x Neo4j workshop.** Same LLM, same prompt: vector RAG retrieves passages that *look like* the question,
GraphRAG adds 2-hop entity facts from the graph. Eval: 22 questions on a fictional company (8 single-hop, 14 multi-hop), 3 runs, LLM judge.
Built by `exercises/09_graphrag.py`, `10_eval_rag_vs_graphrag.py`, `eval_to_neo4j.py`."""

# (widget id in dashboard.cypher, NeoDash type, title, x, y, w, h, selection). x/w on a 12-column grid;
# Aura keeps the numbers but lays them out on 24 columns, so they are doubled on export (GRID_SCALE).
GRID_SCALE = 2
PAGES = {
    "RAG vs GraphRAG": [
        ("intro", "text", "About this dashboard", 0, 0, 12, 2, {}),
        ("D1", "value", "GraphRAG accuracy", 0, 2, 4, 2, {}),
        ("D2", "value", "Vector RAG accuracy", 4, 2, 4, 2, {}),
        ("D3", "value", "Multi-hop gain (GraphRAG - vector)", 8, 2, 4, 2, {}),
        ("D4", "bar", "Accuracy % by variant and question type", 0, 4, 8, 4, {"index": "variant", "value": "multi-hop", "key": "(none)"}),
        ("D5", "bar", "Cost: avg prompt context (tokens)", 8, 4, 4, 4, {"index": "variant", "value": "ctx_tokens", "key": "(none)"}),
        ("D6", "table", "Per-question accuracy % (3 runs)", 0, 8, 12, 5, {}),
    ],
    "Knowledge graph": [
        ("D7a", "value", "Chunks", 0, 0, 4, 2, {}),
        ("D7b", "value", "Entities", 4, 0, 4, 2, {}),
        ("D7c", "value", "Relations", 8, 0, 4, 2, {}),
        ("D8", "graph", "Multi-hop: Project Halcyon -> lead -> manager -> office", 0, 2, 6, 5, {}),
        ("D9", "bar", "Entity types extracted by the LLM", 6, 2, 6, 5, {"index": "type", "value": "entities", "key": "(none)"}),
        ("D11", "graph", "Entity graph", 0, 7, 8, 6, {}),
        ("D10", "table", "Most connected entities", 8, 7, 4, 6, {}),
        ("D12", "table", "Entities each eval question depends on", 0, 13, 12, 4, {}),
    ],
    "Agent memory": [
        ("D13", "graph", "What the agent remembers (current facts)", 0, 0, 6, 5, {}),
        ("D14", "table", "Fact history incl. corrections", 6, 0, 6, 5, {}),
        ("D15", "table", "Chat history per session", 0, 5, 12, 5, {}),
    ],
    "Document stores": [
        ("D16", "table", "Documents per store", 0, 0, 12, 3, {}),
        ("D17", "table", "Vector RAG chunks (exercise 08)", 0, 3, 12, 5, {}),
    ],
}


def load_queries() -> dict[str, str]:
    """Map widget id -> query from blocks headed `// <id> (<type>): <title>`."""
    queries, current, lines = {}, None, []
    for line in CYPHER.read_text().splitlines():
        m = re.match(r"//\s*(D\w+)\s*\(", line)
        if m:
            current, lines = m.group(1), []
        elif current and not line.startswith("//"):
            lines.append(line)
            if line.rstrip().endswith(";"):
                queries[current] = "\n".join(lines).strip().rstrip(";")
                current = None
    return queries


def main() -> None:
    queries = load_queries()
    pages = []
    for title, widgets in PAGES.items():
        reports = []
        for wid, rtype, wtitle, x, y, w, h, selection in widgets:
            reports.append({
                "id": f"{title.lower().replace(' ', '-')}-{wid.lower()}",
                "title": wtitle,
                "query": INTRO if wid == "intro" else queries[wid],
                "x": x * GRID_SCALE, "y": y, "width": w * GRID_SCALE, "height": h,
                "type": rtype,
                "selection": selection,
                "settings": {},
            })
        pages.append({"title": title, "reports": reports})
    dashboard = {
        "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL, "haystack-workshop/aura-dashboard")),  # stable across rebuilds
        "title": "Haystack GraphRAG on Neo4j",
        "version": "2.4",
        "settings": {"pagenumber": 0, "editable": True, "fullscreenEnabled": True, "parameters": {}},
        "pages": pages,
    }
    OUT.write_text(json.dumps(dashboard, indent=2) + "\n")
    print(f"Wrote {OUT} ({len(pages)} pages, {sum(len(p['reports']) for p in pages)} cards)")


if __name__ == "__main__":
    main()
