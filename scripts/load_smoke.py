#!/usr/bin/env python3
"""Lightweight API load smoke test.

Usage:
  python scripts/load_smoke.py http://localhost:8080 --requests 200 --concurrency 25

It intentionally uses only stdlib so buyers can run it without extra tools.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from urllib import request, error


def fetch(url: str, timeout: float = 10) -> tuple[int, float]:
    start = time.perf_counter()
    try:
        with request.urlopen(url, timeout=timeout) as response:
            response.read()
            return response.status, time.perf_counter() - start
    except error.HTTPError as exc:
        return exc.code, time.perf_counter() - start
    except Exception:
        return 0, time.perf_counter() - start


async def worker(url: str, queue: asyncio.Queue, results: list[tuple[int, float]]):
    while True:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        results.append(await asyncio.to_thread(fetch, url))
        queue.task_done()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--path", default="/health/live")
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=25)
    args = parser.parse_args()

    url = args.base_url.rstrip("/") + args.path
    queue = asyncio.Queue()
    for i in range(args.requests):
        queue.put_nowait(i)
    results: list[tuple[int, float]] = []
    started = time.perf_counter()
    await asyncio.gather(*(worker(url, queue, results) for _ in range(args.concurrency)))
    elapsed = time.perf_counter() - started
    ok = sum(1 for status, _ in results if 200 <= status < 400)
    by_status: dict[int, int] = {}
    for status, _duration in results:
        by_status[status] = by_status.get(status, 0) + 1
    durations = sorted(duration for _status, duration in results)
    p95 = durations[int(len(durations) * 0.95) - 1] if durations else 0
    print(json.dumps({
        "url": url,
        "requests": len(results),
        "concurrency": args.concurrency,
        "ok": ok,
        "by_status": by_status,
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(len(results) / elapsed, 2) if elapsed else 0,
        "p95_ms": round(p95 * 1000, 2),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
