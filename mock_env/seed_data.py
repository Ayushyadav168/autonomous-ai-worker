"""
Ground-truth data for the simulated company environment.

Each invoice has a ground-truth amount/due_date that is ONLY ever rendered
into an image (see generate_invoices.py). It is intentionally not exposed
through any text API, so an agent is forced to actually look at the
"attachment" (like a real invoice PDF/scan) rather than reading a field.
"""

INVOICES = [
    {
        "id": 1,
        "company": "Acme Corp",
        "sender_email": "billing@acmecorp.example",
        "invoice_number": "INV-1042",
        "subject": "Invoice INV-1042 for September services",
        "date_received": "2026-09-30",
        "amount": "4,250.00",
        "due_date": "2026-10-15",
    },
    {
        "id": 2,
        "company": "Acme Corp",
        "sender_email": "billing@acmecorp.example",
        "invoice_number": "INV-1098",
        "subject": "Invoice INV-1098 for October services",
        "date_received": "2026-10-09",
        "amount": "5,100.00",
        "due_date": "2026-10-24",
    },
    {
        "id": 3,
        "company": "Nimbus Traders",
        "sender_email": "accounts@nimbustraders.example",
        "invoice_number": "NT-7731",
        "subject": "Invoice NT-7731 - Q3 Supply Order",
        "date_received": "2026-10-02",
        "amount": "12,840.50",
        "due_date": "2026-10-20",
    },
    {
        "id": 4,
        "company": "Zenith Logistics",
        "sender_email": "finance@zenithlogistics.example",
        "invoice_number": "ZL-2291",
        "subject": "Invoice ZL-2291 - Freight Services",
        "date_received": "2026-10-05",
        "amount": "890.00",
        "due_date": "2026-10-18",
    },
]


def get_invoice(invoice_id: int):
    for inv in INVOICES:
        if inv["id"] == invoice_id:
            return inv
    return None


def get_invoices_by_company(company: str):
    return [inv for inv in INVOICES if inv["company"].lower() == company.lower()]


def latest_invoice_for_company(company: str):
    invs = get_invoices_by_company(company)
    if not invs:
        return None
    return max(invs, key=lambda inv: inv["date_received"])
