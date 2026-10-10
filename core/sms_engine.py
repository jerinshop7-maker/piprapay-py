"""
Exact port of senderWhitelist() + MFSMessageVerified() + reconcileByLongestChain().

Verified against pp-functions.php:888-1249:
- 9 providers have formats (telecash + ipay have NONE -> always False).
- Output keys: {mfs, type, raw} + mapped keys verbatim (sender/trxid/ref/fee/
  balance/datetime/receiver). trxid is NOT uppercased; only amount/balance/fee
  are comma-stripped.
- reconcile: moneyToInt/money_sanitize semantics, BFS longest chain from
  canonical balance, bulk-approve chain, current_balance=intToMoney(last,2).
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


# Exact PHP $formats (commented-out Merchant/Agent blocks excluded, as in PHP).
FORMATS = {
    "bkash": [
        {"type": "Personal", "priority": 100,
         "pattern": r"You have received Tk ([\d,.]+) from (\d+)\.(?:\s*Ref[:\-]?\s*(\S+))? Fee Tk ([\d,.]+)\. Balance Tk ([\d,.]+)\. TrxID ([A-Z0-9]+) at ([\d\/:\s]+)",
         "map": ["amount", "sender", "ref", "fee", "balance", "trxid", "datetime"]},
        {"type": "Personal", "priority": 90,
         "pattern": r"Cash In Tk ([\d,.]+) from (\d+) successful\. Fee Tk ([\d,.]+)\. Balance Tk ([\d,.]+)\. TrxID ([A-Z0-9]+) at ([\d\/:\s]+)",
         "map": ["amount", "sender", "fee", "balance", "trxid", "datetime"]},
        {"type": "Merchant", "priority": 80,
         "pattern": r"You have received payment Tk ([\d,.]+) from (\d+)\.(?:\s*Ref[:\-]?\s*(\S+))? Fee Tk ([\d,.]+)\. Balance Tk ([\d,.]+)\. TrxID ([A-Z0-9]+) at ([\d\/:\s]+)",
         "map": ["amount", "sender", "ref", "fee", "balance", "trxid", "datetime"]},
    ],
    "nagad": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Money Received\. Amount: Tk ([\d,.]+) Sender: (\d+)(?:\s*Ref[:\-]?\s*(\S+))? TxnID: ([A-Z0-9]+) Balance: Tk ([\d,.]+) ([\d\/:\s]+)",
         "map": ["amount", "sender", "ref", "trxid", "balance", "datetime"]},
        {"type": "Personal", "priority": 90,
         "pattern": r"Cash In Received\. Amount: Tk ([\d,.]+) Uddokta: (\d+) TxnID: ([A-Z0-9]+) Balance: ([\d,.]+) ([\d\/:\s]+)",
         "map": ["amount", "sender", "trxid", "balance", "datetime"]},
    ],
    "rocket": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk([\d,.]+) received from A\/C:([*\d]+) Fee:Tk([\d,.]+)\, Your A\/C Balance: Tk([\d,.]+) TxnId:([A-Z0-9]+)(?: Date:([\w\-:\s]+))?",
         "map": ["amount", "sender", "fee", "balance", "trxid", "datetime"]},
    ],
    "upay": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Tk\. ([\d,.]+) has been received from (\d+)\.(?:\s*Ref[:\-]?\s*(\S+))? Balance Tk\. ([\d,.]+)\. TrxID ([A-Z0-9]+) at ([\d\/:\s]+)\.",
         "map": ["amount", "sender", "ref", "balance", "trxid", "datetime"]},
    ],
    "tap": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Received Tk ([\d,.]+) from (\d+)\. Balance Tk\. ([\d,.]+)\. TxID: ([A-Z0-9]+)\.",
         "map": ["amount", "sender", "balance", "trxid"]},
    ],
    "cellfin": [
        {"type": "Personal", "priority": 100,
         "pattern": r"Islami Bank CellFin Received ([\d,.]+) Tk From CellFin: (\d+) To CellFin: (\d+) TrxId: ([A-Z0-9]+)",
         "map": ["amount", "sender", "receiver", "trxid"]},
    ],
    "okwallet": [
        {"type": "Personal", "priority": 100,
         "pattern": r"\(OK Wallet\) Successfully received Tk ([\d,.]+) from A\/C (\d+)\.(?:\s*Ref[:\-]?\s*(\S+))? Balance Tk ([\d,.]+)\. TrxID ([A-Z0-9]+)",
         "map": ["amount", "sender", "ref", "balance", "trxid"]},
    ],
    "mcash": [
        {"type": "Personal", "priority": 100,
         "pattern": r"IBBL mCash You have received Tk: ([\d,.]+) From: (\d+)(?:\s*Reference:\s*(\S*))? Balance Tk: ([\d,.]+) TrxID: ([A-Z0-9]+)",
         "map": ["amount", "sender", "ref", "balance", "trxid"]},
    ],
    "pathaopay": [
        {"type": "Personal", "priority": 100,
         "pattern": r"You have received BDT ([\d,.]+) from (\+?\d+)\. Balance BDT ([\d,.]+) TrxID ([A-Z0-9]+)",
         "map": ["amount", "sender", "balance", "trxid"]},
    ],
    # telecash + ipay intentionally have no formats (PHP has none) -> always False.
}


def mfs_message_verified(mfs: str, message: str):
    """Parse an MFS SMS. Returns {mfs,type,raw,...mapped} or False. Exact PHP port."""
    key = (mfs or "").lower().strip()
    if key not in FORMATS:
        return False
    msg = re.sub(r"\s+", " ", (message or "").strip())
    formats = sorted(FORMATS[key], key=lambda f: f.get("priority", 0), reverse=True)
    for fmt in formats:
        m = re.search(fmt["pattern"], msg, re.IGNORECASE)
        if not m:
            continue
        data = {"mfs": key, "type": fmt["type"], "raw": msg}
        groups = m.groups()
        for i, map_key in enumerate(fmt["map"]):
            data[map_key] = groups[i] if i < len(groups) else None
        for field in ("amount", "balance", "fee"):
            if field in data and data[field] is not None:
                data[field] = data[field].replace(",", "")
        return data
    return False


def reconcile_by_longest_chain(device_id: str, sender_key: str, gateway_type: str):
    """Exact port of reconcileByLongestChain() (pp-functions.php:1169-1249)."""
    from core import money as M
    from core.models import BalanceVerification, SmsData

    rule = BalanceVerification.objects.filter(
        device_id=device_id, sender_key=sender_key, type=gateway_type).first()
    canonical = 0
    if rule and rule.current_balance:
        canonical = M.money_to_int(str(rule.current_balance))

    rows = list(SmsData.objects.filter(
        device_id=device_id, sender_key=sender_key, type=gateway_type,
        status__in=["approved", "awaiting-review"], source="app").order_by("id"))
    if not rows:
        return

    sms_list = []
    for r in rows:
        amount_int = M.money_to_int(str(r.amount or "0"))
        balance_int = M.money_to_int(str(r.balance or "0"))
        if amount_int <= 0 or balance_int <= 0:
            continue
        sms_list.append({"id": r.id, "amount_int": amount_int,
                         "balance_int": balance_int,
                         "prev": balance_int - amount_int, "bal": balance_int})

    nxt: dict[int, list[dict]] = {}
    for s in sms_list:
        nxt.setdefault(s["prev"], []).append(s)

    best_chain: list[dict] = []
    queue = [canonical]
    while queue:
        current = queue.pop(0)
        if current not in nxt:
            continue
        for _sms in nxt[current]:
            chain: list[dict] = []
            temp_current = current
            temp_next = {k: list(v) for k, v in nxt.items()}
            while temp_current in temp_next and temp_next[temp_current]:
                sms_in_chain = temp_next[temp_current].pop(0)
                chain.append(sms_in_chain)
                temp_current = sms_in_chain["bal"]
            if len(chain) > len(best_chain):
                best_chain = chain

    if not best_chain:
        return

    ids = [s["id"] for s in best_chain]
    if ids:
        SmsData.objects.filter(id__in=ids).update(status="approved", reason="--")

    final_balance = M.int_to_money(best_chain[-1]["bal"], 2)
    BalanceVerification.objects.filter(
        device_id=device_id, sender_key=sender_key, type=gateway_type).update(
        current_balance=final_balance)
    return final_balance
