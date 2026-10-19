"""Scrub configured credentials and credential-bearing URLs before output."""
import logging
import re
from urllib.parse import quote, quote_plus


def redact_secrets(message, settings):
    text = str(message)
    for name in ("auth_secret", "database_url", "redis_url", "llm_api_key", "amap_api_key",
                 "qweather_api_key", "embedding_api_key", "bootstrap_admin_password", "alert_webhook_url"):
        secret = getattr(settings, name, None)
        if isinstance(secret, str) and len(secret) >= 6:
            for value in {secret, quote(secret, safe=""), quote_plus(secret)}:
                text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@", r"\1[REDACTED]@", text)
    return re.sub(r"(?i)([?&](?:key|api_key|apikey|access_token|token|password)=)[^&\s]+", r"\1[REDACTED]", text)


class SecretFilter(logging.Filter):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings

    def filter(self, record):
        record.msg = redact_secrets(record.getMessage(), self.settings)
        record.args = ()
        if record.exc_info:
            record.exc_text = redact_secrets(logging.Formatter().formatException(record.exc_info), self.settings)
            record.exc_info = None
        return True


def install_redaction(settings):
    handlers = set(logging.getLogger().handlers)
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            handlers.update(logger.handlers)
    for handler in handlers:
        if not any(isinstance(item, SecretFilter) for item in handler.filters):
            handler.addFilter(SecretFilter(settings))
