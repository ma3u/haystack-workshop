# Hackathon Blueprint: Sovereign Multi-IDE Engineering Memory on Haystack Enterprise Platform + Neo4j Aura

**Event**: [Hack Night: Haystack Enterprise Platform (Berlin, Oct 21, 2026 — House of Sovereignty)](https://hackathonhub.eu/events/hack-night-haystack-enterprise-platform-berlin-2026)  
**Challenge Script**: [`exercises/13_enterprise_aura_multi_ide.py`](13_enterprise_aura_multi_ide.py) & [`exercises/12_enterprise_aura_bundle.py`](12_enterprise_aura_bundle.py)

---

## 1. The Challenge & "House of Sovereignty" Pitch

Modern engineering teams use multiple AI coding IDEs simultaneously—**Claude Code**, **Google Antigravity**, and **GitHub Copilot**. Today, this creates three major enterprise problems:
1. **Fragmented Silos**: Architectural decisions, benchmarks, and bug fixes discovered in Claude Code are invisible to Google Antigravity, GitHub Copilot, and non-IDE stakeholders.
2. **Data Sovereignty & Secret Risk**: Raw IDE session logs contain `.env` secrets, terminal outputs, and proprietary code that **must never leave the developer's machine unredacted**.
3. **Opaque, Unauditable Decision Processes**: When multiple team members make engineering decisions assisted by AI agents, standard Vector RAG cannot explain **who** made a decision, **which tools** were executed to verify it, or **which datasets and benchmark artifacts** justified the outcome.

### Our Solution: Sovereign, Auditable Two-Tier Graph Memory + Haystack Enterprise GraphRAG
We separate **Local Raw Telemetry** from **Cloud Auditable Team Intelligence**:
- **Tier 1 — Local Sovereign Memory (`bolt://127.0.0.1:7689`)**:
  - **Claude Code** (native MKG plugin + `recall_all.py` hook), **Google Antigravity** (13-tool MKG MCP server), and **GitHub Copilot** (`mkg_agent_hook.py` adapter + MCP) log sessions to a local Neo4j instance.
  - `scrub_secrets.py --hook` redacts any credentials or API tokens on `SessionEnd`.
- **Tier 2 — Cloud Knowledge & Decision Provenance Graph on Neo4j Aura (`neo4j+s://ma3u-g993-fhgd.endpoints.neo4j.io`)**:
  - Only **approved, secret-free architectural decisions, learnings, and provenance triples** are indexed into **Neo4j Aura Enterprise (`5.27-aura`)** with both **384-dim HNSW vectors** (`:GraphRagChunk`) and **explicit decision-audit relationships** (`(:GraphRagEntity)-[:REL]->(:GraphRagEntity)` linking **Team Members → IDEs → Decisions → Tools Used → Data Artifacts**).
- **Tier 3 — Explainable Serving & Evaluation on Haystack Enterprise Platform (deepset Studio)**:
  - **Haystack Enterprise Platform** connects to **Neo4j Aura** using our packaged custom components (`build/neo4j_haystack_enterprise_components.zip`) and YAML pipelines (`enterprise_graphrag_index.yaml`, `enterprise_graphrag_query.yaml`).
  - Engineers, auditors, tech leads, and agents query the collective memory via Haystack Enterprise's **Playground UI**, **REST API**, and **Evaluation Dashboard** with **100% multi-hop accuracy** (vs. 62% for standard Vector RAG) and full **decision explainability**.

---

## 2. End-to-End Architecture & Execution Order

```mermaid
flowchart TB
    H1["1. Developer Workstation — 3 AI Coding IDEs"]
    AG(["Google Antigravity\nMCP Server"])
    CC(["Claude Code\nPlugin + Hooks"])
    GC(["GitHub Copilot\nCLI + MCP"])
    H1 --> AG & CC & GC

    H2["2. Sovereign Local Graph — Neo4j :7689"]
    RAW[("Raw Prompts & Turns")]
    GATE{{"Secret Scrubbing & Quality Gate"}}
    AG --> H2
    CC -->|"Step 1: Prompts & Hooks"| H2
    GC --> H2
    H2 --> RAW --> GATE

    H3["3. Haystack Enterprise — Phase A: GraphRAG Indexing"]
    ZIP["Custom Component ZIP"]
    IDX["Indexing Pipeline (YAML)\nEmbeds Prompts + Extracts Entities"]
    GATE -->|"Step 2: Sanitized Prompts"| H3
    H3 --> ZIP --> IDX

    H4["4. Cloud GraphRAG Store — Neo4j Aura 5.27-aura"]
    VEC[("HNSW Vector Index\n(:GraphRagChunk)")]
    ENT[("Entity-Relation Graph\n(:GraphRagEntity)")]
    IDX -->|"Step 3: Write Vectors & Triples"| H4
    H4 --> VEC & ENT

    H5["5. Haystack Enterprise — Phase B: Hybrid GraphRAG Query"]
    QRY["Query Pipeline (YAML)\nVector Seeds + Ranker + 3-Hop Cypher + LLM"]
    VEC --> H5
    ENT -->|"Step 4: Vectors + 3-Hop Cypher"| H5
    H5 --> QRY

    H6["6. deepset Studio — Serving, Playground & Evaluation"]
    UI{{"Playground, REST API & A/B Evaluation (100% vs 62%)"}}
    QRY <-->|"Step 5: Question & Grounded Answer"| H6
    H6 --> UI

    style H1 fill:#3730a3,stroke:#818cf8,color:#ffffff,stroke-width:3px
    style AG fill:#6366f1,stroke:#4338ca,color:#ffffff,stroke-width:2px
    style CC fill:#6366f1,stroke:#4338ca,color:#ffffff,stroke-width:2px
    style GC fill:#6366f1,stroke:#4338ca,color:#ffffff,stroke-width:2px

    style H2 fill:#b45309,stroke:#fbbf24,color:#ffffff,stroke-width:3px
    style RAW fill:#f59e0b,stroke:#b45309,color:#ffffff,stroke-width:2px
    style GATE fill:#f59e0b,stroke:#b45309,color:#ffffff,stroke-width:2px

    style H3 fill:#065f46,stroke:#34d399,color:#ffffff,stroke-width:3px
    style ZIP fill:#10b981,stroke:#047857,color:#ffffff,stroke-width:2px
    style IDX fill:#10b981,stroke:#047857,color:#ffffff,stroke-width:2px

    style H4 fill:#075985,stroke:#38bdf8,color:#ffffff,stroke-width:3px
    style VEC fill:#0ea5e9,stroke:#0369a1,color:#ffffff,stroke-width:2px
    style ENT fill:#0ea5e9,stroke:#0369a1,color:#ffffff,stroke-width:2px

    style H5 fill:#065f46,stroke:#34d399,color:#ffffff,stroke-width:3px
    style QRY fill:#10b981,stroke:#047857,color:#ffffff,stroke-width:2px

    style H6 fill:#9f1239,stroke:#fb7185,color:#ffffff,stroke-width:3px
    style UI fill:#f43f5e,stroke:#be123c,color:#ffffff,stroke-width:2px
```

### Detailed Diagram Explanation: Component Groups & Execution Order

#### A. Architectural Component Groups
| Architectural Tier | Components Included | Role in the System |
|---|---|---|
| **1. Developer Workstation (3 AI Coding IDEs)** | `Claude Code`, `Google Antigravity`, `GitHub Copilot CLI` | Where developers write prompts, debug code, and generate architectural insights. |
| **2. Sovereign Local Graph (`Neo4j :7689`)** | `Raw Prompts & SessionEvents`, `scrub_secrets.py`, `Sovereignty & Quality Gate` | Captures 100% of raw user prompts and agent turns locally; strips secrets/keys before anything leaves the workstation. |
| **3. Haystack Enterprise Platform (Pipelines)** | `Custom Component ZIP`, `Phase A: Indexing Pipeline`, `Phase B: Hybrid Query Pipeline` | Orchestrates **both** GraphRAG ingestion (embedding prompts + extracting entity triples) and hybrid multi-hop retrieval. |
| **4. Cloud GraphRAG Store (`Neo4j Aura 5.27-aura`)** | `HNSW Vector Index (:GraphRagChunk)`, `Entity-Relation Graph (:GraphRagEntity)` | Cloud knowledge graph storing all sanitized prompts, learning passages, 384-dim embeddings, and `[:REL]` triples. |
| **5. deepset Studio & Evaluation** | `Studio Playground`, `REST API`, `A/B Evaluation Runs` | Interactive UI, production API endpoint, and side-by-side evaluation comparing Vector RAG vs. Hybrid GraphRAG. |

---

#### B. Step-by-Step Execution Order (And Exactly When Haystack Enterprise Comes Into Place)

**Haystack Enterprise Platform** is not a passive viewer—it sits directly in the middle of the cloud workflow and executes at **two distinct lifecycle phases** (**Phase A: Ingestion/Indexing** in Steps 2–3, and **Phase B: Hybrid Querying & Evaluation** in Steps 4–6):

1. **Step 1 — Capture All Prompts & Turns Locally (Tier 1 → Tier 2)**:
   - Every time a developer submits a prompt in **Claude Code**, **Google Antigravity**, or **GitHub Copilot CLI**, lifecycle hooks (`log_event.py`, `mkg_agent_hook.py`) and MCP tools (`project_add_learning`) record the prompt, tool calls, and discovered learnings into the local sovereign Neo4j instance (`bolt://127.0.0.1:7689`).
2. **Step 2 — Redact Secrets & Feed into Haystack Enterprise Indexing (Tier 2 → Tier 3)**:
   - On `SessionEnd`, `scrub_secrets.py` scrubs any API keys, `.env` credentials, or tokens from the stored prompts and turns.
   - **Haystack Enterprise Comes Into Place (Phase A — Ingestion)**: The sanitized prompts and approved cross-IDE learnings are sent into the **Haystack Enterprise Indexing Pipeline** (`build/enterprise_graphrag_index.yaml`), which loads our custom Neo4j components from `build/neo4j_haystack_enterprise_components.zip`.
3. **Step 3 — Index Prompts & Triples into Neo4j Aura GraphRAG (Tier 3 → Tier 4)**:
   - Inside Haystack Enterprise, the Indexing Pipeline runs two branches over every prompt and learning:
     1. **Vector Branch**: `SentenceTransformersDocumentEmbedder` embeds the prompt/learning text and `DocumentWriter` writes `(:GraphRagChunk)` nodes into **Neo4j Aura's HNSW Vector Index**.
     2. **Graph Branch**: `LLMGraphExtractor` extracts structured `(Subject)-[RELATION]->(Object)` triples from the prompt/learning text and `Neo4jGraphWriter` merges `(:GraphRagEntity)` nodes and `[:MENTIONS]` / `[:REL]` edges into **Neo4j Aura (`5.27-aura`)**.
4. **Step 4 — Trigger a Cross-IDE Question in deepset Studio or REST API (Tier 5 → Tier 3)**:
   - **Haystack Enterprise Comes Into Place (Phase B — Query & Evaluation)**: A developer, tech lead, or AI agent submits a multi-hop question via **deepset Studio Playground** or the **Haystack Enterprise REST API** into the **Hybrid GraphRAG Query Pipeline** (`build/enterprise_graphrag_query.yaml`).
5. **Step 5 — Execute Hybrid Vector + 3-Hop Cypher Traversal on Aura (Tier 3 ↔ Tier 4)**:
   - The Haystack Enterprise Query Pipeline executes:
     - `Neo4jEmbeddingRetriever`: Finds top-10 semantically similar prompt/learning chunks in Aura's HNSW index.
     - `SentenceTransformersSimilarityRanker`: Reranks to the top-4 chunks.
     - `Neo4jGraphRetriever`: Starts from the seed chunks and linked entities, traversing up to **3 hops (`[:REL*1..3]`)** in Neo4j Aura (while pruning high-degree hub nodes) to collect connected facts across all stored prompts and IDEs.
6. **Step 6 — Return Grounded Answer & Log A/B Evaluation Metrics (Tier 3 → Tier 5)**:
   - `ChatPromptBuilder` combines the reranked prompt passages + multi-hop graph facts and sends them to the `LLM` (`AzureOpenAI` / `Gemini`), returning a grounded answer with full provenance to **deepset Studio** and logging evaluation scores (`100%` multi-hop recall vs. `62%` vector-only).

---

#### C. Runtime Sequence Diagram (Prompt Capture → GraphRAG Indexing → Multi-Hop Query)

```mermaid
sequenceDiagram
    autonumber
    participant IDE as 1. AI Coding IDEs (Claude / Antigravity / Copilot)
    participant Local as 2. Local Neo4j :7689 (scrub_secrets.py + Gate)
    participant HEP_Idx as 3a. Haystack Enterprise Indexing Pipeline (YAML)
    participant Aura as 4. Neo4j Aura Cloud GraphRAG (Vectors + Entities)
    participant HEP_Qry as 3b. Haystack Enterprise Query Pipeline (YAML)
    participant Studio as 5. deepset Studio (Playground / REST API / Eval)

    Note over IDE,Local: Phase 1 - Sovereign Prompt Capture & Redaction
    IDE->>Local: Step 1: Log all User Prompts, Turns & Learnings (MCP + Hooks)
    Local->>Local: Run scrub_secrets.py & Sovereignty Gate (strip .env secrets)

    Note over Local,Aura: Phase 2 - Haystack Enterprise Comes Into Place (GraphRAG Indexing)
    Local->>HEP_Idx: Step 2: Push sanitized Prompts & Learnings
    HEP_Idx->>HEP_Idx: Embed passages (384-dim) + LLMGraphExtractor (Entity Triples)
    HEP_Idx->>Aura: Step 3: Write (:GraphRagChunk) vectors + (:GraphRagEntity)-[:REL]->()

    Note over Aura,Studio: Phase 3 - Haystack Enterprise Comes Into Place (Hybrid GraphRAG Query & Eval)
    Studio->>HEP_Qry: Step 4: Ask multi-hop question across stored prompts & IDEs
    HEP_Qry->>Aura: Step 5a: HNSW Vector Search (top-10 seed chunks)
    HEP_Qry->>Aura: Step 5b: Neo4jGraphRetriever Cypher traversal (<=3 hops, hub-pruned)
    Aura-->>HEP_Qry: Return seed passages + connected multi-hop entity facts
    HEP_Qry->>Studio: Step 6: Synthesize grounded LLM answer + log A/B Eval (100% recall)
```

---

## 3. Auditable & Explainable Decision Process (Multi-Team Provenance)

To make AI-assisted engineering decisions **auditable and explainable** across multiple team members, every decision indexed by **Haystack Enterprise Platform** into **Neo4j Aura** links the **Team Member**, **AI Coding IDE**, **Architectural Decision**, **Verification Tools**, and **Ground-Truth Data Artifacts**:

```mermaid
flowchart TB
    P1["1. Engineering Team Members & AI Coding IDEs"]
    ALICE(["Alice Chen — Staff AI Architect\nClaude Code (session e1a5a822)"])
    BOB(["Bob Martinez — Graph Platform Eng\nGoogle Antigravity (session f5aed901)"])
    CLARA(["Clara Weber — SecOps & Sovereignty Lead\nGitHub Copilot CLI (session 89f97e2e)"])
    P1 --> ALICE & BOB & CLARA

    P2["2. Architectural Decisions Recorded in GraphRAG"]
    D1{{"DEC-01: Standardize on MCP +\nrecall_all.py Hook (cosine >= 0.75)"}}
    D2{{"DEC-02: Adopt Hybrid GraphRAG (<=3 hops)\non Neo4j Aura + Haystack Enterprise"}}
    D3{{"DEC-03: Enforce Sovereign Split-Graph\nSecret Redaction on Local Neo4j :7689"}}
    ALICE -->|"MADE_DECISION"| D1
    BOB -->|"MADE_DECISION"| D2
    CLARA -->|"MADE_DECISION"| D3

    P3["3. Verification Tools Executed by Team Members"]
    T1["bench.py +\nrecall_all.py"]
    T2["10_eval_rag_vs_graphrag.py +\n12_enterprise_aura_bundle.py"]
    T3["scrub_secrets.py +\nmkg_agent_hook.py"]
    D1 -->|"VERIFIED_WITH_TOOL"| T1
    D2 -->|"VERIFIED_WITH_TOOL"| T2
    D3 -->|"VERIFIED_WITH_TOOL"| T3

    P4["4. Ground-Truth Data & Benchmark Artifacts Used"]
    DATA1[("claude_memory/eval/results.json\n100% Pass, 23.7K Tokens ($0.024)")]
    DATA2[("build/eval_rag_vs_graphrag.json\n100% GraphRAG vs 62% Vector RAG")]
    DATA3[("Local Neo4j :7689 SessionEvent Trace\n0 Leaked Secrets to Cloud")]
    T1 -->|"GROUNDED_IN_DATA"| DATA1
    T2 -->|"GROUNDED_IN_DATA"| DATA2
    T3 -->|"GROUNDED_IN_DATA"| DATA3

    P5["5. Explainable Audit Query in Haystack Enterprise (deepset Studio)"]
    AUDIT{{"Neo4jGraphRetriever + LLM Audit Report\nFull Provenance: Who + Which IDE + Which Tool + Which Data"}}
    DATA1 & DATA2 & DATA3 --> P5 --> AUDIT

    style P1 fill:#3730a3,stroke:#818cf8,color:#ffffff,stroke-width:3px
    style ALICE fill:#6366f1,stroke:#4338ca,color:#ffffff,stroke-width:2px
    style BOB fill:#6366f1,stroke:#4338ca,color:#ffffff,stroke-width:2px
    style CLARA fill:#6366f1,stroke:#4338ca,color:#ffffff,stroke-width:2px

    style P2 fill:#b45309,stroke:#fbbf24,color:#ffffff,stroke-width:3px
    style D1 fill:#f59e0b,stroke:#b45309,color:#ffffff,stroke-width:2px
    style D2 fill:#f59e0b,stroke:#b45309,color:#ffffff,stroke-width:2px
    style D3 fill:#f59e0b,stroke:#b45309,color:#ffffff,stroke-width:2px

    style P3 fill:#065f46,stroke:#34d399,color:#ffffff,stroke-width:3px
    style T1 fill:#10b981,stroke:#047857,color:#ffffff,stroke-width:2px
    style T2 fill:#10b981,stroke:#047857,color:#ffffff,stroke-width:2px
    style T3 fill:#10b981,stroke:#047857,color:#ffffff,stroke-width:2px

    style P4 fill:#075985,stroke:#38bdf8,color:#ffffff,stroke-width:3px
    style DATA1 fill:#0ea5e9,stroke:#0369a1,color:#ffffff,stroke-width:2px
    style DATA2 fill:#0ea5e9,stroke:#0369a1,color:#ffffff,stroke-width:2px
    style DATA3 fill:#0ea5e9,stroke:#0369a1,color:#ffffff,stroke-width:2px

    style P5 fill:#9f1239,stroke:#fb7185,color:#ffffff,stroke-width:3px
    style AUDIT fill:#f43f5e,stroke:#be123c,color:#ffffff,stroke-width:2px
```

### Multi-Member Decision Provenance Matrix

| Decision ID | Team Member & Role | AI Coding IDE & Session | Tools Used in Decision Process | Data & Artifacts Used | Auditable & Explainable Result |
|---|---|---|---|---|---|
| **`DEC-01`** | **Alice Chen** *(Staff AI Architect)* | **Claude Code** (`session: e1a5a822`) | `bench.py`, `meta-knowledge-graph MCP`, `recall_all.py` | `claude_memory/eval/results.json`, `learning:c49e4e94ebbb2329` | Selected MCP + `recall_all.py` hook (`cosine >= 0.75`): **100% pass rate (3/3)** at **23.7K tokens (`\$0.024`)**, beating CLI (`47K` tokens) and HTTP (`39K` tokens). |
| **`DEC-02`** | **Bob Martinez** *(Graph Platform Engineer)* | **Google Antigravity** (`session: f5aed901`) | `10_eval_rag_vs_graphrag.py`, `12_enterprise_aura_bundle.py`, `Neo4j Aura CLI` | `build/eval_rag_vs_graphrag.json`, `build/neo4j_haystack_enterprise_components.zip` | Selected Hybrid GraphRAG (`<=3 hops`, hub-pruned) on **Neo4j Aura (`5.27-aura`)**: **100% multi-hop recall** vs. **62% Vector RAG**. |
| **`DEC-03`** | **Clara Weber** *(SecOps & Sovereignty Lead)* | **GitHub Copilot CLI** (`session: 89f97e2e`) | `mkg_agent_hook.py`, `scrub_secrets.py`, `cypher/05_scrub_verify.cypher` | `Local Neo4j :7689 SessionEvent Trace`, `.env.example` | Enforced Sovereign Split-Graph redaction: raw prompts stay on `:7689`, secrets are scrubbed on `SessionEnd`, and only approved learnings reach **Neo4j Aura**. |

---

## 4. Live Demo Runbook for Tomorrow's Hackathon

### Step 1: Verify Neo4j Aura Cloud Bundle & Custom Component ZIP
```bash
uv run --with-requirements requirements.txt python exercises/12_enterprise_aura_bundle.py
```
- Connects to `neo4j+s://ma3u-g993-fhgd.endpoints.neo4j.io` (`Neo4j Kernel 5.27-aura enterprise`)
- Verifies YAML serialization round-trip and secret safety (`NEO4J_AURA_PASSWORD` referenced strictly via `env_var`)
- Outputs:
  - `build/enterprise_graphrag_index.yaml`
  - `build/enterprise_graphrag_query.yaml`
  - `build/neo4j_haystack_enterprise_components.zip`

### Step 2: Sync 3-IDE Collective Memory & Decision Provenance to Neo4j Aura
```bash
uv run --with-requirements requirements.txt python exercises/13_enterprise_aura_multi_ide.py
```
- Publishes curated learnings, team member decisions (`DEC-01`, `DEC-02`, `DEC-03`), tools used, and data artifacts from **Claude Code**, **Google Antigravity**, and **GitHub Copilot** into **Neo4j Aura**.
- Executes multi-hop Hybrid GraphRAG & Decision Audit queries live against Neo4j Aura:
  - **HQ1 (Cross-IDE Memory)**: *"Which codeword was stored by Google Antigravity, and which other two AI coding IDEs recalled or received it via the MCP memory graph?"*
  - **HQ2 (Sovereignty & Redaction)**: *"How does GitHub Copilot connect to the Neo4j memory system, and how are raw session secrets kept out of Neo4j Aura?"*
  - **HQ3 (Benchmark Justification)**: *"Why does Haystack Enterprise Platform use Hybrid GraphRAG on Neo4j Aura instead of plain Vector RAG, and what was the benchmark accuracy difference?"*
  - **HQ4 (Multi-Team Decision Audit & Explainability)**: *"Audit decisions DEC-01, DEC-02, and DEC-03 across the team: which team member made each decision, which IDE and tools did they use, and which data artifacts justified their decision?"*
- Prints the **Auditable & Explainable Team Decision Trail** and exports `build/enterprise_multi_ide_eval.json` for Haystack Enterprise Studio's Evaluation runner.

### Step 3: Upload to Haystack Enterprise Platform at the Event
Once you receive your workspace credentials at the Berlin Hack Night:
1. In **Haystack Enterprise Studio → Settings → Secrets**, add `NEO4J_AURA_PASSWORD`.
2. Upload `build/neo4j_haystack_enterprise_components.zip` via the UI or CLI/API (`--deploy` flag in `13_enterprise_aura_multi_ide.py`).
3. Import `build/enterprise_graphrag_index.yaml` and `build/enterprise_graphrag_query.yaml` into Studio and open the **Playground** to demo multi-hop queries and **auditable decision provenance** across all 3 AI coding IDEs live!
