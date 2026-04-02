"""
Prompt construction for the LLM planner.

Keeping prompts here (not inlined in the planner) makes them easy to iterate,
diff, and test in isolation.
"""

from __future__ import annotations

from worker.tools.registry import list_tools

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
- tool_input must be a JSON object (never null — use {{}} if empty).
- Generate between 2 and 8 steps. Do not over-decompose.
"""


def build_user_prompt(prompt: str, context: list[str] | None = None) -> str:
    """
    Build the user-turn message sent to the LLM.

    If *context* is provided (retrieved from memory), it is injected before
    the user request so the LLM can reuse prior findings and avoid redundant
    work on similar tasks.
    """
    tools = list_tools()
    tool_list = "\n".join(f"  - {t}" for t in tools) if tools else "  (none registered yet)"

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
