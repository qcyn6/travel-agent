import httpx
import json
import pytest

from backend.observability.alerts import LoggingAlertSink, WebhookAlertSink


@pytest.mark.asyncio
async def test_logging_alert_sink_emits_operator_alert(caplog):
    with caplog.at_level("ERROR"):
        await LoggingAlertSink().notify(
            event="planning_job_failed",
            severity="error",
            message="规划任务执行失败",
            metadata={"job_id": "job-1"},
        )

    assert "operator_alert" in caplog.text
    assert "planning_job_failed" in caplog.text


@pytest.mark.asyncio
async def test_webhook_alert_sink_posts_structured_payload():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await WebhookAlertSink(
            "https://alerts.example.test/hook",
            client=client,
        ).notify(
            event="planning_queue_unavailable",
            severity="warning",
            message="规划任务队列不可用",
            metadata={"job_id": "job-1"},
        )

    assert len(requests) == 1
    assert requests[0].url == "https://alerts.example.test/hook"
    assert json.loads(requests[0].content) == {
        "event": "planning_queue_unavailable",
        "severity": "warning",
        "message": "规划任务队列不可用",
        "metadata": {"job_id": "job-1"},
    }
