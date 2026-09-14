import logging
from typing import Mapping, Protocol

import httpx

from backend.observability.metrics import metrics


logger = logging.getLogger(__name__)


class AlertSink(Protocol):
    async def notify(
        self,
        *,
        event: str,
        severity: str,
        message: str,
        metadata: Mapping[str, object] | None = None,
    ) -> None: ...


class LoggingAlertSink:
    """Emit operator alerts to structured logs without sending network traffic."""

    async def notify(
        self,
        *,
        event: str,
        severity: str,
        message: str,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        metrics.inc(
            "travel_agent_alerts_total",
            {"event": event, "severity": severity},
        )
        log = logger.critical if severity in {"critical", "error"} else logger.warning
        log(
            "operator_alert event=%s severity=%s message=%s metadata=%s",
            event,
            severity,
            message,
            dict(metadata or {}),
        )


class WebhookAlertSink:
    def __init__(self, url: str, *, timeout_seconds: float = 5.0, client=None):
        self.url = url
        self.timeout_seconds = timeout_seconds
        self.client = client
        self._log_sink = LoggingAlertSink()

    async def notify(
        self,
        *,
        event: str,
        severity: str,
        message: str,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        await self._log_sink.notify(
            event=event,
            severity=severity,
            message=message,
            metadata=metadata,
        )
        payload = {
            "event": event,
            "severity": severity,
            "message": message,
            "metadata": dict(metadata or {}),
        }
        try:
            if self.client is not None:
                response = await self.client.post(self.url, json=payload)
                response.raise_for_status()
            else:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(self.url, json=payload)
                    response.raise_for_status()
        except httpx.HTTPError:
            metrics.inc("travel_agent_alert_delivery_failures_total")
            logger.exception("operator alert webhook delivery failed: event=%s", event)

    async def close(self) -> None:
        if self.client is None:
            return None
        close = getattr(self.client, "aclose", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result


def build_alert_sink(settings) -> AlertSink:
    url = getattr(settings, "alert_webhook_url", None)
    if url:
        return WebhookAlertSink(
            url,
            timeout_seconds=float(getattr(settings, "alert_timeout_seconds", 5.0)),
        )
    return LoggingAlertSink()
