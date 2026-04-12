"""
Prompt construction for the LLM planner.

Keeping prompts here (not inlined in the planner) makes them easy to iterate,
diff, and test in isolation.
"""

from __future__ import annotations

from worker.tools.registry import list_tools

# ---------------------------------------------------------------------------
# Per-tool descriptions shown to the LLM so it knows when to use each one
# ---------------------------------------------------------------------------

TOOL_DESCRIPTIONS: dict[str, str] = {
    "web_search": (
        "Search the public internet for current information. "
        "tool_input: {\"query\": \"<search query>\", \"max_results\": <int, optional>}"
    ),
    "code_exec": (
        "Execute a Python code snippet in a sandbox and return stdout. "
        "tool_input: {\"code\": \"<python source>\", \"timeout\": <int seconds, optional>}"
    ),
    "retrieval": (
        "Search documents the user has already uploaded to their knowledge base. "
        "Use this — NOT web_search — when the prompt refers to uploaded files, "
        "internal documents, or the user's own data. "
        "tool_input: {\"query\": \"<search query>\", \"top_k\": <int, optional, default 5>}. "
        "Do NOT include workspace_id — it is injected automatically by the executor."
    ),
}

# ---------------------------------------------------------------------------
# System prompt — instructs the LLM on its role and the exact output contract
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a task planner for a distributed AI agent platform.
Your job is to decompose a user's natural language request into a structured \
execution plan that a fleet of worker agents can execute step-by-step.

You must respond with a single JSON object — no prose, no markdown, no code fences.

The JSON must match this schema exactly:

{
  "steps": [
    {
      "step_id":        "<unique snake_case string, e.g. search_papers>",
      "name":           "<short human-readable title>",
      "description":    "<what this step accomplishes>",
      "task_type":      "<tool_call | synthesis>",
      "tool_name":      "<registered tool name, or null if task_type=synthesis>",
      "tool_input":     { <tool-specific arguments> },
      "dependencies":   ["<step_id>", ...],
      "priority":       <integer, 0=highest, default 0>,
      "expected_output":"<short description of a successful result>"
    }
  ]
}

Rules:
- Every step must have a unique step_id.
- dependencies must only reference step_ids defined in the same plan.
- Steps with no dependencies can run in parallel.
- The last step should always be a synthesis step (task_type=synthesis, tool_name=null) \
that aggregates prior results into a final answer.
- Use only tools from the AVAILABLE TOOLS list.
- tool_input must be a JSON object (never null — use {} if empty).
- Generate between 2 and 8 steps. Do not over-decompose.
- Tool selection guidance:
    * Use "retrieval" when the user asks about uploaded files, their own documents, \
or internal knowledge base content. Do NOT use web_search for these queries.
    * Use "web_search" for general internet searches and current public information.
    * Use "code_exec" for computation, data processing, or running Python code.
    * retrieval tool_input must contain "query" and optionally "top_k". \
Never include workspace_id — it is injected automatically.
"""


def build_user_prompt(prompt: str, context: list[str] | None = None) -> str:
    """
    Build the user-turn message sent to the LLM.

    If *context* is provided (retrieved from memory), it is injected before
    the user request so the LLM can reuse prior findings and avoid redundant
    work on similar tasks.
    """
    tools = list_tools()

    # Annotate each tool name with its description when available
    tool_lines: list[str] = []
    for t in tools:
        desc = TOOL_DESCRIPTIONS.get(t)
        if desc:
            tool_lines.append(f"  - {t}: {desc}")
        else:
            tool_lines.append(f"  - {t}")
    tool_list = "\n".join(tool_lines) if tool_lines else "  (none registered yet)"

    context_section = ""
    if context:
        snippets = "\n".join(f"  [{i+1}] {c}" for i, c in enumerate(context))
        context_section = f"\nRELEVANT PAST RESULTS (from memory — use as context, do not repeat verbatim):\n{snippets}\n"

    return f"""\
AVAILABLE TOOLS:
{tool_list}
{context_section}
USER REQUEST:
{prompt}

Respond with the JSON execution plan now.\
"""
