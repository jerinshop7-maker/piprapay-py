"""
sendIPN()/sendIPNMulti() port — concurrent webhook delivery.
PHP used curl_multi with 3s connect / 5s total timeouts; requests mirrors
that, sends Connection: close, and serializes JSON with unescaped unicode
(JSON_UNESCAPED_UNICODE) so raw body bytes match.
"""
from __future__ import annotations

import concurrent.futures
import json

import requests

_HEADERS = {"Connection": "close"}


def send_ipn(url: str, payload: dict, timeout=(3, 5)) -> int:
    try:
        resp = requests.post(url, data=json.dumps(payload, ensure_ascii=False),
                             headers={**_HEADERS, "Content-Type": "application/json"},
                             timeout=timeout)
        return resp.status_code
    except Exception:
        return 0


def send_ipn_multi(jobs: list[dict], timeout=(3, 5), workers: int = 10) -> dict:
    results: dict = {}

    def _one(job):
        code = send_ipn(job.get("url", ""), job.get("payload", {}), timeout=timeout)
        return job.get("id"), code

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for jid, code in pool.map(_one, jobs):
            results[jid] = code
    return results
