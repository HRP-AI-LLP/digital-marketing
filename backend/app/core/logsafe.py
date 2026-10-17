"""Keep secrets out of log files.

httpx logs every request URL at INFO, and some APIs (Meta's Graph API, Google) put app secrets, access tokens and
OAuth codes in the query string. This quiets httpx to warnings and masks secret-looking query values in every
log line (including uvicorn's access log, which shows OAuth callback codes).
"""

import logging
import re

SECRET_PARAMS = (
    "access_token", "client_secret", "fb_exchange_token", "refresh_token", "code", "api_key", "apikey", "key",
    "token", "secret", "password", "signature", "sig", "s", "input_token", "appsecret_proof", "state",
)
_RX = re.compile(r"([?&](?:" + "|".join(SECRET_PARAMS) + r")=)[^&\s\"']+", re.I)
_BEARER = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.I)


def redact(text: str) -> str:
    return _BEARER.sub(r"\1[redacted]", _RX.sub(r"\1[redacted]", text))


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


def install() -> None:
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    f = RedactFilter()
    for h in logging.getLogger().handlers:
        h.addFilter(f)
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn", "httpx"):
        logging.getLogger(name).addFilter(f)
