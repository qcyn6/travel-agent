from bisect import bisect_left
from contextlib import asynccontextmanager
from dataclasses import dataclass
import time
from threading import RLock
from typing import Mapping


MetricLabels = Mapping[str, object] | None
_DEFAULT_BUCKETS = (0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 300.0)


@dataclass(frozen=True)
class _MetricDefinition:
    kind: str
    help_text: str
    buckets: tuple[float, ...] = ()


@dataclass
class _Histogram:
    buckets: tuple[float, ...]
    counts: list[int]
    count: int = 0
    total: float = 0.0


class MetricsRegistry:
    """Small dependency-free Prometheus text registry.

    Metrics are deliberately process-local. In a multi-process deployment each
    API/worker process can be scraped separately, or the registry can later be
    replaced by prometheus-client multiprocess mode without changing callers.
    """

    def __init__(self) -> None:
        self._definitions: dict[str, _MetricDefinition] = {}
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], _Histogram
        ] = {}
        self._lock = RLock()

    def define(
        self,
        name: str,
        kind: str,
        help_text: str,
        *,
        buckets: tuple[float, ...] = _DEFAULT_BUCKETS,
    ) -> None:
        if kind not in {"counter", "gauge", "histogram"}:
            raise ValueError(f"不支持的指标类型：{kind}")
        with self._lock:
            existing = self._definitions.get(name)
            if existing is not None and existing.kind != kind:
                raise ValueError(f"指标类型冲突：{name}")
            self._definitions[name] = _MetricDefinition(
                kind=kind,
                help_text=help_text,
                buckets=tuple(sorted(set(buckets))) if kind == "histogram" else (),
            )

    def inc(self, name: str, labels: MetricLabels = None, value: float = 1.0) -> None:
        self._ensure_definition(name, "counter", f"累计计数：{name}")
        key = (name, _labels(labels))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float, labels: MetricLabels = None) -> None:
        self._ensure_definition(name, "gauge", f"当前值：{name}")
        key = (name, _labels(labels))
        with self._lock:
            self._gauges[key] = float(value)

    def change_gauge(self, name: str, delta: float, labels: MetricLabels = None) -> None:
        self._ensure_definition(name, "gauge", f"当前值：{name}")
        key = (name, _labels(labels))
        with self._lock:
            self._gauges[key] = self._gauges.get(key, 0.0) + float(delta)

    def observe(self, name: str, value: float, labels: MetricLabels = None) -> None:
        self._ensure_definition(name, "histogram", f"耗时分布：{name}")
        key = (name, _labels(labels))
        with self._lock:
            definition = self._definitions[name]
            histogram = self._histograms.get(key)
            if histogram is None:
                histogram = _Histogram(
                    buckets=definition.buckets,
                    counts=[0] * len(definition.buckets),
                )
                self._histograms[key] = histogram
            histogram.count += 1
            histogram.total += float(value)
            bucket_index = bisect_left(histogram.buckets, float(value))
            for index in range(bucket_index, len(histogram.buckets)):
                histogram.counts[index] += 1

    def render(self) -> str:
        with self._lock:
            lines: list[str] = []
            for name in sorted(self._definitions):
                definition = self._definitions[name]
                lines.append(f"# HELP {name} {_escape_help(definition.help_text)}")
                lines.append(f"# TYPE {name} {definition.kind}")
                if definition.kind == "counter":
                    samples = (
                        (metric_name, labels, value)
                        for (metric_name, labels), value in self._counters.items()
                        if metric_name == name
                    )
                    lines.extend(_render_sample(*sample) for sample in samples)
                elif definition.kind == "gauge":
                    samples = (
                        (metric_name, labels, value)
                        for (metric_name, labels), value in self._gauges.items()
                        if metric_name == name
                    )
                    lines.extend(_render_sample(*sample) for sample in samples)
                else:
                    for (metric_name, labels), histogram in self._histograms.items():
                        if metric_name != name:
                            continue
                        for bucket, count in zip(
                            histogram.buckets,
                            histogram.counts,
                            strict=True,
                        ):
                            bucket_labels = dict(labels)
                            bucket_labels["le"] = _number(bucket)
                            lines.append(
                                _render_sample(
                                    f"{name}_bucket",
                                    _labels(bucket_labels),
                                    count,
                                )
                            )
                        bucket_labels = dict(labels)
                        bucket_labels["le"] = "+Inf"
                        lines.append(
                            _render_sample(
                                f"{name}_bucket",
                                _labels(bucket_labels),
                                histogram.count,
                            )
                        )
                        lines.append(
                            _render_sample(f"{name}_count", labels, histogram.count)
                        )
                        lines.append(
                            _render_sample(f"{name}_sum", labels, histogram.total)
                        )
            return "\n".join(lines) + ("\n" if lines else "")

    def _ensure_definition(self, name: str, kind: str, help_text: str) -> None:
        with self._lock:
            if name not in self._definitions:
                self._definitions[name] = _MetricDefinition(
                    kind=kind,
                    help_text=help_text,
                    buckets=_DEFAULT_BUCKETS if kind == "histogram" else (),
                )


def _labels(labels: MetricLabels) -> tuple[tuple[str, str], ...]:
    if not labels:
        return ()
    return tuple(sorted((str(key), str(value)) for key, value in labels.items()))


def _render_sample(
    name: str,
    labels: tuple[tuple[str, str], ...],
    value: float | int,
) -> str:
    label_text = ""
    if labels:
        label_text = "{" + ",".join(
            f'{key}="{_escape_label(value)}"' for key, value in labels
        ) + "}"
    return f"{name}{label_text} {_number(value)}"


def _escape_label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _escape_help(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n")


def _number(value: float | int) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


@asynccontextmanager
async def observe_external_request(service: str, operation: str):
    started = time.perf_counter()
    try:
        yield
    except Exception as exc:
        metrics.inc(
            "travel_agent_external_requests_total",
            {"service": service, "operation": operation, "outcome": type(exc).__name__},
        )
        raise
    else:
        metrics.inc(
            "travel_agent_external_requests_total",
            {"service": service, "operation": operation, "outcome": "success"},
        )
    finally:
        metrics.observe(
            "travel_agent_external_request_duration_seconds",
            time.perf_counter() - started,
            {"service": service, "operation": operation},
        )


metrics = MetricsRegistry()
metrics.define(
    "travel_agent_http_requests_total",
    "counter",
    "HTTP 请求总数",
)
metrics.define(
    "travel_agent_http_request_duration_seconds",
    "histogram",
    "HTTP 请求耗时（秒）",
)
metrics.define(
    "travel_agent_planning_jobs_total",
    "counter",
    "规划任务状态转换总数",
)
metrics.define(
    "travel_agent_planning_job_duration_seconds",
    "histogram",
    "规划任务执行耗时（秒）",
)
metrics.define(
    "travel_agent_planning_active_jobs",
    "gauge",
    "当前进程正在执行的规划任务数",
)
metrics.define(
    "travel_agent_planning_sse_connections",
    "gauge",
    "当前 SSE 连接数",
)
metrics.define(
    "travel_agent_planning_sse_duration_seconds",
    "histogram",
    "SSE 连接持续时间（秒）",
)
metrics.define(
    "travel_agent_planning_queue_enqueues_total",
    "counter",
    "规划任务入队总数",
)
metrics.define(
    "travel_agent_planning_queue_receives_total",
    "counter",
    "规划任务出队总数",
)
metrics.define(
    "travel_agent_planning_queue_errors_total",
    "counter",
    "规划任务队列错误总数",
)
metrics.define(
    "travel_agent_planning_lease_reclaims_total",
    "counter",
    "规划任务租约接管总数",
)
metrics.define(
    "travel_agent_alerts_total",
    "counter",
    "告警产生总数",
)
metrics.define(
    "travel_agent_alert_delivery_failures_total",
    "counter",
    "告警发送失败总数",
)
