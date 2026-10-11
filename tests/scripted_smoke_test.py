"""
Plumbing smoke test: drives the REAL browser against the REAL mock Flask app
through every tool in agent/tools.py, with a hardcoded action sequence
standing in for Claude's reasoning (no LLM calls, no API key needed).

This exists to separate two different risks before a live run:
  1. "Does the mechanical pipeline work?" (browser automation, the flaky
     retry endpoint, the verification query against ground truth) - this
     script proves that, deterministically, every time.
  2. "Does Claude reason well enough to drive it autonomously?" - that can
     only be answered by an actual live run (`python run.py`), which needs
     a funded ANTHROPIC_API_KEY.

If this script passes but a live run fails, the bug is in prompting/agent
behavior, not in the tools/environment - a useful debugging signal either
way. It intentionally reads the ground-truth amount/due_date from
seed_data.py rather than from the rendered image (no OCR here) - it still
calls browser_view_image to prove that tool path works end to end.

Run: python3 -m tests.scripted_smoke_test
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.browser import BrowserController
from agent.memory import Memory
from agent.tools import ToolExecutor, ImageToolResult
from mock_env.generate_invoices import generate_all
from mock_env.seed_data import latest_invoice_for_company

PORT = 5099
BASE_URL = f"http://127.0.0.1:{PORT}"


def find_ref_by_name(read_page_text: str, name: str) -> str:
    for line in read_page_text.splitlines():
        if f"name={name}" in line:
            return line.strip().split("]")[0].lstrip("[")
    raise AssertionError(f"No input with name={name} found in:\n{read_page_text}")


def find_refs_by_text(read_page_text: str, substring: str):
    matches = []
    for line in read_page_text.splitlines():
        line = line.strip()
        if line.startswith("[ref_") and substring in line:
            ref = line.split("]")[0].lstrip("[")
            matches.append((ref, line))
    return matches


def step(label):
    print(f"\n--- {label} ---")


def main():
    generate_all()

    from mock_env.app import app, init_db

    init_db()
    import threading

    server_thread = threading.Thread(
        target=lambda: app.run(port=PORT, debug=False, use_reloader=False), daemon=True
    )
    server_thread.start()
    time.sleep(1)

    browser = BrowserController(headless=True, screenshot_dir="logs/smoke_test_screenshots")
    browser.start()
    memory = Memory(path="logs/smoke_test_memory.json")
    executor = ToolExecutor(browser=browser, memory=memory, base_url=BASE_URL)

    def call(name, **kwargs):
        result, is_error = executor.execute(name, kwargs)
        assert not is_error, f"{name}({kwargs}) FAILED: {result}"
        return result

    ground_truth = latest_invoice_for_company("Acme Corp")
    print(f"Ground truth (from seed data, for assertions only): {ground_truth}")

    try:
        step("1. Navigate to inbox")
        print(call("browser_navigate", url=f"{BASE_URL}/inbox"))

        step("2. Read inbox page, find Acme Corp rows, pick latest by date")
        page_text = call("browser_read_page")
        print(page_text)
        acme_rows = find_refs_by_text(page_text, "Acme Corp")
        assert len(acme_rows) == 2, f"expected 2 Acme Corp rows, found {len(acme_rows)}"
        # pick the row whose text contains the ground-truth invoice number (the latest one)
        target_ref = None
        for ref, line in acme_rows:
            if ground_truth["invoice_number"] in line:
                target_ref = ref
        assert target_ref, "could not find the latest Acme invoice row"
        print(f"Selected ref {target_ref} as latest Acme Corp invoice")

        step("3. Click into the invoice detail page")
        print(call("browser_click", ref=target_ref))

        step("4. Read detail page, find the attachment image, view it")
        page_text = call("browser_read_page")
        print(page_text)
        img_refs = find_refs_by_text(page_text, "<img>")
        assert img_refs, "no image element found on invoice detail page"
        img_ref = img_refs[0][0]
        result, is_error = executor.execute("browser_view_image", {"ref": img_ref})
        assert not is_error and isinstance(result, ImageToolResult), "browser_view_image failed"
        assert len(result.image_bytes) > 1000, "image bytes suspiciously small"
        print(f"Viewed image at {img_ref}: {len(result.image_bytes)} bytes PNG")

        step("5. Save extracted facts to memory")
        print(call("save_fact", key="amount", value=ground_truth["amount"]))
        print(call("save_fact", key="due_date", value=ground_truth["due_date"]))
        print(call("get_facts"))

        step("6. Navigate to internal system add-record form")
        print(call("browser_navigate", url=f"{BASE_URL}/system/add"))

        step("7. Read form, fill in all fields")
        page_text = call("browser_read_page")
        company_ref = find_ref_by_name(page_text, "company")
        invoice_ref = find_ref_by_name(page_text, "invoice_number")
        amount_ref = find_ref_by_name(page_text, "amount")
        due_date_ref = find_ref_by_name(page_text, "due_date")
        call("browser_type", ref=company_ref, text="Acme Corp")
        call("browser_type", ref=invoice_ref, text=ground_truth["invoice_number"])
        call("browser_type", ref=amount_ref, text=ground_truth["amount"])
        call("browser_type", ref=due_date_ref, text=ground_truth["due_date"])

        step("8. Submit (1st attempt - mock env must reject this one)")
        page_text = call("browser_read_page")
        submit_ref = find_refs_by_text(page_text, "submit-record")[0][0] if find_refs_by_text(page_text, "submit-record") else None
        if not submit_ref:
            # fall back: button text is "Submit"
            submit_ref = find_refs_by_text(page_text, "Submit")[0][0]
        call("browser_click", ref=submit_ref)
        page_text = call("browser_read_page")
        assert "Submission failed" in page_text, (
            "expected the deliberate first-attempt failure banner, did not see it:\n" + page_text
        )
        print("Confirmed: first submission deterministically failed as designed.")

        step("9. Retry submit (2nd attempt - must succeed)")
        submit_refs = find_refs_by_text(page_text, "Submit")
        assert submit_refs, "could not find submit button on retry"
        call("browser_click", ref=submit_refs[0][0])
        page_text = call("browser_read_page")
        assert "Submission failed" not in page_text, "second attempt should have succeeded"
        assert ground_truth["amount"] in page_text, "submitted amount not visible in records list"
        print("Confirmed: retry succeeded, record now visible in internal system list.")

        step("10. Independently verify via verify_outcome tool")
        result = call(
            "verify_outcome",
            company="Acme Corp",
            invoice_number=ground_truth["invoice_number"],
            expected_amount=ground_truth["amount"],
            expected_due_date=ground_truth["due_date"],
        )
        print(result)
        assert result.startswith("VERIFIED"), f"verification did not confirm a match: {result}"

        print("\n" + "=" * 70)
        print("ALL PLUMBING CHECKS PASSED")
        print("Tools, browser automation, flaky-retry handling, and ground-truth")
        print("verification all work correctly end to end.")
        print("=" * 70)
    finally:
        browser.stop()


if __name__ == "__main__":
    main()
