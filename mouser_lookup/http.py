"""
The one place this package talks to api.mouser.com - every call goes
through request(), so the timeout, the API key and Mouser's way of
reporting errors (HTTP 200 with an "Errors" list) are handled the same
for search, order history and the cart.

Mouser takes the API key in the query string, and requests puts the whole
URL in its error text - so every message that leaves here goes through
redact() first: an error shown on a dashboard or written to a log never
carries the key.
"""

import logging
import re
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)

MOUSER_API_BASE = "https://api.mouser.com/api/v1"
REQUEST_TIMEOUT = 20


class MouserError(Exception):
    """Mouser refused a request or answered with something unusable."""


_KEY_IN_URL = re.compile(r'(apiKey=)[^&\s\'")]+', re.I)


def redact(text: Any, api_key: str = "") -> str:
    """text with the API key taken out - as given, and any apiKey=... in a URL."""
    text = str(text)
    if api_key:
        text = text.replace(api_key, "***")
    return _KEY_IN_URL.sub(r"\1***", text)


def error_messages(body: Any) -> list:
    """Mouser's own error messages from a response body ("Errors": [{"Message": ...}])."""
    if not isinstance(body, dict):
        return []
    messages = []
    # some errors come as a bare {"Message": "..."} instead of an Errors list
    if body.get("Message") and not body.get("Errors") and len(body) <= 2:
        messages.append(str(body["Message"]).strip())
    for err in body.get("Errors") or []:
        if isinstance(err, dict):
            text = (err.get("Message") or err.get("Code") or "").strip()
            if text:
                messages.append(text)
    return messages


def request(method: str, path: str, api_key: str, *, params: Optional[Dict[str, Any]] = None,
            json: Any = None) -> Dict[str, Any]:
    """
    Call a Mouser API path (e.g. "/orderhistory/ByDateRange") and return the
    JSON body. Raises MouserError for no key, network trouble, an HTTP error,
    a body that isn't JSON, or Mouser's own "Errors" list.
    """
    if not api_key:
        raise MouserError("No Mouser API key is set.")
    query = {"apiKey": api_key, **(params or {})}
    try:
        response = requests.request(method, f"{MOUSER_API_BASE}{path}", params=query, json=json,
                                    headers={"Accept": "application/json"}, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.RequestException as exc:
        raise MouserError(redact(f"Couldn't reach Mouser: {exc}", api_key)) from None
    try:
        body = response.json()
    except ValueError:
        body = None
    messages = error_messages(body)
    if response.status_code >= 400:
        detail = "; ".join(messages) or (response.text or "")[:200].strip()
        raise MouserError(redact(f"Mouser answered {response.status_code}" + (f": {detail}" if detail else ""),
                                 api_key))
    if body is None:
        raise MouserError("Mouser's answer wasn't JSON.")
    if messages:
        raise MouserError(redact("; ".join(messages), api_key))
    return body
