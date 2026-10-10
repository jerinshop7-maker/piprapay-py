"""
sendIPN()/sendIPNMulti() port — concurrent webhook delivery.
PHP used curl_multi; here we use ThreadPoolExecutor + requests.
"""
from __future__ import annotations

import concurrent.futures

import requests


def send_ipn(url: str, payload: dict, timeout: int = 15) -> int:
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        return resp.status_code
    except Exception:
        return 0


def send_ipn_multi(jobs: list[dict], timeout: int = 15, workers: int = 10) -> dict:
    results: dict = {}

    def _one(job):
        code = send_ipn(job.get("url", ""), job.get("payload", {}), timeout=timeout)
        return job.get("id"), code

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for jid, code in pool.map(_one, jobs):
            results[jid] = code
    return results
