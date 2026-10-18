// Import results: what the Claude Code history import wrote. Read-only.

// R1: overview per project
MATCH (p:Project)-[:HAS_SESSION]->(s:Session)-[:HAS_EVENT]->(e:SessionEvent)
RETURN p.name AS project, count(DISTINCT s) AS sessions,
       count(CASE WHEN e.event_name = 'UserPromptSubmit' THEN 1 END) AS prompts,
       count(CASE WHEN e.event_name = 'PreToolUse' THEN 1 END) AS tool_calls,
       toString(date(min(e.timestamp))) AS first, toString(date(max(e.timestamp))) AS last
ORDER BY prompts DESC;

// R2: one session as an ordered event chain (replace the session id)
MATCH (s:Session {session_id: $session_id})-[:FIRST_EVENT]->(first)
MATCH path = (first)-[:NEXT*0..]->(e)
RETURN e.timestamp AS ts, e.event_name AS event, e.tool_name AS tool,
       left(coalesce(e.prompt, e.tool_input, e.last_assistant_message, ''), 100) AS text
ORDER BY ts LIMIT 200;

// R3: tool usage across all sessions (from the enriched tool-call graph)
MATCH (c:ToolCall)-[:USES_TOOL]->(t:Tool)
OPTIONAL MATCH (c)-[:RETURNED]->(r:ToolResult)
RETURN t.name AS tool, count(c) AS calls,
       count(CASE WHEN r.succeeded = false THEN 1 END) AS failed
ORDER BY calls DESC;

// R4: subagents and what spawned them
MATCH (parent:Session)-[:HAS_SUBAGENT]->(child:Session)
OPTIONAL MATCH (child)-[:HAS_EVENT]->(e:SessionEvent)
RETURN parent.session_id AS parent, child.session_id AS subagent, count(e) AS events
ORDER BY events DESC;

// R5: provenance of one tool call: prompt that started the turn -> call -> result -> files
MATCH (t:Turn)-[:ISSUED]->(c:ToolCall)-[:USES_TOOL]->(tool:Tool)
WHERE c.input_summary CONTAINS $text
OPTIONAL MATCH (c)-[:RETURNED]->(r:ToolResult)
OPTIONAL MATCH (c)-[:TARGETS]->(res:Resource)
RETURN left(t.prompt, 120) AS prompt, tool.name AS tool, left(c.input_summary, 120) AS call,
       r.succeeded AS ok, left(r.summary, 120) AS result, collect(DISTINCT res.name)[..5] AS resources
LIMIT 10;

// R6: redaction check: the import replaced secrets with [REDACTED:<kind>] markers
MATCH (e:SessionEvent)
WITH e, [x IN [e.prompt, e.tool_input, e.tool_response] WHERE x CONTAINS '[REDACTED:'] AS hits
WHERE size(hits) > 0
RETURN count(e) AS events_with_redactions;
