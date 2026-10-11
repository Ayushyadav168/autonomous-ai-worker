"""
Renders each seed invoice as a PNG "scanned document" image.

This is the only place the amount/due_date ever appear as pixels instead of
text, so the agent must use vision (browser_view_image) to read them -
mirroring a real invoice PDF/scan rather than a conveniently structured API.

Run directly: python3 -m mock_env.generate_invoices
"""
import os

from PIL import Image, ImageDraw, ImageFont

from .seed_data import INVOICES

OUT_DIR = os.path.join(os.path.dirname(__file__), "static", "invoices")


def _font(size):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_invoice(inv: dict, out_path: str):
    width, height = 800, 1000
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    title_font = _font(34)
    header_font = _font(20)
    body_font = _font(18)
    big_font = _font(26)

    y = 40
    draw.text((40, y), inv["company"], fill="black", font=title_font)
    y += 50
    draw.text((40, y), "INVOICE", fill=(80, 80, 80), font=header_font)
    y += 40
    draw.line((40, y, 760, y), fill=(200, 200, 200), width=2)
    y += 30

    rows = [
        ("Invoice Number:", inv["invoice_number"]),
        ("Date Received:", inv["date_received"]),
        ("Bill To:", "Our Company Inc."),
    ]
    for label, value in rows:
        draw.text((40, y), label, fill=(60, 60, 60), font=body_font)
        draw.text((260, y), value, fill="black", font=body_font)
        y += 36

    y += 20
    draw.line((40, y, 760, y), fill=(200, 200, 200), width=2)
    y += 30

    draw.text((40, y), "Amount Due:", fill=(60, 60, 60), font=header_font)
    draw.text((260, y), f"${inv['amount']}", fill="black", font=big_font)
    y += 50

    draw.text((40, y), "Payment Due Date:", fill=(60, 60, 60), font=header_font)
    draw.text((260, y), inv["due_date"], fill="black", font=big_font)
    y += 60

    draw.line((40, y, 760, y), fill=(200, 200, 200), width=2)
    y += 30
    draw.text(
        (40, y),
        "Thank you for your business.",
        fill=(120, 120, 120),
        font=body_font,
    )

    os.makedirs(OUT_DIR, exist_ok=True)
    img.save(out_path)


def generate_all():
    os.makedirs(OUT_DIR, exist_ok=True)
    for inv in INVOICES:
        out_path = os.path.join(OUT_DIR, f"{inv['id']}.png")
        render_invoice(inv, out_path)
        print(f"wrote {out_path}")


if __name__ == "__main__":
    generate_all()
