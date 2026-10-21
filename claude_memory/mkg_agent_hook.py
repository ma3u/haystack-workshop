# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
Connect other coding agents (Gemini CLI, GitHub Copilot CLI) to the MKG memory graph, without changing MKG.

MKG's hooks expect Claude Code payloads. This adapter reads the agent's hook payload from stdin, maps it to the
Claude Code shape, and runs MKG's own hook scripts with it:

  every event          hooks/log_event.py --client <client>     (same graph shape as Claude Code sessions)
  SessionStart/prompt  hooks/inject_project_context.py          (recall, returned to the agent as context)
  prompt, no MKG hit   recall_all.py --hook                      (cross-project recall with a cosine floor)
  Stop (end of turn)   hooks/process_project.py --mode turn      (learning extraction, detached)
  SessionEnd           scrub_secrets.py --hook                   (redaction, detached)

    Gemini CLI  (~/.gemini/settings.json "hooks"):   uv run --script .../mkg_agent_hook.py --client gemini_cli
    Copilot CLI (~/.copilot/hooks/mkg.json):         uv run --script .../mkg_agent_hook.py --client copilot_cli --event userPromptSubmitted

Copilot's camelCase payloads carry no event name, so its config passes --event. It never blocks the agent:
stdout is the agent's JSON output ("{}" when there is nothing to add), errors go to stderr, exit code is 0.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

MKG_ROOT = Path.home() / ".claude/plugins/cache/mkg/meta-knowledge-graph/0.1.43"
HOOKS = MKG_ROOT / "hooks"
SCRUB = Path(__file__).resolve().parent / "scrub_secrets.py"
RECALL = Path(__file__).resolve().parent / "recall_all.py"

# agent event name -> Claude Code event name
EVENTS = {
    # Gemini CLI
    "SessionStart": "SessionStart", "SessionEnd": "SessionEnd", "BeforeAgent": "UserPromptSubmit",
    "AfterAgent": "Stop", "BeforeTool": "PreToolUse", "AfterTool": "PostToolUse", "PreCompress": "PreCompact",
    "Notification": "Notification",
    # Copilot CLI (camelCase config events)
    "sessionStart": "SessionStart", "sessionEnd": "SessionEnd", "userPromptSubmitted": "UserPromptSubmit",
    "agentStop": "Stop", "preToolUse": "PreToolUse", "postToolUse": "PostToolUse",
    "postToolUseFailure": "PostToolUseFailure", "preCompact": "PreCompact", "subagentStop": "SubagentStop",
    "notification": "Notification",
    # Claude-style names (Copilot also accepts PascalCase events with snake_case fields)
    "UserPromptSubmit": "UserPromptSubmit", "PreToolUse": "PreToolUse", "PostToolUse": "PostToolUse",
    "Stop": "Stop", "PreCompact": "PreCompact",
}


def as_text(value) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def normalize(raw: dict, event: str) -> dict:
    """One Claude Code hook payload from a Gemini or Copilot payload."""
    tool_input = raw.get("tool_input", raw.get("toolArgs"))
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except json.JSONDecodeError:
            tool_input = {"args": tool_input}
    response = raw.get("tool_response", raw.get("toolResult"))
    error = raw.get("error")
    if isinstance(response, dict):  # Gemini {llmContent, returnDisplay, error}, Copilot {resultType, textResultForLlm}
        error = error or response.get("error")
        response = response.get("llmContent", response.get("textResultForLlm", response))
    payload = {
        "hook_event_name": event,
        "session_id": raw.get("session_id") or raw.get("sessionId"),
        "cwd": raw.get("cwd") or os.getcwd(),
        "prompt": raw.get("prompt") if event == "UserPromptSubmit" else None,
        "tool_name": raw.get("tool_name") or raw.get("toolName"),
        "tool_input": tool_input,
        "tool_response": as_text(response),
        "tool_error": as_text(error) if error else None,
        "last_assistant_message": raw.get("prompt_response") or raw.get("response"),
        "source": raw.get("source"),
        "reason": raw.get("reason"),
        "agent_id": raw.get("agentId"),
        # transcript_path is left out: MKG reads it as a Claude Code JSONL transcript
    }
    if event == "PostToolUseFailure":
        payload["hook_event_name"] = "PostToolUse"
    return {k: v for k, v in payload.items() if v not in (None, "", {})}


def run_mkg(script: str, payload: dict, *args: str, detach: bool = False) -> str:
    cmd = ["uv", "run", "-q", "--script", str(HOOKS / script), *args]
    env = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(MKG_ROOT))
    env.setdefault("MKG_ENV_FILE", str(Path.home() / ".config/meta-knowledge-graph/.env"))
    if detach:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=env, text=True, start_new_session=True)
        p.stdin.write(json.dumps(payload))
        p.stdin.close()
        return ""
    r = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, env=env, timeout=60)
    errors = [x for x in r.stderr.splitlines() if "error" in x.lower() and "GqlStatusObject" not in x]
    if errors:  # Neo4j notifications (warnings) are not errors
        print("\n".join(errors)[-500:], file=sys.stderr)
    return r.stdout


def context_of(output: str) -> str:
    try:
        return json.loads(output)["hookSpecificOutput"]["additionalContext"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return ""


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--client", required=True, help="client name stored on the events, e.g. gemini_cli, copilot_cli")
    p.add_argument("--event", help="agent event name, when the payload has none (Copilot camelCase hooks)")
    args = p.parse_args()
    out = {}
    try:
        raw = json.loads(sys.stdin.read() or "{}")
        agent_event = args.event or raw.get("hook_event_name") or ""
        event = EVENTS.get(agent_event)
        if not event:
            print(json.dumps(out))
            return 0
        payload = normalize(raw, event)
        context = ""
        if event in ("SessionStart", "UserPromptSubmit"):
            context = context_of(run_mkg("inject_project_context.py", payload))
            if not context and event == "UserPromptSubmit":
                r = subprocess.run(["uv", "run", "-q", "--script", str(RECALL), "--hook"], input=json.dumps(payload),
                                   capture_output=True, text=True, timeout=60)
                context = context_of(r.stdout)
        run_mkg("log_event.py", payload, "--client", args.client)
        if event == "Stop":
            run_mkg("process_project.py", payload, "--mode", "turn", "--background", detach=True)
        if event == "SessionEnd":
            subprocess.Popen(["uv", "run", "-q", "--script", str(SCRUB), "--hook"], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        if context:
            if args.client.startswith("copilot"):
                # Copilot config hooks honor additionalContext only on sessionStart (not on prompts)
                out = {"additionalContext": context} if event == "SessionStart" else {}
            else:
                out = {"hookSpecificOutput": {"hookEventName": agent_event, "additionalContext": context}}
    except Exception as exc:  # a memory hook must never break the agent
        print(f"[mkg_agent_hook] {type(exc).__name__}: {exc}", file=sys.stderr)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
