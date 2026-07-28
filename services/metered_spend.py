"""Metered-API spend tally — one JSONL row per paid generation call.

WHY (Mike, 2026-07-28): last month closed with ~$100 of Gemini and $50 of OpenAI
spend that nobody on the box could see until the bill arrived. Nothing here
watches metered keys — the usage-costs layer tracks subscription tokens, not
per-call metered APIs. This module is the missing receipt trail: every call
that costs real money appends one line, into the tenant's UPLOADS_DIR so the
HOST sees it via the existing /mnt/clients/<t>/openvoiceui/uploads mount with
no new plumbing. Host-side aggregator: scripts/metered-spend-report.py →
boot-gate line.

Costs are ESTIMATES (list-price ballpark per image/call, not billing truth) —
they exist to make relative burn visible daily, not to reconcile invoices.
Update EST_COST_USD when providers reprice; an unknown model logs cost=None,
which the aggregator surfaces as UNPRICED rather than silently $0 (a spend row
that cannot be priced must not look free).

Never raises: a broken tally must not break generation itself.
"""
from __future__ import annotations

import json
import logging
import time

from services.paths import UPLOADS_DIR

logger = logging.getLogger(__name__)

SPEND_LOG = UPLOADS_DIR / ".metered-spend.jsonl"

# List-price ballparks per CALL (image models: per image). Estimates on purpose.
EST_COST_USD = {
    # Gemini image generation
    "nano-banana-pro-preview": 0.15,
    "gemini-3.1-flash-image-preview": 0.04,
    "gemini-2.5-flash-image": 0.04,
    "gemini-2.0-flash-exp-image-generation": 0.04,
    "imagen-4.0-generate-001": 0.04,
    "imagen-4.0-fast-generate-001": 0.02,
    "imagen-3.0-generate-002": 0.03,
    # Gemini text calls made by generation features (prompt enhance)
    "enhance-prompt": 0.001,
    # HF inference (FLUX/SD) — credit-metered, negligible per call at our tier;
    # priced non-zero so volume still shows up in the tally.
    "hf-inference": 0.005,
}


def log_spend(route: str, model: str, n_calls: int = 1, cost_key: str | None = None) -> None:
    """Append one spend row. cost_key overrides model for pricing (e.g. 'hf-inference')."""
    try:
        est = EST_COST_USD.get(cost_key or model)
        row = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "route": route,
            "model": model,
            "n": n_calls,
            "est_usd": round(est * n_calls, 4) if est is not None else None,
        }
        with SPEND_LOG.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001 — tally must never break the feature
        logger.warning("metered_spend: failed to log row", exc_info=True)
