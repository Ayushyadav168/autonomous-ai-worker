# Autonomous AI Task Worker

A prototype AI worker that takes a natural-language task, figures out the steps itself,
drives a real browser to do the work, verifies the outcome against ground truth, and
handles failures/ambiguity along the way — built for the CentrAlign AI Engineering
Intern take-home problem statement ("Autonomous AI Task Worker").

Example task it solves end to end:

> "Find the latest invoice from Acme Corp, extract the amount and due date from the
> invoice attachment, enter it into our internal system, and tell me once it is done."

## What it actually does (not simulated)

1. Boots a small local "company" — a Flask app with a mock **Inbox** (several invoices
   from different senders, amounts/due-dates rendered only as pixels in a scanned-document
   image, like a real invoice PDF) and a mock **Internal System** (a web form + record
   list backed by SQLite).
2. An agent loop gives Claude a small set of tools (browser navigate/click/type/read,
   "look at this image", a scratchpad, an outcome-verification query, ask-for-help, and
   finish) and lets it decide, step by step, what to do — there is no hardcoded script for
   "open inbox, click invoice #2, ...".
3. Claude actually drives a real Chromium browser via Playwright: clicks into the inbox,
   opens the right invoice, **visually reads** the amount/due date off the rendered image
   (it cannot cheat by reading a text field — there isn't one), navigates to the internal
   system, fills in and submits the form.
4. The internal system's submit endpoint **deliberately fails the first attempt** for any
   given invoice (simulated transient backend error). The agent has to notice the failure,
   decide to retry, and succeed on the second attempt — this is real, reproducible
   failure-handling, not a mocked retry.
5. Before declaring success, the agent must call a `verify_outcome` tool that queries the
   internal system's own JSON data directly (independent of whatever the browser DOM
   claims) and compares it field-by-field against what it extracted from the invoice.
6. Everything is logged: a full step-by-step transcript (reasoning, tool calls, tool
   results) plus screenshots, saved under `logs/run_<timestamp>/`.

## Setup

Requires Python 3.9+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY=sk-ant-...
```

## Running it

```bash
# default example task (Acme Corp, latest invoice)
python run.py

# watch the browser work instead of running headless
python run.py --show

# a different task, same code, no changes needed (see "Generalization" below)
python run.py --task-file tasks/example2_zenith.txt
python run.py --task-file tasks/example3_check_then_act.txt

# or type any task yourself
python run.py "Find the invoice from Nimbus Traders and enter it into the internal system."
```

Each run prints a live trace of the agent's reasoning and actions, then a final report
including an **independent** read-out of the internal system's actual stored data (not
just the agent's self-report) and the path to the full transcript/screenshots.

To reset the mock environment's database between manual experiments, restart `run.py` —
it recreates `mock_env/internal_system.db` and the flaky-submit counters fresh each run.

### Verifying the pipeline without spending API credits

```bash
python3 -m tests.scripted_smoke_test
```

This drives the **real** browser against the **real** mock Flask app through every tool
(navigate, click, type, view an invoice image, hit the deliberate first-attempt submit
failure, retry, and independently verify the result via `/api/records`) with a hardcoded
action sequence standing in for Claude's reasoning — no API key needed. It exists to
separate two risks: "does the mechanical pipeline work" (this test proves it,
deterministically) vs. "does Claude reason well enough to drive it autonomously" (only a
real `python run.py` run with a funded API key can answer that). If the smoke test passes
but a live run misbehaves, the bug is in prompting/agent behavior, not the tools/environment.

## Architecture

```
run.py                     CLI entrypoint: boots mock server, runs the agent, reports
mock_env/
  app.py                   Flask app: Inbox + Internal System + /api/records (ground truth)
  seed_data.py             Invoice ground-truth data
  generate_invoices.py     Renders each invoice as a PNG "scan" (Pillow)
  templates/               Inbox + Internal System HTML
agent/
  agent_loop.py            The ReAct-style loop: ask Claude -> execute tool(s) -> feed
                            back real observations -> repeat, until finish/ask_user/cap
  tools.py                 Tool schemas + execution (browser, memory, verification)
  browser.py               Playwright wrapper (navigate/click/type/read_page/view_image)
  memory.py                Agent's explicit scratchpad (save_fact/get_facts)
  prompts.py               System prompt describing the agent's tools and operating rules
```

### The agent loop (how autonomy/retries/verification actually work)

- The loop sends the task + full conversation to Claude with the tool definitions and lets
  it choose what to call next — the loop has no "if task mentions invoice then..." logic.
- Every tool call is executed and wrapped in a try/except. **A Python exception becomes a
  text observation** fed straight back to Claude (e.g. `"browser_click failed: no element
  with ref=ref_7 found"`), rather than crashing the process. This is the failure-detection
  mechanism: the model sees the same kind of signal a human would (an error, a stale
  reference, a rejected form) and decides what to do about it — retry, re-read the page,
  try a different path, or ask for help.
- `browser_read_page` returns a short list of `ref_N` handles instead of raw HTML or asking
  the model to invent CSS selectors — this is far more reliable for an LLM than selector
  guessing, and keeps the model's "what can I do right now" context small.
- `verify_outcome` queries `/api/records` directly over HTTP — it does **not** re-read the
  page the agent just submitted. This matters: a UI can show a misleading "success" banner,
  or the agent could misremember what it clicked. The system prompt requires a verified
  match before `finish(success=true)` is allowed, and `run.py` additionally prints the
  actual database contents at the end regardless of what the agent claims, so a human
  reviewer always has ground truth, not just the agent's word.
- `ask_user` lets the agent pause instead of guessing when it's genuinely stuck. In an
  interactive terminal it prompts and reads your answer, feeding it back into the loop so
  the agent continues with that information. In a non-interactive run it stops cleanly and
  surfaces the question in the final report, rather than hanging.
- A hard step cap (`AGENT_MAX_STEPS`, default 25) prevents infinite loops; the system
  prompt also tells the model to stop retrying an identical failing action after 3 attempts.

### Generalization

The loop, the browser tools, the memory tool, `ask_user`, and `finish` are all completely
task-agnostic — they know nothing about invoices. Pointing `run.py` at a different task
string (see `tasks/example2_zenith.txt`, a different company, and
`tasks/example3_check_then_act.txt`, a check-then-conditionally-act task requiring
different reasoning) requires **zero code changes**.

The one deliberately domain-specific tool is `verify_outcome`: "what does success mean" is
different for every real task, and building a fully generic verifier was out of scope for
a 3-day prototype. In a production version, the verification step would be swapped per
task-type (or generated per task) while the loop, retry policy, and failure handling stay
identical — that boundary is the main thing I'd change first with more time (see below).

## Key design decisions

- **Browser automation over direct API calls**, even though the mock internal system has a
  simpler HTTP API under the hood. The JD specifically calls out "browser automation and
  computer use" and real company systems are rarely programmatically accessible the way
  the backend happens to be here — so the agent is deliberately made to work the way a
  human would, through the UI.
- **Amount/due-date only exist as image pixels**, never as text/HTML, specifically to force
  genuine use of Claude's vision for "reading an attachment" rather than letting the agent
  shortcut by parsing structured text — closer to a real scanned invoice.
- **Deterministic, reproducible flakiness** (first submit always fails) rather than random
  failure — makes the retry behavior demonstrable and debuggable on every run, instead of
  "sometimes you'll see it handle a failure."
- **Explicit scratchpad tool (`save_fact`/`get_facts`)** in addition to the model's own
  context window — gives an inspectable audit trail of what the agent believes are the
  load-bearing facts, independent of the raw transcript.
- **`ref_N` element handles instead of CSS selectors** for the model to act on — much more
  reliable than asking an LLM to write/guess selectors against HTML it only partially sees.

## Known limitations

- `verify_outcome` is hardcoded to the internal-system record shape (company/invoice
  number/amount/due date) — not a generic "check any outcome" tool.
- No persistent cross-run memory — each `run.py` invocation starts the agent with a blank
  scratchpad (by design, for reproducible demos), though the mock Internal System's data
  does persist across the same run.
- Single-agent, single-browser-tab design — no parallel sub-tasks.
- Error recovery is LLM-judgment-based (guided by the system prompt's "stop after 3
  identical retries" rule) rather than a formally verified retry/backoff policy.
- The mock environment is intentionally small (4 invoices, 3 companies, one flaky
  endpoint) — it's meant to exercise the full loop clearly, not to be a realistic-scale
  business system.
- No sandboxing of the browser beyond Playwright's own isolation; this is fine for a local
  mock app but would need real sandboxing/permissioning before pointing an agent like this
  at arbitrary real websites.

## What I'd build next with more time

- A generic, pluggable verification layer (e.g. a small DSL or a second "verifier" LLM
  call that checks task completion against the original goal, rather than one hardcoded
  tool per environment).
- Parallel/sub-task execution for multi-part goals.
- A richer memory system (vector/semantic recall across many past runs, not just a
  per-run scratchpad).
- Human-in-the-loop approval gates for higher-stakes actions (e.g. "pause before
  submitting any payment-related form over $X" as a policy, not just model judgment).
- A small eval harness: a fixed set of tasks (including ones designed to require a retry,
  ones requiring `ask_user`, and ones with no valid path) run automatically against the
  agent to track autonomy/reliability/verification regressions as the prompt/tools change.
- Replacing the DOM `ref_N` snapshot with a full accessibility-tree read for more complex,
  less controlled real-world pages.

## Assumptions made

- "Tell me once it is done" means: verify against the system's actual data before
  reporting success, not just reporting after the final click.
- A single deterministic transient failure per invoice submission is a reasonable stand-in
  for "the kind of flakiness a real backend/API exhibits," without needing to simulate full
  network conditions.
- It's acceptable (per the JD) to restrict the prototype to a self-contained simulated
  company environment rather than a real, unpredictable third-party website, in exchange
  for the prototype being genuinely reliable rather than mostly mocked.

## Models / APIs / frameworks used

- **Claude (Anthropic Messages API, tool use)** — the agent's reasoning/planning/tool-
  selection, including vision for reading the invoice images. Model id is configurable via
  `ANTHROPIC_MODEL` in `.env` (defaults to `claude-sonnet-5`).
- **Playwright (Python, Chromium)** — browser automation.
- **Flask + SQLite** — the mock company environment (Inbox + Internal System).
- **Pillow** — renders the invoice "scan" images.
- No other third-party agent frameworks (no LangChain/AutoGPT/etc.) — the loop is written
  directly against the Anthropic API so the full control flow (retries, verification
  gating, ask_user handling) is visible in `agent/agent_loop.py` rather than hidden inside
  a framework.

## Demo / known-good run

See `logs/` for a saved transcript + screenshots from a real run (added after end-to-end
testing). [Demo video / live demo link to be added here.]
