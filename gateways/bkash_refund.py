"""bKash tokenized refund entry-point. Delegates to BkashApiTokenizedGateway.refund()
with the pp_bkash_tokenized_refund() guard chain (see gateways/api_gateways.py)."""
from __future__ import annotations


def bkash_tokenized_refund(transaction, refund: dict) -> dict:
    from gateways.api_gateways import bkash_tokenized_refund as _refund

    return _refund(transaction, refund)
