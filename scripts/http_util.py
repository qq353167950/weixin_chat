#!/usr/bin/env python3
"""Shared HTTP helpers: short-lived sessions, always close, optional retry.

Windows 10053 often happens when connections are aborted mid-flight or left half-open.
This module:
  - uses a short-lived requests.Session per call (or short batch)
  - always closes response + session in finally
  - disables keep-alive by default to avoid sticky dead sockets after Ctrl+C / kill
  - retries on common connection aborts
"""

from __future__ import annotations

import atexit
import os
import time
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter

# Track open sessions so we can force-close on exit / interrupt
_OPEN_SESSIONS: set[requests.Session] = set()


def _register(sess: requests.Session) -> requests.Session:
    _OPEN_SESSIONS.add(sess)
    return sess


def _unregister(sess: requests.Session) -> None:
    _OPEN_SESSIONS.discard(sess)


def close_all_sessions() -> None:
    """Force-close every tracked session (call on exit / KeyboardInterrupt)."""
    for sess in list(_OPEN_SESSIONS):
        try:
            sess.close()
        except Exception:
            pass
        _unregister(sess)


atexit.register(close_all_sessions)


def _make_session(keep_alive: bool = False) -> requests.Session:
    sess = requests.Session()
    # Prefer closing connection after each response on flaky Windows / proxy networks
    if not keep_alive or os.getenv("HTTP_FORCE_CLOSE", "1") == "1":
        sess.headers.update({"Connection": "close"})
    # modest pool; we still close session after use
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4, max_retries=0)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return _register(sess)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    # nested: ('Connection aborted.', ConnectionAbortedError(10053, ...))
    msg = str(exc).lower()
    for token in ("10053", "10054", "connection aborted", "remotedisconnected", "broken pipe", "reset by peer"):
        if token in msg:
            return True
    return False


def request_json(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    json_body: Any = None,
    data: Any = None,
    files: Any = None,
    timeout: float | tuple = 60,
    retries: int | None = None,
    retry_sleep: float | None = None,
) -> dict:
    """HTTP request -> JSON dict. Always closes connection."""
    if retries is None:
        retries = int(os.getenv("HTTP_RETRIES", "3") or 3)
    if retry_sleep is None:
        retry_sleep = float(os.getenv("HTTP_RETRY_SLEEP", "1.5") or 1.5)

    method = method.upper()
    last_err: BaseException | None = None
    for attempt in range(1, retries + 1):
        sess = _make_session(keep_alive=False)
        resp = None
        try:
            resp = sess.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body,
                data=data,
                files=files,
                timeout=timeout,
            )
            # read body fully before close
            try:
                data_json = resp.json()
            except Exception:
                text = resp.text
                raise RuntimeError(f"Non-JSON response HTTP {resp.status_code}: {text[:300]}")
            if resp.status_code >= 400:
                # do not retry most 4xx except 429
                if resp.status_code == 429 and attempt < retries:
                    time.sleep(retry_sleep * attempt)
                    last_err = RuntimeError(f"HTTP 429: {data_json}")
                    continue
                raise RuntimeError(f"HTTP {resp.status_code}: {data_json}")
            return data_json
        except BaseException as e:
            last_err = e
            if attempt < retries and _is_retryable(e):
                print(f"[http] retry {attempt}/{retries} after: {e}")
                time.sleep(retry_sleep * attempt)
                continue
            raise
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            try:
                sess.close()
            except Exception:
                pass
            _unregister(sess)

    raise RuntimeError(f"HTTP failed after retries: {last_err}")


def request_bytes(
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    timeout: float | tuple = 60,
    retries: int | None = None,
) -> bytes:
    if retries is None:
        retries = int(os.getenv("HTTP_RETRIES", "3") or 3)
    last_err: BaseException | None = None
    for attempt in range(1, retries + 1):
        sess = _make_session(keep_alive=False)
        resp = None
        try:
            resp = sess.request(method, url, headers=headers, timeout=timeout, stream=True)
            resp.raise_for_status()
            # consume fully
            content = resp.content
            return content
        except BaseException as e:
            last_err = e
            if attempt < retries and _is_retryable(e):
                print(f"[http] retry {attempt}/{retries} after: {e}")
                time.sleep(1.2 * attempt)
                continue
            raise
        finally:
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            try:
                sess.close()
            except Exception:
                pass
            _unregister(sess)
    raise RuntimeError(f"HTTP bytes failed after retries: {last_err}")


def with_session(fn: Callable[[requests.Session], Any]) -> Any:
    """Run a callback with a session that is always closed."""
    sess = _make_session(keep_alive=False)
    try:
        return fn(sess)
    finally:
        try:
            sess.close()
        except Exception:
            pass
        _unregister(sess)
