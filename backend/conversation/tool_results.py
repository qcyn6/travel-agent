"""Ordinary-mode tool envelopes and per-turn call budget, without LangChain dependencies."""

from __future__ import annotations

import json
import logging
import asyncio
import time
from typing import Any

logger = logging.getLogger(__name__)


def tool_result(
    status: str,
    message: str,
    *,
    data: object = None,
    retryable: bool = False,
    missing_fields: list[str] | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "message": message,
        "data": data,
        "retryable": retryable,
        "missing_fields": missing_fields or [],
    }


class ReActToolCallGuard:
    """Limit tool calls per turn and allow one retry for identical arguments."""

    max_calls = 6

    def __init__(self):
        self.timeouts = {"amap_route": 25.0, "qweather_forecast": 12.0,
                         "amap_nearby_search": 25.0, "travel_guide_search": 8.0}
        self.deadline: float | None = None
        self.on_progress = None
        self.request_id: str | None = None
        self.reset()

    def configure_budget(self, *, deadline: float, timeouts: dict[str, float], on_progress=None, request_id: str | None = None) -> None:
        self.deadline = deadline
        self.timeouts = timeouts
        self.on_progress = on_progress
        self.request_id = request_id

    def reset(self) -> None:
        self.calls_used = 0
        self.attempts: dict[str, dict[str, Any]] = {}

    async def invoke(self, name: str, arguments: dict[str, object], operation) -> str:
        remaining = self.deadline - time.monotonic() if self.deadline is not None else float("inf")
        # Reserve time for the model to explain the result instead of starting
        # work that cannot finish before the turn deadline.
        if remaining <= 3:
            return tool_json(tool_result("budget_exceeded", "本轮剩余时间不足，请使用已有结果回答，或说明未完成的部分。"))
        signature = json.dumps(
            {"tool": name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        previous = self.attempts.get(signature)
        if self.calls_used >= self.max_calls:
            return tool_json(tool_result(
                "budget_exceeded", "本轮工具调用已达上限。请使用已有结果回答，或说明尚未完成的部分。",
            ))
        if previous is not None:
            can_retry = (
                previous.get("status") in {"retryable_error", "partial"}
                and previous.get("retryable")
                and previous.get("attempts") == 1
            )
            if not can_retry:
                return tool_json(tool_result(
                    "repeat_blocked", "相同工具和参数已经执行且不能再次重试；请基于已有结果回答，或询问用户补充信息。",
                ))
        attempts = int(previous.get("attempts", 0)) + 1 if previous else 1
        started = time.monotonic()
        self.calls_used += 1
        self.attempts[signature] = {"attempts": attempts, "status": "in_progress", "retryable": False}
        if self.on_progress:
            label = {"amap_route": "正在查询路线", "qweather_forecast": "正在查询天气",
                     "amap_nearby_search": "正在查询附近地点", "travel_guide_search": "正在检索旅行指南"}.get(name, "正在查询")
            self.on_progress(label)
        try:
            async with asyncio.timeout(min(self.timeouts.get(name, 25.0), remaining - 2)):
                result = await operation()
            if not isinstance(result, dict) or result.get("status") not in {
                "ok", "partial", "needs_clarification", "retryable_error", "error"
            }:
                result = tool_result("ok", str(result))
        except TimeoutError:
            result = tool_result("retryable_error", "工具查询超时，请根据剩余时间判断是否重试。", retryable=True)
            logger.warning("普通模式工具超时 tool=%s attempt=%s", name, attempts)
        except Exception as exc:  # noqa: BLE001 - send a sanitized failure back to the Agent
            logger.warning("普通模式工具 %s 执行失败：%s", name, type(exc).__name__)
            result = tool_result(
                "retryable_error", "工具执行暂时失败，请判断是否重试一次或改用其他相关工具。",
                retryable=True,
            )
        self.attempts[signature] = {
            "attempts": attempts,
            "status": result.get("status"),
            "retryable": bool(result.get("retryable")),
        }
        log = logger.info if result.get('status') == 'ok' else logger.warning
        log('普通模式工具结束 request_id=%s tool=%s attempt=%s elapsed=%.3f status=%s',
            self.request_id, name, attempts, time.monotonic() - started, result.get('status'))
        return tool_json(result)


def tool_json(result: dict[str, object]) -> str:
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
