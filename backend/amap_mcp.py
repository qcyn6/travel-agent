"""Shared client helpers for the official Amap Streamable HTTP MCP server."""

import json
from collections.abc import Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from backend.observability import observe_external_request


class AmapMcpError(RuntimeError):
    """Base error for Amap MCP connection and tool failures."""


class AmapMcpConfigurationError(AmapMcpError):
    """The MCP client cannot be configured from the current settings."""


class AmapMcpAPIError(AmapMcpError):
    """The MCP endpoint or a tool returned an unusable result."""


class AmapMcpClient:
    """Small protocol adapter that hides MCP session and JSON response details.

    A session can be shared by a caller that uses ``session_scope``. Ordinary
    calls open and close one session automatically. The active session is kept
    in a task-local context so concurrent calls on the same adapter cannot
    overwrite each other's session.
    """

    def __init__(
        self,
        api_key: str | None,
        *,
        endpoint: str = "https://mcp.amap.com/mcp",
        timeout_seconds: float = 15.0,
        sse_read_timeout_seconds: float = 20.0,
    ):
        if not api_key:
            raise AmapMcpConfigurationError("未配置 AMAP_API_KEY")
        if not endpoint.strip():
            raise AmapMcpConfigurationError("未配置高德 MCP 地址")

        self._api_key = api_key
        self._endpoint = endpoint.strip()
        self._timeout_seconds = timeout_seconds
        self._sse_read_timeout_seconds = sse_read_timeout_seconds
        self._session_context: ContextVar[object | None] = ContextVar(
            f"amap_mcp_session_{id(self)}",
            default=None,
        )

    @asynccontextmanager
    async def session_scope(self):
        """Open one initialized MCP session for a group of tool calls."""

        try:
            async with streamablehttp_client(
                self._endpoint_with_key(),
                timeout=self._timeout_seconds,
                sse_read_timeout=self._sse_read_timeout_seconds,
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    token: Token = self._session_context.set(session)
                    try:
                        yield self
                    finally:
                        self._session_context.reset(token)
        except AmapMcpError:
            raise
        except Exception as exc:
            raise AmapMcpAPIError("高德 MCP 连接失败") from exc

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, str],
        *,
        operation: str,
    ) -> dict[str, object]:
        """Call a tool and normalize its JSON text payload to a dictionary."""

        async with observe_external_request("amap_mcp", operation):
            session = self._session_context.get()
            if session is not None:
                return await self._call_tool(session, name, arguments)
            async with self.session_scope():
                session = self._session_context.get()
                if session is None:
                    raise AmapMcpAPIError("高德 MCP 会话初始化失败")
                return await self._call_tool(session, name, arguments)

    async def _call_tool(self, session, name: str, arguments: dict[str, str]) -> dict[str, object]:
        try:
            result = await session.call_tool(name, arguments)
        except AmapMcpError:
            raise
        except Exception as exc:
            raise AmapMcpAPIError(f"高德 MCP 工具调用失败：{name}") from exc
        return tool_payload(result, f"高德 MCP 工具 {name}")

    def _endpoint_with_key(self) -> str:
        parsed = urlsplit(self._endpoint)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        if not any(name == "key" for name, _ in query):
            query.append(("key", self._api_key))
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )


def tool_payload(result, label: str) -> dict[str, object]:
    """Extract the JSON object returned in an MCP ``CallToolResult``."""

    if getattr(result, "isError", False):
        raise AmapMcpAPIError(f"{label}失败")

    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, Mapping):
        return dict(structured)

    for block in getattr(result, "content", []) or []:
        text = block.get("text") if isinstance(block, Mapping) else getattr(block, "text", None)
        if not isinstance(text, str) or not text.strip():
            continue
        payload = parse_json(text)
        if isinstance(payload, Mapping):
            return dict(payload)
    raise AmapMcpAPIError(f"{label}返回格式异常")


def parse_json(text: str):
    normalized = text.strip()
    if normalized.startswith("```"):
        normalized = normalized.strip("`").strip()
        if normalized.startswith("json"):
            normalized = normalized[4:].strip()
    try:
        return json.loads(normalized)
    except (TypeError, ValueError):
        start = normalized.find("{")
        end = normalized.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            return json.loads(normalized[start : end + 1])
        except (TypeError, ValueError):
            return None


__all__ = [
    "AmapMcpAPIError",
    "AmapMcpClient",
    "AmapMcpConfigurationError",
    "AmapMcpError",
    "parse_json",
    "tool_payload",
]
