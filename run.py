#!/usr/bin/env python3
"""
CLI entrypoint: boots the mock company environment, runs the autonomous
agent on a natural-language task, and prints/saves a summary + evidence.

Usage:
    python run.py "Find the latest invoice from Acme Corp, extract the amount
    and due date, enter it into our internal system, and tell me once it is done."

    python run.py --show                     # watch the browser (non-headless)
    python run.py --task-file tasks/example2.txt
"""
import argparse
import os
import socket
import subprocess
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

from agent.agent_loop import run_agent, save_transcript
from agent.browser import BrowserController
from agent.memory import Memory
from agent.tools import ToolExecutor
from mock_env.generate_invoices import generate_all

DEFAULT_TASK = (
    "Find the latest invoice from Acme Corp, extract the amount and due date from "
    "the invoice attachment, enter it into our internal system, and tell me once it "
    "is done."
)

BASE_URL = os.environ.get("MOCK_ENV_URL", "http://127.0.0.1:5050")
PORT = int(BASE_URL.rsplit(":", 1)[1])


def _port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def start_mock_server():
    if _port_in_use(PORT):
        print(f"[setup] Mock env already running on port {PORT}, resetting state...")
        requests.post(f"{BASE_URL}/api/reset", timeout=5)
        return None

    print(f"[setup] Generating invoice images...")
    generate_all()

    print(f"[setup] Starting mock company environment on port {PORT}...")
    env = os.environ.copy()
    env["FLASK_RUN_PORT"] = str(PORT)
    proc = subprocess.Popen(
        [sys.executable, "-c", (
            "from mock_env.app import app, init_db; "
            f"init_db(); app.run(port={PORT}, debug=False)"
        )],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    for _ in range(50):
        if _port_in_use(PORT):
            break
        time.sleep(0.2)
    else:
        raise RuntimeError("Mock environment server failed to start.")
    return proc


def main():
    parser = argparse.ArgumentParser(description="Run the autonomous AI task worker.")
    parser.add_argument("task", nargs="?", default=None, help="Natural language task")
    parser.add_argument("--task-file", default=None, help="Read the task from a text file")
    parser.add_argument("--show", action="store_true", help="Run browser non-headless (watch it work)")
    args = parser.parse_args()

    if args.task_file:
        with open(args.task_file) as f:
            task = f.read().strip()
    else:
        task = args.task or DEFAULT_TASK

    if "ANTHROPIC_API_KEY" not in os.environ:
        print("ERROR: set ANTHROPIC_API_KEY in your environment (see .env.example).")
        sys.exit(1)

    server_proc = start_mock_server()

    run_id = time.strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join("logs", f"run_{run_id}")
    os.makedirs(log_dir, exist_ok=True)

    browser = BrowserController(headless=not args.show, screenshot_dir=os.path.join(log_dir, "screenshots"))
    browser.start()
    memory = Memory(path=os.path.join(log_dir, "memory.json"))
    executor = ToolExecutor(browser=browser, memory=memory, base_url=BASE_URL)

    print(f"\n{'=' * 70}\nTASK: {task}\n{'=' * 70}")

    try:
        result = run_agent(task, executor)
    finally:
        final_shot = browser.screenshot("final_state")
        browser.stop()
        if server_proc:
            server_proc.terminate()

    save_transcript(result.transcript, os.path.join(log_dir, "transcript.json"))

    # Independent sanity check: print the internal system's actual data,
    # regardless of what the agent itself claimed, as human-reviewable evidence.
    try:
        actual_records = requests.get(f"{BASE_URL}/api/records", timeout=5).json()
    except Exception:
        actual_records = {"records": "<unavailable - server already stopped>"}

    print(f"\n{'=' * 70}\nFINAL REPORT\n{'=' * 70}")
    print(f"Status: {result.status}")
    print(f"Success (agent-reported, must be backed by verify_outcome): {result.success}")
    print(f"Summary: {result.summary}")
    if result.evidence:
        print(f"Evidence cited by agent: {result.evidence}")
    if result.question:
        print(f"Clarification needed: {result.question}")
    print(f"\nIndependent check - actual internal system records right now:")
    print(actual_records)
    print(f"\nFull transcript saved to: {log_dir}/transcript.json")
    print(f"Final screenshot: {final_shot}")
    print(f"{'=' * 70}\n")


if __name__ == "__main__":
    main()
