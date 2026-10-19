"""Extract a distributor stock statement into StockStatementModelV2 with Hawser.

Usage:
    export OPENROUTER_API_KEY=...        # or ANTHROPIC_API_KEY / OPENAI_API_KEY
    python extract_stock_statement.py statement.pdf [more files...] [-o out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

import hawser
from pydantic import BaseModel, Field


class StockItem(BaseModel):
   """One product row of the stock statement table.

   Columns usually appear left to right as: PRODUCT DESCRIPTION, PACKING,
   OPSTK (opening qty), PURCH (purchase qty), SALE (sale qty), SALE VAL
   (sale value), then closing STOCK and STK VAL (closing value).
   Tally-style 'Stock Group Summary' statements instead use: Inwards Quantity
   (= purchase), Outwards Quantity (= sale) and Closing Balance Quantity.

   Column headers often span TWO (or three) stacked lines. Two common forms:
   1. A wrapped name: 'Outwards' on one line with 'Quantity' below it is ONE
      column called 'Outwards Quantity', not two columns.
   2. A group row with sub-columns: a first line such as
      'OPENING   PURCHASE   SALES   CLOSING' with 'QTY  VALUE' repeated
      beneath each group. That is eight columns - opening qty, opening value,
      purchase qty, purchase value, sale qty, sale value, closing qty,
      closing value. Pair every QTY/VALUE sub-header with the group name
      printed above it.
   Always merge the stacked header lines into full column names FIRST, then
   assign each number to the merged column it sits under. Never treat header
   fragments as separate columns, and never skip a number because its header
   wraps onto another line.

   Read each number under the header it belongs to, not the nearest one.
   Quantities may carry a unit suffix such as '901 NOS' - extract just the
   number. Any column absent from the statement is 0.
   When the statement has only opening, purchase and sale columns, every row
   must satisfy: opening_qty + purchase_qty - sale_qty = closing_qty. When the
   statement also has return / replacement columns (see RETURN COLUMNS below),
   that identity does NOT hold on the extracted fields, because the returns are
   part of the printed arithmetic:
   OPENING STOCK + PURCHASES + SALE RETURN - SALES - PURCHASE RETURN
   (-/+ REPL./OTHERS) = CLOSING STOCK.
   Use the identity that fits the statement to check your column assignment, and
   NEVER move a return quantity into purchase_qty or sale_qty to make the simple
   three-column identity add up - the return columns are simply not extracted.

   Emit one entry per PRODUCT row only. Some tables start with a section
   header row containing just a company/division name (e.g. a manufacturer
   name) and no numbers of its own - do NOT emit an item for such rows, and
   do NOT let them absorb the numbers of the product row below them. Also
   skip 'Division Total', 'Total' and 'End Of Report' rows.

   RETURN COLUMNS: 'STOCK & SALES ANALYSIS' statements carry return and
   replacement columns, with the header split across two printed lines like
   this (line 1 above, line 2 below):

       ITEM DESCRIPTION  OPENING            SALE   REPL./  TOTAL          PURCHASE  REPL./  CLOSING  RE-
                         STOCK   PURCHASES  RETURN OTHERS  STOCK   SALES  RETURN    OTHERS  STOCK    ORDER

   Merged, those are eleven columns: item description, OPENING STOCK,
   PURCHASES, SALE RETURN, REPL./OTHERS (inward), TOTAL STOCK, SALES,
   PURCHASE RETURN, REPL./OTHERS (outward), CLOSING STOCK, RE-ORDER.
   Two traps in this layout:
   - 'SALE' on line 1 sits above 'RETURN' on line 2: together they are the
     single column SALE RETURN. It is NOT the sales column and NOT a second
     sales figure.
   - The real SALES column prints its header only on line 2, with nothing
     above it. sale_qty must come from THAT column - never from SALE RETURN,
     and never from PURCHASE RETURN or REPL./OTHERS.
   SALE RETURN, PURCHASE RETURN, REPL./OTHERS and TOTAL STOCK are NOT extracted
   - ignore those four columns completely. Only OPENING STOCK, PURCHASES, SALES
   and CLOSING STOCK are wanted, each read from its own column and reported
   exactly as printed.
   """
   product_description: str = Field(
      ..., title="Description of the item.",
      description="The product/medicine name from this row's first column. Must be an actual "
               "product, never a company, division or section header name."
   )
   packing: str = Field(
      ...,
      title="Packing unit of the item",
      description="Indicates the unit or packaging type, such as BOTTLE, VIAL, UNIT, 1X10, 5ML, etc."
   )
   opening_qty: int = Field(
      ..., title="Opening stock quantity of the item.",
      description="Column often labelled OPSTK, OP.STK, OPENING or OP QTY."
   )
   opening_value: float = Field(
      ..., title="Opening stock value in rupees of the item.",
      description="In grouped layouts this is the VALUE sub-column under the OPENING group "
               "header. Many statements have no opening value column; use 0 when absent."
   )
   purchase_qty: int = Field(
      ..., title="Incoming stock quantity (purchase units) of the item.",
      description="Column often labelled PURCH, PURCHASE, RECEIPTS or 'Inwards Quantity'. "
               "This is stock bought from the company, NOT the quantity sold - do not copy "
               "the SALE or 'Outwards Quantity' column here. It is 0 for many items."
   )
   purchase_value: float = Field(
      ..., title="Incoming stock value (purchase value) in rupees of the item.",
      description="Only fill this from a column explicitly labelled as purchase value, "
               "or the VALUE sub-column under a PURCHASE group header in grouped layouts. "
               "When the statement has no purchase value column, use 0. "
               "NEVER put the SALE VAL amount here."
   )
   sale_qty: int = Field(
      ..., title="Sales quantity (sale units) of the item.",
      description="Column labelled SALE, SALES or 'Outwards Quantity' (outwards = stock going "
               "out = sold). In Tally-style summaries the Outwards column is the sale "
               "quantity, never the purchase quantity. In 'STOCK & SALES ANALYSIS' layouts "
               "this is the SALES column that sits between TOTAL STOCK and PURCHASE RETURN - "
               "never the SALE RETURN column printed before TOTAL STOCK, and never a "
               "REPL./OTHERS column. Report the SALES figure as printed; do not net returns "
               "into it and do not add any return quantity to it."
   )
   sale_value: float = Field(
      ..., title="Sales value in rupees of the item.",
      description="Column labelled SALE VAL or SALE VALUE, or the VALUE sub-column under the "
               "SALES group header in grouped layouts. Belongs to sales, never to purchases "
               "and never to a SALE RETURN or PURCHASE RETURN column."
   )
   closing_qty: int = Field(
      ..., title="Closing stock quantity of the item.",
      description="Column often labelled STOCK, CL.STK, CLOSING or 'Closing Balance'. In "
               "'STOCK & SALES ANALYSIS' layouts it is the CLOSING STOCK column, never the "
               "TOTAL STOCK column that sits before SALES. When the statement has no return "
               "or replacement columns, check: opening_qty + purchase_qty - sale_qty must "
               "equal this value."
   )
   closing_value: float = Field(
      ..., title="Closing stock value in rupees of the item.",
      description="Column often labelled STK VAL, STOCK VAL or CLOSING VALUE, or the VALUE "
               "sub-column under the CLOSING group header in grouped layouts."
   )


class StockStatementModelV2(BaseModel):
   distributor_name: str = Field(..., title="Name of the distributor that sent the statement.")
   distributor_address_line: str = Field(..., title="Address of the distributor that sent the statement.")
   distributor_state: str = Field(..., title="State of the distributor that sent the statement.")
   distributor_city: str = Field(..., title="City of the distributor that sent the statement.")
   distributor_pincode: str = Field(..., title="Pincode of the distributor that sent the statement.")
   distributor_gstin: str = Field(...,
                                  title="GSTIN of the distributor that sent the statement. The GSTIN is in the format 11AAAAA1111A1Z1. Where 1 is a digit and A is an alphabet.",
                                  description="The value often wraps to the line below the 'GSTIN :' label - read it "
                                              "from there and transcribe all 15 characters exactly as printed.")
   distributor_pan: str = Field(..., title="PAN of the distributor that sent the statement.")
   distributor_contact: str = Field(..., title="Contact number of the distributor that sent the statement.")
   distributor_email: str = Field(..., title="Email address of the distributor that sent the statement.")
   distributor_website: str = Field(..., title="Website of the distributor that sent the statement.")
   distributor_dl_no: str = Field(..., title="DL (Drug License) number of the distributor that sent the statement.")
   distributor_fssai_no: str = Field(..., title="FSSAI number of the distributor that sent the statement.")
   start_date: str = Field(...,
                           title="Start date of the stock statement. Sometimes expressed as a starting month like 'Oct 2024'")
   end_date: str = Field(...,
                         title="End date of the stock statement. Sometimes expressed as an ending month like 'Oct 2024'")
   items: List[StockItem] = Field(
      ..., title="List of items in the stock statement.",
      description="One entry per product row of the table. Exclude section/group header rows "
               "(company or division names) and total/summary rows."
   )


INSTRUCTION = (
    "Extract the distributor details, the statement period and every product row from this "
    "stock statement. Follow the column-reading rules in the schema exactly: merge stacked "
    "header lines first, read each number under its own merged column, skip section-header "
    "and total rows, and never move a return quantity into purchase or sale."
)


def check_stock_identity(statement: StockStatementModelV2) -> list[str]:
    """Rows where opening + purchase - sale != closing.

    Only meaningful for statements without return/replacement columns; on a
    'STOCK & SALES ANALYSIS' layout a mismatch is expected, so treat these as hints.
    """
    problems = []
    for i, item in enumerate(statement.items):
        expected = item.opening_qty + item.purchase_qty - item.sale_qty
        if expected != item.closing_qty:
            problems.append(
                f"items[{i}] {item.product_description!r}: "
                f"{item.opening_qty} + {item.purchase_qty} - {item.sale_qty} = {expected}, "
                f"but closing_qty is {item.closing_qty}"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", help="Stock statement PDF(s) or image(s).")
    parser.add_argument("-o", "--out", help="Write the extracted JSON here (default: stdout).")
    parser.add_argument(
        "--model",
        default="openrouter/anthropic/claude-haiku-5.5",
        help="Registry model id; see `hawser models list`.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="Report rows where opening + purchase - sale != closing.",
    )
    args = parser.parse_args()

    result = hawser.run(
        INSTRUCTION,
        inputs=args.files,
        output_schema=StockStatementModelV2,
        models=args.model,
    )

    print(f"run {result.run_id}: {result.status}", file=sys.stderr)
    print(f"cost ${result.usage.totals.cost_usd:.4f}, {result.usage.totals.model_calls} model call(s)", file=sys.stderr)

    if result.status != "accepted":
        for issue in result.validation.issues:
            print(f"- [{issue.severity}] {issue.field}: {issue.message}", file=sys.stderr)
        print(f"review it with: hawser review show {result.run_id}", file=sys.stderr)
        if result.result is None:
            return 1

    statement = result.parse_as(StockStatementModelV2)

    if args.check:
        for problem in check_stock_identity(statement):
            print(f"! {problem}", file=sys.stderr)

    payload = statement.model_dump_json(indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
        print(f"wrote {args.out} ({len(statement.items)} items)", file=sys.stderr)
    else:
        print(payload)
    return 0 if result.status == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
