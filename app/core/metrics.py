"""轻量指标注册表（P2-7，DEVELOPMENT.md 10.2）：零第三方依赖的 Prometheus 文本输出。

- Counter：单调累计（按 label 组合分序列）；
- Gauge：瞬时值（最后一次 set）；
- Histogram：固定桶累计（.05/.1/.25/.5/1/2.5/5/10/30 + +Inf），输出 bucket/count/sum；
- render_exposition()：Prometheus exposition 文本（# HELP/# TYPE），供 /api/metrics 输出。

线程安全：单一 RLock 保护全部注册表；v1 单进程语义（多 worker 部署时各进程独立计数，
与限速器同口径——全局聚合留待引入 Redis/部署升级，记 followup）。
"""
from __future__ import annotations

import threading

_lock = threading.RLock()
_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)

# name → (help 文本, type)
_meta: dict[str, tuple[str, str]] = {}
_counters: dict[str, dict[tuple, float]] = {}          # name → {label_key: value}
_gauges: dict[str, tuple[tuple | None, float]] = {}    # name → (label_key, value)
_histos: dict[str, dict[tuple, list[float]]] = {}      # name → {label_key: [bucket.., count, sum]}


def _label_key(labels: dict | None) -> tuple:
    return tuple(sorted((labels or {}).items()))


def _label_str(key: tuple) -> str:
    if not key:
        return ""
    inner = ",".join(f'{k}="{v}"' for k, v in key)
    return "{" + inner + "}"


def _register(name: str, help_text: str, mtype: str) -> None:
    if name not in _meta:
        _meta[name] = (help_text, mtype)


def inc_counter(name: str, labels: dict | None = None, amount: float = 1.0,
                help_text: str = "") -> None:
    """Counter 累加（label 组合分序列）。"""
    with _lock:
        _register(name, help_text or _meta.get(name, ("", "counter"))[0], "counter")
        series = _counters.setdefault(name, {})
        key = _label_key(labels)
        series[key] = series.get(key, 0.0) + amount


def set_gauge(name: str, value: float, labels: dict | None = None,
              help_text: str = "") -> None:
    """Gauge 置为当前值（最后一次写入生效）。"""
    with _lock:
        _register(name, help_text or _meta.get(name, ("", "gauge"))[0], "gauge")
        _gauges[name] = (_label_key(labels), value)


def observe(name: str, value: float, labels: dict | None = None,
            help_text: str = "") -> None:
    """Histogram 观测（固定桶 + count/sum）。"""
    with _lock:
        _register(name, help_text or _meta.get(name, ("", "histogram"))[0], "histogram")
        series = _histos.setdefault(name, {})
        key = _label_key(labels)
        buckets = series.setdefault(key, [0.0] * (len(_BUCKETS) + 2))   # 桶 + count + sum
        for i, bound in enumerate(_BUCKETS):
            if value <= bound:
                buckets[i] += 1
        buckets[-2] += 1
        buckets[-1] += value


def reset() -> None:
    """清空全部指标（仅测试用）。"""
    with _lock:
        _meta.clear()
        _counters.clear()
        _gauges.clear()
        _histos.clear()


def render_exposition() -> str:
    """输出 Prometheus exposition 文本（# HELP/# TYPE + 序列行）。"""
    lines: list[str] = []
    with _lock:
        for name, (help_text, mtype) in _meta.items():
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {mtype}")
            if mtype == "counter" and name in _counters:
                for key, value in sorted(_counters[name].items()):
                    lines.append(f"{name}{_label_str(key)} {value}")
            elif mtype == "gauge" and name in _gauges:
                key, value = _gauges[name]
                lines.append(f"{name}{_label_str(key)} {value}")
            elif mtype == "histogram" and name in _histos:
                for key, buckets in sorted(_histos[name].items()):
                    for i, bound in enumerate(_BUCKETS):
                        lines.append(f'{name}_bucket{_label_str(key + (("le", f"{bound}"),))} '
                                     f"{int(buckets[i])}")
                    lines.append(f'{name}_bucket{_label_str(key + (("le", "+Inf"),))} '
                                 f"{int(buckets[-2])}")
                    lines.append(f"{name}_count{_label_str(key)} {int(buckets[-2])}")
                    lines.append(f"{name}_sum{_label_str(key)} {round(buckets[-1], 6)}")
    return "\n".join(lines) + ("\n" if lines else "")
