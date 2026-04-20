from __future__ import annotations

from autopatch_j.agent.types import AgentContext


SYSTEM_PROMPT = (
    "You are AutoPatch-J, a ReAct-Lite coding agent for Java static-scan repair. "
    "For each user turn, decide whether you need a tool or can answer directly. "
    "Call scan when the user asks to scan, inspect findings, or look for Java code problems. "
    "Call patch when the user asks to generate or revise a minimal patch from active findings. "
    "If scoped paths are provided, keep scan limited to those paths. "
    "Patch application is always gated by the human and must never be applied by the agent. "
    "If no tool is needed, reply with concise plain text. "
    "Do not reveal chain-of-thought."
)


ACTION_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "scan",
            "description": "Run the local Java scanner on the selected repository scope.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Repository-relative file or directory paths to scan.",
                    }
                },
                "required": ["scope"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch",
            "description": "Draft one minimal patch from active findings or revise the pending patch.",
            "parameters": {
                "type": "object",
                "properties": {
                    "finding_index": {
                        "type": "integer",
                        "description": "One-based finding index selected by the user, if specified.",
                    }
                },
                "additionalProperties": False,
            },
        },
    },
]


def build_agent_messages(context: AgentContext) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": render_agent_prompt(context)},
    ]


def render_agent_prompt(context: AgentContext) -> str:
    scoped_paths = ", ".join(context.scoped_paths) if context.scoped_paths else "(none)"
    return (
        f"User text:\n{context.user_text}\n\n"
        f"Scoped paths:\n{scoped_paths}\n\n"
        f"Mention context:\n{context.mention_context}\n\n"
        f"Has active findings:\n{context.has_active_findings}\n"
    )
