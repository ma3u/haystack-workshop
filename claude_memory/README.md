# Graph memory for Claude Code: MCP vs CLI vs HTTP API

The same `Neo4jMemory` (`exercises/neo4j_memory.py`) exposed three ways:

| File | Interface | How Claude Code uses it |
|------|-----------|-------------------------|
| `mcp_server.py` | MCP server (stdio) | Native tools `mcp__graph-memory__remember / recall / forget` |
| `cli.py` | CLI | Bash tool: `venv/bin/python claude_memory/cli.py recall` |
| `api.py` | HTTP API on 127.0.0.1:8765 | Bash tool: `curl` |

## Setup

```bash
# MCP (recommended), project scope, writes .mcp.json
claude mcp add graph-memory -s project -- venv/bin/python claude_memory/mcp_server.py

# CLI: nothing to install
venv/bin/python claude_memory/cli.py --help

# API: start the server
venv/bin/python claude_memory/api.py
```

`MEMORY_USER_ID` selects the memory namespace (default `claude-code`).

## Benchmark

`bench.py` runs headless Claude Code (`claude -p`, Sonnet) through each interface. One rep is two separate
processes: store 5 facts, then a fresh session recalls them, answers, and replaces Berlin with Lisbon.
Both the answer and the graph in Neo4j are checked. 3 reps per interface, 2026-10-17.

| Interface | Permissions | Pass | Turns | Input tokens | Cost / rep | Wall / rep |
|-----------|-------------|------|-------|--------------|------------|------------|
| **MCP** | `mcp__graph-memory` only | **3/3** | 11 | **~23.7K** | **$0.024** | 23 s |
| CLI | `Bash(venv/bin/python claude_memory/cli.py:*)` | 1/3 | 8 | – | $0.031 | 39 s |
| API | `Bash(curl:*)` | 0/3 | 7 | – | $0.030 | 25 s |
| CLI | all of `Bash` | 3/3 | 8-9 | ~47K | $0.027 | 45 s |
| API | all of `Bash` | 3/3 | 7 | ~39K | $0.025 | 21 s |

Raw data: `results/bench-*.json`.

### Findings

1. **Narrow Bash permissions break CLI and API.** To save turns, Claude batches the writes into one shell
   `for` loop or `&&` chain. A compound command does not match `Bash(curl:*)`, so it is denied.
   MCP tool calls cannot be batched into shell, so a narrow allowlist works.
2. **MCP uses about half the input tokens** (24K vs 39-47K). The tools describe themselves, so there is no
   `--help` / `GET /` discovery step and no Bash tool prompt. It takes more turns (one call per fact), but
   each turn is small.
3. **CLI is the slowest** (about 45 s): every call starts Python, imports Haystack and opens a Neo4j connection.
   The MCP and API servers keep one process and one connection open.
4. **Permissions per tool only exist with MCP**: auto-allow `recall` (read-only), and ask before `forget`
   (marked destructive). With CLI or API the choice is "this Bash prefix" or all of Bash.

## Live test in a Claude Code session (2026-10-18)

A running Claude Code session cannot load a newly registered MCP server; it only takes effect after a restart.
So the live test used the CLI from inside the session, which calls the same `Neo4jMemory` code as the MCP tools.
User namespace: `claude-code` (the default). Facts came from the real setup session; no secrets were stored.

```bash
m() { venv/bin/python claude_memory/cli.py "$@"; }   # zsh: a function, because "$M args" does not word-split
```

| Step | Command | Result |
|------|---------|--------|
| Empty start | `m recall` | `Memory is empty.` |
| Store 8 facts | `m remember "User" "prepares for" "deepset Haystack hackathon"`, ... | Linked graph: User → hackathon → haystack-workshop → haystack-ai 3.4.0 / Neo4j / LLM provider / MCP |
| 2-hop recall | `m recall User` | User → hackathon → workspace |
| 3-hop recall | `m recall User --hops 3` | Adds everything the workshop uses |
| Correction | `m forget haystack-workshop LLM_PROVIDER oai-mvhd-5f53b7` then `m remember haystack-workshop "LLM provider" oai-haystack-f791ed` | Old provider edge removed, new resource stored with its `gpt-5-mini` deployment and `rg-haystack-workshop` |
| Partial name match | `m recall oai --hops 1` | Finds both Azure resources |
| No match | `m recall kubernetes` | `No memories found for 'kubernetes'.` |

The correction replayed a real change from the setup session: the workshop first used the EHDS Azure OpenAI
resource, then moved to its own resource.

### Known gap (fixed 2026-10-18)

`forget` used to delete only the relationship. The entity it pointed to stayed, with its other facts: after the
correction, `oai-mvhd-5f53b7 -BELONGS_TO-> EHDS project` was no longer reachable from `recall User`, but it still
appeared in a name search (`recall oai`).

Now `forget` retires facts instead of deleting them:

- The fact gets a `valid_to` timestamp. `recall` returns live facts only; `recall --history` (CLI) or
  `recall(..., history=True)` also returns retired ones, marked `(forgotten <time>)`.
- If the forgotten fact was the only link between two groups of entities, the facts inside the smaller group are
  retired too, so they no longer show up in a name search. A group that contains `User` is never retired.
- `remember` on a retired fact makes it live again.

Tests: `exercises/test_neo4j_memory.py` (11 pass), including the `oai-old -BELONGS_TO-> EHDS project` case.
Facts deleted before this change (the `oai-mvhd-5f53b7` edge in the `claude-code` namespace) are not touched.

### Use it in the next session

The facts stay in Neo4j under `claude-code`. Register the MCP server, restart Claude Code, and the
`mcp__graph-memory__*` tools can read them:

```bash
claude mcp add graph-memory -s project -- venv/bin/python claude_memory/mcp_server.py
```

To start over: `venv/bin/python -c "import sys; sys.path.insert(0, 'claude_memory'); from _common import open_memory; open_memory().reset()"`

## Recommendation

- **Claude Code / agents: MCP.** Most reliable under strict permissions, cheapest, and the same server works in
  Claude Desktop and other MCP clients (Haystack Agents can use MCP tools too).
- **Humans, scripts, debugging: CLI.**
- **Other services or remote clients: HTTP API**, or run the MCP server over `streamable-http`.
  Add auth before binding to anything other than localhost.

---

# Agent memory: Claude Code history in the meta-knowledge-graph

Second approach, next to the `Neo4jMemory` tools above: the Neo4j Labs plugin
[meta-knowledge-graph](https://github.com/neo4j-labs/meta-knowledge-graph) (MKG) logs every Claude Code event
into Neo4j through hooks, and `import_claude_history.py` loads the history that existed before the plugin.
Imported sessions get the same graph shape as live ones, because the importer calls MKG's own hook code.

## Status (2026-10-18)

| Step | Status |
|------|--------|
| Neo4j Desktop instance `GraphRAG-agent-memory` (2026.09.0 Enterprise), ports moved to bolt 7689 / http 7475 in its `neo4j.conf` | Done; restarted 2026-10-18 with its own `bin/neo4j restart`, now listens on **7689** (http 7475) |
| Plugin `meta-knowledge-graph@mkg` 0.1.43 installed, user scope (all sessions) | Done |
| `MKG_ENV_FILE=~/.config/meta-knowledge-graph/.env` in `~/.claude/settings.json` | Done |
| `NEO4J_URI` and `NEO4J_MEMORY_URI` = `bolt://127.0.0.1:7689` in the workshop `.env` | Done |
| Secret scan of `~/.claude/projects` (gitleaks + every secret value from `~/projects/**/.env*`) | Done |
| Sanitized, filtered copy in `~/.mkg-import/clean/`, re-scanned: gitleaks 0, known `.env` values 0, detect-secrets 0 real hits | Done |
| Load the copy into Neo4j | Done 2026-10-18: 2,963 events, 19 transcripts (loaded on 7687 before the restart; same instance, now 7689) |
| Enrich (tool-call graph) | Done 2026-10-18 |
| MKG credentials file `~/.config/meta-knowledge-graph/.env` (Neo4j 7689, Azure OpenAI) | Done 2026-10-18; hooks now log live sessions |
| Run the 10 questions, screenshots | Done 2026-10-18, queries fixed after the first run (see "Twelve questions") |
| Azure deployment `text-embedding-3-small` (DataZoneStandard, 100K TPM) on `oai-haystack-f791ed` | Done 2026-10-18 |
| LLM extraction of learnings / observations (Azure OpenAI `gpt-5-mini`) | Done 2026-10-18: all 19 sessions, see step 6 |
| Embeddings for the learnings / observations written before the embedding deployment existed | Done 2026-10-18 with `backfill_embeddings.py` (8 learnings, 24 observations) |
| Questions on learnings and the episodic timeline (Q11, Q12) | Done 2026-10-18 |
| Rotate the AI Studio key and the Google keys found in the TwoBreath history | Done 2026-10-18 (by you) |

## Steps, in order

All steps are done (2026-10-18). Nothing in this list is open.

1. **Rotate the leaked keys.** Done. The AI Studio key is in this session's transcript. The 9 Google API keys found in the
   TwoBreath sessions are in plain text in `~/.claude/projects`. They are redacted only in the staged copy.
   Rotate them in AI Studio / Google Cloud Console, then update `AISTUDIO_API_KEY` in `.env`. After this session
   ends, `python3 ~/redact_key.py --apply` removes the old AI Studio key from the local transcripts.

2. **Port.** Done: `GraphRAG-agent-memory` serves on **7689**, so `DeepSet` can run on 7687 again.
   Check: `lsof -nP -iTCP -sTCP:LISTEN | grep java`. Restart without the Desktop UI (its Java path has a space):

   ```bash
   D="$HOME/Library/Application Support/neo4j-desktop/Application/Data/dbmss/dbms-329920e9-dda8-4f8f-9a55-10bbb1386859"
   JAVA_HOME="$(ls -d "$HOME/Library/Application Support/neo4j-desktop/Application/Cache/runtime/"*/ | head -1)" "$D/bin/neo4j" restart
   ```

   The workshop `.env` now points `NEO4J_URI` to 7689 as well, so the exercises use this instance too.

3. **Load and enrich.** Done (on 7687 before the restart; it is the same instance). The importer refuses to write when the target database contains workshop data
   (`GraphRagChunk`, `GraphRagEntity`, `HaystackRagDocument`, `HaystackDocument`), so it cannot hit `DeepSet` by mistake.

   ```bash
   cd ~/projects/haystack-workshop
   RUN="uv run --no-project --with neo4j python claude_memory/import_claude_history.py"
   $RUN sanitize                                   # optional: re-stage and re-scan with the latest history
   $RUN load   --uri bolt://127.0.0.1:7689         # 2,963 events (9 sessions, 10 subagents)
   $RUN enrich --uri bolt://127.0.0.1:7689         # Turn / ToolCall / ToolResult / Resource nodes, no LLM
   ```

   Re-running `load` does not duplicate events: MKG derives each event id from a hash of its payload.
   In a session where Claude runs these commands in auto mode, the classifier denies them. Switch the session to
   Manual (Claude app: **+** → **Permission**; terminal: Shift+Tab) and approve each command, or add an allow rule:

   ```json
   // ~/projects/haystack-workshop/.claude/settings.local.json
   { "permissions": { "allow": ["Bash(uv run --no-project --with neo4j python claude_memory/import_claude_history.py *)"] } }
   ```

4. **Create the MKG credentials file.** Done. It copies the values from the workshop `.env`, so no secret is typed
   or printed. The LLM is the workshop's Azure OpenAI deployment; the AI Studio project had no credits left
   (`402 Your prepayment credits are depleted`). `NEO4J_URI` must be the port the instance really listens on.

   ```bash
   ( set -a; . ~/projects/haystack-workshop/.env; set +a
     umask 077; mkdir -p ~/.config/meta-knowledge-graph
     cat > ~/.config/meta-knowledge-graph/.env <<EOT
   NEO4J_URI=bolt://127.0.0.1:7689
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=$NEO4J_PASSWORD
   NEO4J_DATABASE=neo4j
   MKG_USER_ID=you@example.com
   AZURE_API_KEY=$AZURE_OPENAI_API_KEY
   AZURE_API_BASE=$AZURE_OPENAI_ENDPOINT
   AZURE_API_VERSION=$AZURE_OPENAI_API_VERSION
   LLM_MODEL=azure/$AZURE_OPENAI_DEPLOYMENT
   EMBEDDING_MODEL=azure/text-embedding-3-small
   EMBEDDING_DIMENSIONS=1536
   EOT
   )
   ```

   Then start a new Claude Code session: hooks and the MCP server load only at session start. Verify with
   `claude plugin list` (`meta-knowledge-graph@mkg` enabled) and check that `mcp__meta-knowledge-graph__*` tools exist.

5. **Ask the 10 questions** and render the screenshots. Done, re-run on 7689 (see "Twelve questions"):

   ```bash
   uv run --no-project --with neo4j --with pillow python claude_memory/ask_memory.py --uri bolt://127.0.0.1:7689
   ```

   Writes `results/memory_questions.md` (question, Cypher, result) and `results/screenshots/q1.png` … `q10.png`.

6. **LLM extraction.** Done. `:Learning` and `:Observation` nodes (durable facts, episodic timeline) come from
   MKG's `process_project.py`, which calls the LLM from the credentials file (Azure OpenAI `gpt-5-mini`; the
   history stays in the workshop's own Azure resource in the EU data zone). Without it, the graph holds the raw trace
   and the tool-call graph only, and `project_get_context` / `episode_search` return nothing for imported sessions.
   Run it from the session's project directory, once per session (main sessions and subagents):

   ```bash
   P=~/.claude/plugins/cache/mkg/meta-knowledge-graph/0.1.43
   cd <project dir>
   echo '{"session_id":"<session-id>","cwd":"<project dir>"}' | \
     CLAUDE_PLUGIN_ROOT=$P uv run -q --script $P/hooks/process_project.py --mode session --session-id <session-id>
   ```

   It prints many Neo4j `01N51` / `01N52` warnings (property or relationship type does not exist yet) on a fresh
   graph; they are harmless. A small session takes about 25 s; the large TwoBreath session several minutes.
   The autonomous gate then approves or blocks each candidate (for example `approved`, `already_learned`, `blocked`).

   Embeddings: `text-embedding-3-small` (1536 dimensions) was deployed on `oai-haystack-f791ed` on 2026-10-18:

   ```bash
   az cognitiveservices account deployment create -g rg-haystack-workshop -n oai-haystack-f791ed \
     --deployment-name text-embedding-3-small --model-name text-embedding-3-small --model-version 1 \
     --model-format OpenAI --sku-name DataZoneStandard --sku-capacity 100
   ```

   Result (2026-10-18): 19 sessions processed (9 main, 10 subagents). The imported sessions gave 2-3 observations
   each (MKG summarizes a whole session in one batch) and 19 candidate learnings; this live session added more through
   the hooks. Gate outcome so far: approved, `already_learned` (duplicate of an approved one), `blocked`, and
   `candidate` (not judged yet; the gate runs in the background after later sessions).

   - [x] All 19 sessions extracted.
   - [x] Embeddings for nodes written before the embedding deployment existed. MKG has no backfill command, so
         `backfill_embeddings.py` does it with MKG's own config and embedding code (dry run without `--apply`):

         ```bash
         uv run --script claude_memory/backfill_embeddings.py --apply
         ```

   - [x] Questions re-run on 7689, plus Q11 (learnings) and Q12 (observation timeline).

## What the import keeps and drops

| | |
|---|---|
| Kept | user prompts, assistant text, tool calls (name + input), tool results (cut at 4,000 chars, MKG's own limit) |
| Dropped | thinking blocks, images, attachments, API request/blob/meta records, compact summaries, `<system-reminder>` and other injected context |
| Redacted | known values from every `~/projects/**/.env*` file, plus patterns: Google (`AIza…`, `AQ.…`), Anthropic, OpenAI, GitHub, Slack, AWS, ElevenLabs keys, JWTs, private keys, `user:pass@` in URLs, `KEY=` / `password:` assignments. Each becomes `[REDACTED:<kind>]`. |
| Last run | 19 transcripts; 52 redactions; gitleaks on the staged copy: 0 findings (two reviewed false positives allowlisted: the `oauth-2025-04-20` beta header, a Thoreau quote attribution) |

Plain-word passwords (e.g. `password`) are not replaced globally, only where they appear in an assignment.

## Graph model

```
(:User)-[:HAS_SESSION]->(:Session)<-[:HAS_SESSION]-(:Project)
(:Session)-[:FIRST_EVENT|LATEST_EVENT|HAS_EVENT]->(:SessionEvent)-[:NEXT]->(:SessionEvent)
(:Session)-[:HAS_SUBAGENT]->(:Session {agent_kind: 'subagent'})
# after enrich:
(:Session)-[:HAS_TURN]->(:Turn)-[:ISSUED]->(:ToolCall)-[:USES_TOOL]->(:Tool)
(:ToolCall)-[:RETURNED]->(:ToolResult)   (:ToolCall)-[:TARGETS]->(:Resource)   (:ToolCall)-[:NEXT_TOOL_CALL]->(:ToolCall)
# after LLM extraction:
(:Project)-[:HAS_LEARNING]->(:Learning)-[:FROM_SESSION]->(:Session)
(:Project)-[:HAS_OBSERVATION]->(:Observation)-[:NEXT]->(:Observation)
```

`SessionEvent` properties used by the queries: `event_name` (`UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`Stop`, ...), `timestamp` (original time from the transcript), `prompt`, `tool_name`, `tool_input`, `tool_response`,
`tool_error`, `cwd`.

## Cypher scripts

All read-only. Run them in Neo4j Browser (`http://127.0.0.1:7475`, or 7474 before the restart) or `cypher-shell`.

| File | Contents |
|------|----------|
| `cypher/model.cypher` | M1 schema visualization, M2 nodes per label, M3 relationship counts `(from)-[type]->(to)`, M4 properties per label, M5 indexes |
| `cypher/results.cypher` | R1 overview per project, R2 one session as an ordered event chain (`$session_id`), R3 tool usage and failures, R4 subagents, R5 provenance prompt → tool call → result → files (`$text`), R6 redaction check |
| `cypher/questions.cypher` | the twelve questions below |

In Neo4j Browser, set a parameter first: `:param session_id => '152df933-603e-402c-8857-7316452bf7e5'`.

## Asking through the MCP server

Once step 4 is done, every new Claude Code session has the MKG tools. Ask in plain language and name the tool,
or let Claude choose:

| Tool | Use |
|------|-----|
| `mcp__meta-knowledge-graph__neo4j_get_schema` | labels, relationship types, properties (same as `model.cypher`) |
| `mcp__meta-knowledge-graph__neo4j_read_cypher` | run any read-only Cypher, e.g. a block from `questions.cypher` |
| `mcp__meta-knowledge-graph__project_get_context` | learnings for the current project (from step 6) |
| `mcp__meta-knowledge-graph__episode_search` / `episode_fetch` | episodic timeline (from step 6) |

Example prompts:

- "Use neo4j_read_cypher to run Q5 from claude_memory/cypher/questions.cypher and explain how each Neo4j error was fixed."
- "Which Neo4j errors did I hit in the TwoBreath project? Query the memory graph."
- "Trace why the workshop uses bolt://127.0.0.1:7687: show the prompt, the tool calls and the result that led to it."

Claude Desktop or other MCP clients register the same server with
`uv --directory ~/.claude/plugins/marketplaces/mkg run meta-knowledge-graph`.

## Twelve questions about Neo4j projects and challenges

Ten questions on the raw trace plus two on the LLM-extracted memory, defined in `cypher/questions.cypher`, run by
`ask_memory.py` on 2026-10-18 against `bolt://127.0.0.1:7689`. Full results with the Cypher:
`results/memory_questions.md`.

| # | Question |
|---|----------|
| Q1 | Which projects did I work on with Claude Code, and how much? |
| Q2 | In which projects did Neo4j come up, and how often? |
| Q3 | Which Neo4j instances (connection URIs) did I use, and where? |
| Q4 | Which Neo4j / Cypher errors did I hit? |
| Q5 | How was each error fixed? (path from the error to the next successful call of the same tool) |
| Q6 | Which Neo4j challenges and exercises did I ask for? |
| Q7 | Which Cypher features did I use (vector index, `SEARCH`, `MERGE`, `COUNT {}`, ...)? |
| Q8 | Which files did my Neo4j work touch most? |
| Q9 | Where did the agent get blocked by permissions, per project? |
| Q10 | Why does the workshop use `bolt://127.0.0.1:7687`? Trace prompt → tool calls → decision. |
| Q11 | What did the memory learn per project, and what did the gate approve or block? |
| Q12 | Episodic timeline: what happened with Neo4j / GraphRAG, in order? |

Fixes after the first run:

- **One home project per session.** MKG links a session to every directory it touched, so a `cd exercises` created
  extra projects (`Exercises`, `Haystack` for the venv's site-packages, `Results`, and one per subagent worktree) and
  counted the same events several times. Per-project questions now use the project whose `repo_root` is the cwd of
  the session's first event; worktrees under `.claude/worktrees/` count for their repository.
- **Q3** URIs kept trailing backslashes from escaped JSON (`127.0.0.1:7687\`).
- **Q4 / Q5** error names kept a trailing `}` and missed exceptions after a line break. Tool output cut in the middle
  of a status code (`Neo.ClientError.Security....`) now falls back to the exception class.
- **Q8** listed tool responses, directories and shell fragments (`2>/dev/null`) as files; it now keeps file names only.

_Screenshots and `results/` are generated locally by `ask_memory.py` and are not published: they contain personal session history._

## Troubleshooting

**Every session reports `inject_project_context hook failed (... Neo.ClientError.Security.Unauthorized` or
`AuthenticationRateLimit)`.** `MKG_ENV_FILE` points to `~/.config/meta-knowledge-graph/.env`, but the file does not
exist yet. The hooks then fall back to MKG's defaults (`bolt://localhost:7687`, user `neo4j`, password `password`),
and Neo4j rejects them. After a few rejected logins in a row Neo4j locks the `neo4j` user for a few seconds, so even
correct credentials (the importer, `ask_memory.py`, the workshop) fail with `AuthenticationRateLimit` while a Claude
Code session is active. Fix: create the file (step 4), then start a new session. Until then, retry after a pause.
Fixed on 2026-10-18.

**`402 Your prepayment credits are depleted`** from Gemini: the AI Studio project has no credits. The credentials file
uses Azure OpenAI instead (step 4).

## Security notes

- From the moment the plugin has credentials, **every** Claude Code session on this machine is logged raw into
  Neo4j, including tool results such as file contents. MKG's safety screen filters only the learnings it extracts,
  not the raw events. Keep the instance local; do not deploy it to Aura.
- The import is safe to share only in its sanitized form; `~/.claude/projects` itself still contains the secrets.
- Text you select in the IDE is sent to Claude automatically. Do not select `.env` lines.
