"""
The core ReAct-style loop: ask Claude for the next action(s), execute them,
feed the real-world observation back, repeat - until the model calls
`finish` or `ask_user`, or a step cap is hit.

This loop itself has zero knowledge of invoices/billing/etc. The only
task-specific thing it's given is the natural-language task string and the
tool list; everything else (planning, sequencing, deciding when to retry
vs. ask for help vs. declare done) is the model's job. That's the
"generalization" property: point this same loop at a different task string
and a different (or the same) set of tools, and nothing here changes.
"""
import json
import os
import sys
import time

import anthropic

from .prompts import SYSTEM_PROMPT
from .tools import TOOL_DEFINITIONS, ImageToolResult, ToolExecutor
from .browser import image_bytes_to_base64

DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "25"))


class AgentResult:
    def __init__(self, status, summary, success, evidence, transcript, question=None):
        self.status = status  # "finished" | "needs_clarification" | "max_steps_exceeded"
        self.summary = summary
        self.success = success
        self.evidence = evidence
        self.transcript = transcript
        self.question = question

    def to_dict(self):
        return {
            "status": self.status,
            "summary": self.summary,
            "success": self.success,
            "evidence": self.evidence,
            "question": self.question,
        }


def _log(transcript, entry):
    entry["t"] = time.strftime("%Y-%m-%d %H:%M:%S")
    transcript.append(entry)
    kind = entry.get("type")
    if kind == "assistant_text":
        print(f"\n[agent] {entry['text']}")
    elif kind == "tool_call":
        print(f"[agent] -> {entry['tool']}({entry['input']})")
    elif kind == "tool_result":
        preview = entry["result"] if isinstance(entry["result"], str) else "<image>"
        preview = preview[:300] + ("..." if len(preview) > 300 else "")
        tag = "ERROR" if entry["is_error"] else "ok"
        print(f"[env]   <- [{tag}] {preview}")


def run_agent(task: str, executor: ToolExecutor, interactive_ask: bool = True) -> AgentResult:
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": task}]
    transcript = [{"type": "task", "task": task}]

    for step in range(1, MAX_STEPS + 1):
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=1536,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        assistant_content = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                _log(transcript, {"type": "assistant_text", "text": block.text.strip()})
            assistant_content.append(block)
        messages.append({"role": "assistant", "content": assistant_content})

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_use_blocks:
            # Model produced only text with no action - nudge it to act or finish.
            messages.append(
                {
                    "role": "user",
                    "content": "Please take the next tool action, or call `finish` if the task is complete.",
                }
            )
            continue

        tool_result_content = []
        stop_status = None
        stop_payload = None

        for block in tool_use_blocks:
            _log(transcript, {"type": "tool_call", "tool": block.name, "input": block.input})

            if block.name == "finish":
                stop_status = "finished"
                stop_payload = block.input
                tool_result_content.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": "Task ended."}
                )
                continue

            if block.name == "ask_user":
                question = block.input.get("question", "")
                if interactive_ask and sys.stdin.isatty():
                    print(f"\n[agent asks] {question}")
                    answer = input("Your answer: ")
                    _log(transcript, {"type": "tool_result", "tool": "ask_user", "result": answer, "is_error": False})
                    tool_result_content.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": answer}
                    )
                    continue
                else:
                    stop_status = "needs_clarification"
                    stop_payload = {"question": question}
                    tool_result_content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "Non-interactive run: question recorded, run ending.",
                        }
                    )
                    continue

            result, is_error = executor.execute(block.name, block.input)

            if isinstance(result, ImageToolResult):
                b64 = image_bytes_to_base64(result.image_bytes)
                tool_result_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": [
                            {"type": "text", "text": result.caption},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": b64,
                                },
                            },
                        ],
                    }
                )
                _log(transcript, {"type": "tool_result", "tool": block.name, "result": "<image>", "is_error": False})
            else:
                tool_result_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                        "is_error": is_error,
                    }
                )
                _log(transcript, {"type": "tool_result", "tool": block.name, "result": result, "is_error": is_error})

        messages.append({"role": "user", "content": tool_result_content})

        if stop_status == "finished":
            return AgentResult(
                status="finished",
                summary=stop_payload.get("summary", ""),
                success=bool(stop_payload.get("success", False)),
                evidence=stop_payload.get("evidence", ""),
                transcript=transcript,
            )
        if stop_status == "needs_clarification":
            return AgentResult(
                status="needs_clarification",
                summary="Agent paused for clarification.",
                success=False,
                evidence="",
                transcript=transcript,
                question=stop_payload.get("question"),
            )

    return AgentResult(
        status="max_steps_exceeded",
        summary=f"Stopped after {MAX_STEPS} steps without finishing.",
        success=False,
        evidence="",
        transcript=transcript,
    )


def save_transcript(transcript, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(transcript, f, indent=2, default=str)
