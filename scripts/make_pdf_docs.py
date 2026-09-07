"""
    fpdf2 in not in requirements.txt because it is not run time dependency..
    installed for data creation then removed
    pip install fpdf2
    python scripts/make_pdf_docs.py
    pip uninstall -y fpdf2
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from fpdf import FPDF

from generate_warehouse_csvs import PLANS


CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)

ROWS = [
    ("Individual deductible", 3, "${:,.0f}"),
    ("Family deductible", 4, "${:,.0f}"),
    ("Individual out-of-pocket maximum", 5, "${:,.0f}"),
    ("Coinsurance after deductible", 6, "{:.0%}"),
    ("Primary care visit copay", 7, "${:.0f}"),
    ("Specialist visit copay", 8, "${:.0f}"),
    ("Emergency room copay", 9, "${:.0f}"),
    ("Urgent care copay", 10, "${:.0f}"),
    ("Generic drug copay", 11, "${:.0f}"),
    ("Brand drug copay", 12, "${:.0f}"),
    ("Monthly premium", 13, "${:,.2f}"),
]


def build(out_path: Path) -> None:
    pdf = FPDF()
    pdf.set_creation_date(CREATED)
    pdf.set_title("Cascade Health Plan - Summary of Benefits and Coverage")
    pdf.set_author("Cascade Health Plan")

    for plan in PLANS:
        plan_id, name, tier = plan[0], plan[1], plan[2]
        pdf.add_page()

        pdf.set_font("Helvetica", "B", 15)
        pdf.multi_cell(0, 9, "Summary of Benefits and Coverage", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "B", 12)
        pdf.multi_cell(0, 7, f"{name}  ({plan_id})", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(
            0,
            6,
            f"Metal tier: {tier}   |   Plan year 2026   |   In-network figures",
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.ln(3)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(120, 8, "What you pay", border="B")
        pdf.cell(0, 8, "In-network", border="B")
        pdf.ln(9)

        pdf.set_font("Helvetica", "", 10)
        for label, idx, fmt in ROWS:
            pdf.cell(120, 7, label)
            pdf.cell(0, 7, fmt.format(plan[idx]))
            pdf.ln(7)

        pdf.ln(4)
        pdf.set_font("Helvetica", "I", 9)
        pdf.multi_cell(
            0,
            5,
            new_x="LMARGIN",
            new_y="NEXT",
            text="Preventive care is covered at 100% with no cost sharing and is not "
            "subject to the deductible. Copays do not count toward the deductible "
            "but do count toward the out-of-pocket maximum. Out-of-network care is "
            "subject to a separate, higher deductible and is not shown here. "
            "This summary is not a contract; the Evidence of Coverage governs.",
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(out_path))
    print(f"wrote {out_path}  ({out_path.stat().st_size:,} bytes, {len(PLANS)} pages)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/docs/summary-of-benefits-coverage.pdf")
    args = parser.parse_args()
    build(Path(args.out))


if __name__ == "__main__":
    main()
