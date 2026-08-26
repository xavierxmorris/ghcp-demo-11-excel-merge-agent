"""Generate realistic ESIT / SVP query exports, including the messy bits.

Deliberately reproduces the failure modes that break hand-driven Excel work:
  * different header spellings in each environment
  * a duplicate request from a re-run query
  * amounts stored as text with currency symbols and thousands separators
  * genuine data drift between the two environments
  * rows present in one environment only
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
INBOX = HERE.parent / "inbox"

ESIT_ROWS = [
    # Request ID, Customer, Status, Amount (as text, like a real export), CCY, Owner, Region, Submitted
    ("REQ-1001", "Northwind Traders", "APPROVED", "12,500.00", "GBP", "A.Chen@contoso.com", "EMEA", "2026-07-01 09:14"),
    ("REQ-1002", "Fabrikam Ltd", "PENDING", "3,200.50", "GBP", "b.singh@contoso.com", "EMEA", "2026-07-01 10:02"),
    ("REQ-1003", "Contoso Retail", "APPROVED", "48,000.00", "USD", "c.lopez@contoso.com", "AMER", "2026-07-02 11:30"),
    ("REQ-1004", "Tailspin Toys", "REJECTED", "980.00", "USD", "d.novak@contoso.com", "AMER", "2026-07-02 14:45"),
    ("REQ-1005", "Adventure Works", "APPROVED", "$7,650.25", "USD", "e.mori@contoso.com", "APAC", "2026-07-03 08:20"),
    ("REQ-1006", "Wingtip Toys", "CANCELLED", "1,100.00", "EUR", "f.dubois@contoso.com", "EMEA", "2026-07-03 09:05"),
    ("REQ-1007", "Litware Inc", "PENDING", "22,400.00", "EUR", "g.rossi@contoso.com", "EMEA", "2026-07-04 16:10"),
    ("REQ-1008", "Proseware", "APPROVED", "5,000.00", "GBP", "h.patel@contoso.com", "EMEA", "2026-07-05 12:00"),
    # Duplicate of 1002 from a re-run query - later timestamp, corrected amount.
    ("REQ-1002", "Fabrikam Ltd", "APPROVED", "3,250.00", "GBP", "b.singh@contoso.com", "EMEA", "2026-07-06 08:00"),
    # Exists only in ESIT.
    ("REQ-1009", "Coho Vineyard", "PENDING", "15,750.00", "USD", "i.zhang@contoso.com", "AMER", "2026-07-06 13:22"),
]

SVP_ROWS = [
    ("REQ-1001", "Northwind Traders", "APPROVED", "12500", "GBP", "A.Chen@contoso.com", "EMEA", "2026-07-01 09:14"),
    ("REQ-1002", "Fabrikam Ltd", "APPROVED", "3250", "GBP", "b.singh@contoso.com", "EMEA", "2026-07-06 08:00"),
    # Amount drift - the case reconciliation exists to catch.
    ("REQ-1003", "Contoso Retail", "APPROVED", "47500", "USD", "c.lopez@contoso.com", "AMER", "2026-07-02 11:30"),
    ("REQ-1004", "Tailspin Toys", "REJECTED", "980", "USD", "d.novak@contoso.com", "AMER", "2026-07-02 14:45"),
    # Status drift.
    ("REQ-1005", "Adventure Works", "PENDING", "7650.25", "USD", "e.mori@contoso.com", "APAC", "2026-07-03 08:20"),
    ("REQ-1006", "Wingtip Toys", "CANCELLED", "1100", "EUR", "f.dubois@contoso.com", "EMEA", "2026-07-03 09:05"),
    # Owner drift.
    ("REQ-1007", "Litware Inc", "PENDING", "22400", "EUR", "g.ROSSI@contoso.com", "EMEA", "2026-07-04 16:10"),
    ("REQ-1008", "Proseware", "APPROVED", "5000", "GBP", "h.patel@contoso.com", "EMEA", "2026-07-05 12:00"),
    # Exists only in SVP.
    ("REQ-1010", "Trey Research", "APPROVED", "9100", "GBP", "j.olsen@contoso.com", "EMEA", "2026-07-07 09:45"),
]

# The two environments spell their headers differently. That is the point.
ESIT_HEADERS = ["Request ID", "Customer Name", "Status", "Total Amount", "Currency", "Owner Email", "Region", "Submitted At"]
SVP_HEADERS = ["REQUEST_ID", "CUST_NAME", "REQ_STATUS", "AMT", "CCY", "assigned_to", "geo", "SUBMIT_DT"]

# "We rewrote the SVP query again this morning." These headers are deliberately
# NOT in config/schema.yaml, so `xlflow validate` must reject them.
DRIFT_RENAMES = {"AMT": "NET_AMOUNT_EX_VAT", "assigned_to": "Approver"}
DRIFT_NEW_COLUMNS = {"Legal Entity": "LE-0099"}


def write_samples(inbox: Path = INBOX, drift: bool = False, additive_only: bool = False) -> tuple[Path, Path]:
    """Write the two exports.

    drift=True         simulates 'we rewrote the SQL': compared columns are renamed
                       AND a new column appears.
    additive_only=True simulates the benign case: the query gained an extra column
                       but nothing the pipeline relies on was renamed.
    """
    inbox.mkdir(parents=True, exist_ok=True)
    esit_path, svp_path = inbox / "esit.xlsx", inbox / "svp.xlsx"

    esit = pd.DataFrame(ESIT_ROWS, columns=ESIT_HEADERS)
    svp = pd.DataFrame(SVP_ROWS, columns=SVP_HEADERS)

    if drift:
        svp = svp.rename(columns=DRIFT_RENAMES)
    if drift or additive_only:
        for column, value in DRIFT_NEW_COLUMNS.items():
            svp[column] = value

    esit.to_excel(esit_path, index=False, sheet_name="Query Result")
    svp.to_excel(svp_path, index=False, sheet_name="Query Result")
    return esit_path, svp_path


if __name__ == "__main__":
    import sys

    drift = "--drift" in sys.argv
    for path in write_samples(drift=drift):
        print(f"wrote {path}{' (drifted headers)' if drift else ''}")
