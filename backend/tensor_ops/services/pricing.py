"""Price book from the TensorInfoSec business plan (deck slides 11, 17, 18, 19). Edit here, not in code paths.

Auditor fees are deliberately unset: the "all-in quote" must use the partner audit firm's actual price list.
Until set, quotes show the auditor fee as "to be confirmed" and say so.
"""
from __future__ import annotations

from typing import Any

PRICE_BOOK: dict[str, Any] = {
    "currency": "USD",
    "audit_ready_year": [  # (max employees, price per year)
        {"max_employees": 50, "price": 7500},
        {"max_employees": 150, "price": 10500},
    ],
    "mid_market_custom_from": 151,
    "founding_client_price": 5000,
    "founding_client_slots": 5,
    "additional_framework": {"default": 4000, "ISO42001": 5000, "PCIDSS": 5000},
    "pentest_addon": 5000,
    "stay_compliant_monthly": {"essentials": 750, "core": 1500, "plus": 2500},
    "white_label_per_client_year": 4500,
    "payment_terms": "50% at signing, 50% when audit-ready. If Tensor misses the agreed audit-ready date "
                     "because of Tensor, the first 50% is refunded.",
    "guarantee": "If the independent auditor finds gaps in Tensor's work, Tensor fixes them free until the audit passes.",
    # Set these from your partner auditor's price list (USD). None = not configured.
    "auditor_fee_estimate": {"SOC2_TYPE1": None, "SOC2_TYPE2": None, "ISO27001": None, "ISO42001": None, "PCIDSS": None},
}


def base_price(employee_count: int | None, founding: bool = False, partner_price: float | None = None) -> dict[str, Any]:
    if partner_price is not None:
        return {"label": "Audit-Ready Year (white-label)", "price": partner_price, "custom": False}
    if founding:
        return {"label": "Founding Client Program", "price": PRICE_BOOK["founding_client_price"], "custom": False}
    n = employee_count or 1
    for tier in PRICE_BOOK["audit_ready_year"]:
        if n <= tier["max_employees"]:
            return {"label": f"Audit-Ready Year (up to {tier['max_employees']} staff)", "price": tier["price"],
                    "custom": False}
    return {"label": "Mid-market program (custom quote)", "price": None, "custom": True}


def quote(employee_count: int | None, frameworks: list[str], *, founding: bool = False, pentest: bool = False,
          soc2_type: str = "SOC2_TYPE1", partner_price: float | None = None) -> dict[str, Any]:
    fws = [f for f in frameworks if f] or ["SOC2"]
    base = base_price(employee_count, founding, partner_price)
    lines = [{"item": base["label"] + f" — includes {fws[0]}", "amount": base["price"]}]
    for f in fws[1:]:
        amt = PRICE_BOOK["additional_framework"].get(f, PRICE_BOOK["additional_framework"]["default"])
        lines.append({"item": f"Additional framework: {f}", "amount": amt})
    if pentest:
        lines.append({"item": "Annual penetration test (independent tester)", "amount": PRICE_BOOK["pentest_addon"]})
    tensor_total = None if base["custom"] else sum(x["amount"] for x in lines)
    fee_keys = [soc2_type if f == "SOC2" else f for f in fws]
    fees = {k: PRICE_BOOK["auditor_fee_estimate"].get(k) for k in fee_keys}
    fees_known = all(v is not None for v in fees.values())
    auditor_total = sum(v for v in fees.values() if v is not None) if fees_known else None
    return {
        "currency": PRICE_BOOK["currency"],
        "lines": lines,
        "tensor_total_year1": tensor_total,
        "auditor_fee_estimates": fees,
        "auditor_fee_status": "estimated from partner price list" if fees_known else "to be confirmed with the audit firm",
        "all_in_total_year1": (tensor_total + auditor_total) if (tensor_total is not None and auditor_total is not None) else None,
        "payment_terms": PRICE_BOOK["payment_terms"],
        "guarantee": PRICE_BOOK["guarantee"],
        "note": "The independent audit firm issues the report and is paid by the client directly. Tensor never certifies its own work.",
        "custom_quote_required": base["custom"],
    }
