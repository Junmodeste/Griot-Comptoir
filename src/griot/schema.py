"""Structured shape of one handwritten ledger page.

The same pydantic models drive the JSON schema sent to Muse Spark
(constrained decoding) and the validation of its answer.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class EntryType(str, Enum):
    sale = "sale"  # vente au comptant
    debt = "debt"  # vente à crédit ("crédit", "doit")
    repayment = "repayment"  # remboursement d'une dette
    purchase = "purchase"  # achat / réapprovisionnement
    other = "other"


class Legibility(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class LedgerLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str = Field(description="The line exactly as written, abbreviations and nouchi kept.")
    entry_type: EntryType
    product: str | None = Field(description="Product name, expanded and normalised in French.")
    quantity: float | None
    unit: str | None = Field(description="Unit as written: sac, kg, boîte, paquet, bidon, tas, pièce...")
    unit_price: float | None = Field(description="Price of one unit in XOF.")
    total: float | None = Field(description="Line total in XOF, as written on the page.")
    customer: str | None = Field(description="Customer or supplier name, as written.")
    date: dt.date | None
    legibility: Legibility = Field(description="How clearly the line could be read.")


class LedgerPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_date: dt.date | None = Field(description="Date written at the top of the page, if any.")
    currency: str = Field(description="ISO currency code, XOF unless the page says otherwise.")
    lines: list[LedgerLine]
    notes: str | None = Field(description="Anything on the page that is not a ledger line.")


def json_schema_format(model: type[BaseModel] = LedgerPage) -> dict:
    """`response_format` payload for Meta Model API structured output."""
    return {
        "type": "json_schema",
        "json_schema": {"name": model.__name__, "schema": model.model_json_schema()},
    }
