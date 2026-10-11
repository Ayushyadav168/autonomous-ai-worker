"""
Tool schemas (Anthropic tool-use format) and their execution.

Every tool execution is wrapped so that a Python exception becomes a text
observation fed back to the model (`is_error=True`), rather than crashing
the run. This is the core "failure detection" mechanism: the agent sees
"browser_click failed: ..." in its own transcript and can decide to retry,
try something else, or ask for help - exactly like it would see a real
error page or timeout in production.

`browser_navigate` / `browser_view_image` are fully domain-agnostic. The one
intentionally domain-specific tool is `verify_outcome`, because "what does
success mean" is different for every task; see README "Generalization" for
why that boundary was drawn here rather than trying to make verification
fully generic in a 3-day prototype.
"""
import requests

from .browser import BrowserController, BrowserError, image_bytes_to_base64
from .memory import Memory

TOOL_DEFINITIONS = [
    {
        "name": "browser_navigate",
        "description": "Navigate the browser to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
    },
    {
        "name": "browser_read_page",
        "description": (
            "Get a compact snapshot of the current page: title, url, and a list of "
            "interactive/informative elements, each with a short ref id (e.g. 'ref_3'). "
            "Call this after every navigation or click."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_click",
        "description": "Click an element by its ref id from the last browser_read_page call.",
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    {
        "name": "browser_type",
        "description": "Type text into an input/textarea element by its ref id (replaces existing content).",
        "input_schema": {
            "type": "object",
            "properties": {
                "ref": {"type": "string"},
                "text": {"type": "string"},
            },
            "required": ["ref", "text"],
        },
    },
    {
        "name": "browser_view_image",
        "description": (
            "Look at an image on the current page (e.g. a scanned invoice/attachment) by "
            "its ref id, and receive its visual content so you can read values not present "
            "as text anywhere on the page."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"ref": {"type": "string"}},
            "required": ["ref"],
        },
    },
    {
        "name": "save_fact",
        "description": "Save a fact you've discovered to your scratchpad memory, e.g. save_fact('acme_latest_amount', '5,100.00').",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "get_facts",
        "description": "Retrieve all facts you've saved so far in this task.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "verify_outcome",
        "description": (
            "Independently verify that a billing record now exists in the internal system "
            "with the expected values, by querying the system's own data directly (not by "
            "trusting a UI message). Returns whether a matching record was found and, if "
            "not, what records do exist for that company/invoice number for diagnosis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "company": {"type": "string"},
                "invoice_number": {"type": "string"},
                "expected_amount": {"type": "string"},
                "expected_due_date": {"type": "string"},
            },
            "required": ["company", "invoice_number", "expected_amount", "expected_due_date"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Pause and ask the human a clarifying question. Only use this when you are "
            "genuinely stuck: ambiguous target, missing required information, or an action "
            "you cannot safely verify or undo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "finish",
        "description": (
            "End the task. success must only be true if verify_outcome actually confirmed "
            "the result. evidence should name what you checked and what it showed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "success": {"type": "boolean"},
                "evidence": {"type": "string"},
            },
            "required": ["summary", "success", "evidence"],
        },
    },
]


class ImageToolResult:
    """Sentinel wrapper so agent_loop knows to build an image content block for the API."""

    def __init__(self, image_bytes: bytes, caption: str = ""):
        self.image_bytes = image_bytes
        self.caption = caption


class ToolExecutor:
    def __init__(self, browser: BrowserController, memory: Memory, base_url: str):
        self.browser = browser
        self.memory = memory
        self.base_url = base_url.rstrip("/")

    def execute(self, name: str, tool_input: dict):
        """Returns (result, is_error). `result` is a str or an ImageToolResult."""
        try:
            handler = getattr(self, f"_tool_{name}", None)
            if handler is None:
                return f"Unknown tool: {name}", True
            return handler(tool_input), False
        except BrowserError as e:
            return str(e), True
        except requests.RequestException as e:
            return f"Network error talking to internal system: {e}", True
        except Exception as e:
            return f"Unexpected error running tool '{name}': {e}", True

    def _tool_browser_navigate(self, inp):
        return self.browser.navigate(inp["url"])

    def _tool_browser_read_page(self, inp):
        snapshot = self.browser.read_page()
        lines = [f"URL: {snapshot['url']}", f"Title: {snapshot['title']}", "Elements:"]
        for el in snapshot["elements"]:
            desc = f"  [{el['ref']}] <{el['tag']}>"
            if el.get("text"):
                desc += f' text="{el["text"]}"'
            if el.get("href"):
                desc += f' href="{el["href"]}"'
            if el.get("input_type"):
                desc += f' type={el["input_type"]} name={el.get("name")} value="{el.get("value", "")}"'
            if el.get("src"):
                desc += f' src="{el["src"]}"'
            if el.get("data") and (el["data"].get("invoice_number") or el["data"].get("company")):
                desc += f" data={el['data']}"
            lines.append(desc)
        return "\n".join(lines)

    def _tool_browser_click(self, inp):
        return self.browser.click(inp["ref"])

    def _tool_browser_type(self, inp):
        return self.browser.type_text(inp["ref"], inp["text"])

    def _tool_browser_view_image(self, inp):
        data = self.browser.view_image(ref=inp["ref"])
        return ImageToolResult(data, caption=f"Image content of {inp['ref']}")

    def _tool_save_fact(self, inp):
        return self.memory.save_fact(inp["key"], inp["value"])

    def _tool_get_facts(self, inp):
        facts = self.memory.get_facts()
        if not facts:
            return "No facts saved yet."
        return "\n".join(f"{k}: {v}" for k, v in facts.items())

    def _tool_verify_outcome(self, inp):
        resp = requests.get(f"{self.base_url}/api/records", timeout=5)
        resp.raise_for_status()
        records = resp.json().get("records", [])

        matches = [
            r
            for r in records
            if r["company"].strip().lower() == inp["company"].strip().lower()
            and r["invoice_number"].strip().lower() == inp["invoice_number"].strip().lower()
        ]
        if not matches:
            return (
                f"NOT FOUND: no record for company={inp['company']!r} "
                f"invoice_number={inp['invoice_number']!r}. "
                f"Existing records: {records}"
            )

        rec = matches[0]
        mismatches = []
        if rec["amount"].strip() != inp["expected_amount"].strip():
            mismatches.append(f"amount: found {rec['amount']!r}, expected {inp['expected_amount']!r}")
        if rec["due_date"].strip() != inp["expected_due_date"].strip():
            mismatches.append(f"due_date: found {rec['due_date']!r}, expected {inp['expected_due_date']!r}")

        if mismatches:
            return f"FOUND BUT MISMATCHED: {'; '.join(mismatches)}. Full record: {rec}"
        return f"VERIFIED: record matches exactly. {rec}"
