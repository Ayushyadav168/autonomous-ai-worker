"""
Simulated company environment: a mock "Inbox" (where invoices arrive) and a
mock "Internal System" (where billing records must be entered).

Design notes for reviewers:
- The amount/due_date for each invoice only exist as pixels in a rendered
  image, not as text/HTML/JSON anywhere - the agent must look at the
  "attachment" like a real scanned invoice.
- POST /system/add deliberately fails the FIRST submission for any given
  invoice_number (simulated transient backend error), then succeeds on
  retry. This gives a deterministic, reproducible failure/retry scenario.
- GET /api/records is the ground-truth JSON source the agent's verification
  tool queries - independent of whatever the UI/DOM claims happened.
"""
import os
import sqlite3
import time

from flask import Flask, g, redirect, render_template, request, url_for

from .seed_data import INVOICES, get_invoice

APP_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(APP_DIR, "internal_system.db")

app = Flask(__name__)

# Tracks how many times each invoice_number has been submitted, so we can
# fail the first attempt deterministically (simulated flaky backend).
_submit_attempts = {}


def get_db():
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
    return db


@app.teardown_appcontext
def close_db(exception):
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


def init_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            invoice_number TEXT NOT NULL,
            amount TEXT NOT NULL,
            due_date TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()
    _submit_attempts.clear()


@app.route("/")
def home():
    return redirect(url_for("inbox"))


# ---------------------------------------------------------------- Inbox ---
@app.route("/inbox")
def inbox():
    return render_template("inbox.html", invoices=INVOICES)


@app.route("/inbox/<int:invoice_id>")
def inbox_detail(invoice_id):
    inv = get_invoice(invoice_id)
    if inv is None:
        return "Not found", 404
    return render_template("inbox_detail.html", inv=inv)


# --------------------------------------------------------- Internal system ---
@app.route("/system")
def system_home():
    db = get_db()
    records = db.execute("SELECT * FROM records ORDER BY id DESC").fetchall()
    return render_template("system_list.html", records=records)


@app.route("/system/add", methods=["GET", "POST"])
def system_add():
    if request.method == "GET":
        return render_template("system_add.html", error=None, values={})

    company = request.form.get("company", "").strip()
    invoice_number = request.form.get("invoice_number", "").strip()
    amount = request.form.get("amount", "").strip()
    due_date = request.form.get("due_date", "").strip()
    values = {
        "company": company,
        "invoice_number": invoice_number,
        "amount": amount,
        "due_date": due_date,
    }

    if not (company and invoice_number and amount and due_date):
        return render_template(
            "system_add.html", error="All fields are required.", values=values
        )

    attempts = _submit_attempts.get(invoice_number, 0) + 1
    _submit_attempts[invoice_number] = attempts

    if attempts == 1:
        # Simulated transient backend failure on the first attempt.
        return render_template(
            "system_add.html",
            error=(
                "Submission failed: internal service temporarily unavailable "
                "(error 503). Please try again."
            ),
            values=values,
        )

    db = get_db()
    db.execute(
        "INSERT INTO records (company, invoice_number, amount, due_date, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (company, invoice_number, amount, due_date, time.strftime("%Y-%m-%d %H:%M:%S")),
    )
    db.commit()
    return redirect(url_for("system_home"))


# ------------------------------------------------------------- Verify API ---
@app.route("/api/records")
def api_records():
    db = get_db()
    records = db.execute("SELECT * FROM records ORDER BY id DESC").fetchall()
    return {
        "records": [
            {
                "id": r["id"],
                "company": r["company"],
                "invoice_number": r["invoice_number"],
                "amount": r["amount"],
                "due_date": r["due_date"],
                "created_at": r["created_at"],
            }
            for r in records
        ]
    }


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Resets the internal system DB and flaky-submit counters (test/demo use)."""
    init_db()
    return {"status": "reset"}


if __name__ == "__main__":
    init_db()
    app.run(port=5000, debug=False)
