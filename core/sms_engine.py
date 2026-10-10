"""
senderWhitelist() + MFSMessageVerified() port from pp-functions.php.

senderWhitelist() returns the 11 known SMS sender providers (bKash, Nagad,
Rocket, Upay, Tap, Cellfin, OkWallet, mCash, PathaoPay, TeleCash, Ipay).

MFSMessageVerified() parses carrier SMS text into structured data using the
same regex priority order as PHP (most-specific pattern first per provider).
Returns dict on match, False otherwise.
"""
from __future__ import annotations

import re

PROVIDERS = {
    "bkash": {"name": "bKash", "currency": "BDT", "balance_verify": "true", "senders": ["bkash"]},
    "nagad": {"name": "Nagad", "currency": "BDT", "balance_verify": "true", "senders": ["nagad"]},
    "rocket": {"name": "Rocket", "currency": "BDT", "balance_verify": "true", "senders": ["16216"]},
    "upay": {"name": "Upay", "currency": "BDT", "balance_verify": "true", "senders": ["upay"]},
    "tap": {"name": "Tap", "currency": "USD", "balance_verify": "true", "senders": ["tap."]},
    "cellfin": {"name": "Cellfin", "currency": "BDT", "balance_verify": "false", "senders": ["ibbl ."]},
    "okwallet": {"name": "Ok Wallet", "currency": "BDT", "balance_verify": "true", "senders": ["01847-348685"]},
    "mcash": {"name": "mCash", "currency": "BDT", "balance_verify": "true", "senders": ["16259"]},
    "pathaopay": {"name": "Pathao Pay", "currency": "BDT", "balance_verify": "true", "senders": ["pathaopay"]},
    "telecash": {"name": "TeleCash", "currency": "BDT", "balance_verify": "true", "senders": ["telecash"]},
    "ipay": {"name": "Ipay", "currency": "BDT", "balance_verify": "true", "senders": ["09638-900800"]},
}


def sender_whitelist(sender=None, provider_key=None, mode="provider", provider_name=None):
    if mode == "senders":
        seen, out = set(), []
        for p in PROVIDERS.values():
            for s in p["senders"]:
                if s not in seen:
                    seen.add(s)
                    out.append(s)
        return out
    if sender is not None:
        s = str(sender).lower().strip()
        for key, p in PROVIDERS.items():
            for cand in p["senders"]:
                if cand.lower() == s:
                    return {"provider_key": key, "name": p["name"], "currency": p["currency"],
                            "balance_verify": p["balance_verify"], "sender": s}
        return False
    if provider_key is not None:
        return PROVIDERS.get(provider_key, False)
    if provider_name is not None:
        name = str(provider_name).lower().strip()
        for key, p in PROVIDERS.items():
            if p["name"].lower() == name:
                return {"provider_key": key, "name": p["name"], "currency": p["currency"],
                        "balance_verify": p["balance_verify"], "senders": p["senders"]}
        return False
    return PROVIDERS


# Regex formats — same priority order as PHP $formats array.
FORMATS = {
    "bkash": [
        {"type": "Personal", "priority": 100,
         "pattern": r"You have received Tk ([\d,.]+) from (\d+)\.(?:\s*Ref[:\-]?\s*(\S+))? Fee Tk ([\d,.]+)\. Balance Tk ([\d,.]+)\. TrxID ([A-Z0-9]+) at ([\d\/:\s]+)",
         "map": ["amount", "sender", "ref", "fee", "balance", "trxid", "datetime"]},
        {"type": "Agent", "priority": 90,
         "pattern": r"Cash In Tk ([\d,.]+) from (\d+) successful\. Fee Tk ([\d,.]+)\. Balance Tk ([\d,.]+)\. TrxID ([A-Z0-9]+) at ([\d\/:\s]+)",
         "map": ["amount", "sender", "fee", "balance", "trxid", "datetime"]},
        {"type": "Merchant", "priority": 80,
         "pattern": r"You have received payment Tk ([\d,.]+) from (\d+)\. Ref (.*?)\. Balance Tk ([\d,.]+)\. TrxID ([A-Z0-9]+) at ([\d\/:\s]+)",
         "map": ["amount", "sender", "ref", "balance", "trxid", "datetime"]},
    ],
    "nagad": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\.?([\d,.]+) received from (\S+).*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "sender", "trxid"]},
        {"type": "Merchant", "priority": 80,
         "pattern": r"Payment of Tk\.?([\d,.]+) to (\S+) successful.*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "sender", "trxid"]},
    ],
    "rocket": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\.?([\d,.]+) received from (\d+).*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "sender", "trxid"]},
        {"type": "Agent", "priority": 90,
         "pattern": r"Cash-in Tk\.?([\d,.]+) from (\d+).*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "sender", "trxid"]},
    ],
    "upay": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\.?([\d,.]+) received from (\d+).*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "sender", "trxid"]},
    ],
    "tap": [
        {"type": "Personal", "priority": 100,
         "pattern": r"received ([\d,.]+).*from (\S+).*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "sender", "trxid"]},
    ],
    "cellfin": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\.?([\d,.]+).*credited.*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "trxid"]},
    ],
    "okwallet": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\.?([\d,.]+) received from (\d+).*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "sender", "trxid"]},
    ],
    "mcash": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\.?([\d,.]+) received.*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "trxid"]},
    ],
    "pathaopay": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\.?([\d,.]+) received from (\S+).*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "sender", "trxid"]},
    ],
    "telecash": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\.?([\d,.]+) received.*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "trxid"]},
    ],
    "ipay": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\.?([\d,.]+) received from (\S+).*TrxID\.?\s*([A-Z0-9]+)",
         "map": ["amount", "sender", "trxid"]},
    ],
}


def _num(s: str) -> str:
    return (s or "").replace(",", "").strip()


def mfs_message_verified(mfs: str, message: str):
    """Parse an MFS SMS. Returns dict {type, amount, sender, trx_id, balance, ...} or False."""
    key = (mfs or "").lower().strip()
    msg = re.sub(r"\s+", " ", (message or "").strip())
    formats = sorted(FORMATS.get(key, []), key=lambda f: f.get("priority", 0), reverse=True)
    for fmt in formats:
        m = re.search(fmt["pattern"], msg, re.IGNORECASE)
        if not m:
            continue
        out = {"type": fmt["type"], "provider": key}
        groups = m.groups()
        for i, field in enumerate(fmt["map"]):
            val = groups[i] if i < len(groups) else ""
            val = (val or "").strip()
            if field == "amount":
                out["amount"] = _num(val)
            elif field == "sender":
                out["number"] = val
            elif field in ("trxid", "trx_id"):
                out["trx_id"] = val.upper()
            elif field == "balance":
                out["balance"] = _num(val)
            elif field == "fee":
                out["fee"] = _num(val)
            else:
                out[field] = val
        out.setdefault("amount", "0")
        out.setdefault("trx_id", "--")
        return out
    return False


def reconcile_by_longest_chain(device_id: str, sender_key: str, gateway_type: str):
    """Port of reconcileByLongestChain(): derive running balance chain for a device/sender.

    Returns the reconciled balance string. Pure-python version: walks SmsData
    rows chronologically and keeps the last reported balance per (device, sender_key, type).
    """
    from core.models import SmsData, BalanceVerification

    rows = SmsData.objects.filter(device_id=device_id, sender_key=sender_key, type=gateway_type).order_by("id")
    last_balance = "0"
    for r in rows:
        if r.balance and r.balance != "--":
            last_balance = r.balance
    BalanceVerification.objects.filter(device_id=device_id, sender_key=sender_key, type=gateway_type).update(
        current_balance=last_balance if last_balance != "--" else 0
    )
    return last_balance
