"""
Talk to the MKG MCP server over stdio, the same way Claude Code does, without restarting Claude Code.

    uv run --no-project --with mcp python claude_memory/mcp_probe.py list
    uv run --no-project --with mcp python claude_memory/mcp_probe.py call \
        project_get_context '{"project_id": "haystack-workshop", "query": "GraphRAG accuracy"}' \
        neo4j_read_cypher '{"query": "MATCH (l:Learning) RETURN l.status, count(*)"}'

MAXC limits printed characters per result (default 3000). ERRLOG=<file> keeps the server's stderr.
"""
import asyncio, json, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = os.path.expanduser("~/.claude/plugins/cache/mkg/meta-knowledge-graph/0.1.43")

async def main():
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=ROOT, CLAUDE_PROJECT_DIR=os.getcwd())
    params = StdioServerParameters(command="bash", args=[f"{ROOT}/scripts/mcp-launcher.sh", ROOT], env=env, cwd=os.getcwd())
    async with stdio_client(params, errlog=open(os.environ.get("ERRLOG", os.devnull), "w")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            if sys.argv[1] == "list":
                for t in (await s.list_tools()).tools:
                    props = list((getattr(t, "input_schema", None) or getattr(t, "inputSchema", None) or {}).get("properties", {}).keys())
                    print(f"{t.name}  {props}\n    {(t.description or '').splitlines()[0][:150]}")
                return
            args = sys.argv[2:]
            for i in range(0, len(args), 2):
                tool, a = args[i], json.loads(args[i + 1])
                res = await s.call_tool(tool, a)
                print(f"\n### {tool} {json.dumps(a)[:200]}  isError={getattr(res, 'is_error', getattr(res, 'isError', None))}")
                for c in res.content:
                    print(getattr(c, "text", c)[:int(os.environ.get('MAXC', '3000'))])

if __name__ == "__main__":
    asyncio.run(main())
