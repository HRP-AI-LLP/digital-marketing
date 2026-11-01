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
# Secrets inside URL paths: Telegram bot tokens (api.telegram.org/bot<token>/...) and Discord webhook tokens
# (discord.com/api/webhooks/<id>/<token>).
_TG_BOT = re.compile(r"(/bot)\d{5,16}:[A-Za-z0-9_-]{20,}")
_WEBHOOK = re.compile(r"(/api(?:/v\d+)?/webhooks/\d+/)[A-Za-z0-9_.-]+", re.I)


def redact(text: str) -> str:
    text = _WEBHOOK.sub(r"\1[redacted]", _TG_BOT.sub(r"\1[redacted]", text))
    return _BEARER.sub(r"\1[redacted]", _RX.sub(r"\1[redacted]", text))


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        if redact(msg) == msg:
            return True
        # Redact inside the arguments when there are any: some formatters (uvicorn's access log) unpack
        # record.args themselves, so the tuple has to stay.
        if isinstance(record.args, tuple) and record.args:
            record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
        elif isinstance(record.args, dict) and record.args:
            record.args = {k: redact(v) if isinstance(v, str) else v for k, v in record.args.items()}
        else:
            record.msg, record.args = redact(msg), None
        return True


def install() -> None:
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    f = RedactFilter()
    for h in logging.getLogger().handlers:
        h.addFilter(f)
    for name in ("uvicorn.access", "uvicorn.error", "uvicorn", "httpx"):
        logging.getLogger(name).addFilter(f)
