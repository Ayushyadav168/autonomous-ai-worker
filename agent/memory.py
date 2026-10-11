"""
Explicit working memory for the agent, separate from raw conversation
history. The LLM already "remembers" via its context window, but an
explicit, inspectable scratchpad gives two things the raw transcript
doesn't: an audit trail of what the agent believes are the load-bearing
facts, and a store that survives even if the transcript is later truncated
or summarized.
"""
import json
import os
import time


class Memory:
    def __init__(self, path: str):
        self.path = path
        self.facts = {}

    def save_fact(self, key: str, value: str):
        self.facts[key] = {"value": value, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self._flush()
        return f"Saved fact '{key}'."

    def get_facts(self) -> dict:
        return {k: v["value"] for k, v in self.facts.items()}

    def _flush(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.facts, f, indent=2)
