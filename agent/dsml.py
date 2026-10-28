"""工具调用“方言”的客户端恢复解析。

实战发现（2026-10-28，DeepSeek 网页端）：模型偶尔不按桥接要求的
``<adapter_calls>`` 格式回，而是吐出自家 DSML 标记::

    <｜｜DSML｜｜ calls>
    <｜｜DSML｜｜ invoke name="read_file">
    <｜｜DSML｜｜ parameter name="path">.uwa_smoke.txt</｜｜DSML｜｜ parameter>
    </｜｜DSML｜｜ invoke>
    </｜｜DSML｜｜ calls>

桥接不认识这种格式，会把它当普通终答返回（``finish_reason=stop``）。
本模块在 Agent 侧做最后一道兜底：终答文本里如果“基本只有”工具调用
标记，就地解析并执行，避免一轮慢请求被浪费。

服务端（``app/services/tool_calling_parse.py``）有一份镜像实现，
两者用同一套正则思想，但各自归一化（服务端按 JSON Schema 强转类型，
客户端保持字符串、复用 ``repair.coerce_tool_args`` 宽容归一化）。

安全规则（防误执行“讲解示例”）：只有标记外 prose ≤120 字符时才自动
执行；混在长正文里的标记视为讲解，直接当终接受。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: 标记外允许的 prose 上限（字符）。纯标记终答通常只有换行/短句。
OUTSIDE_PROSE_LIMIT = 120

#: DSML 竖线：全角 ｜（U+FF5C）、制表 │（U+2502）、半角 |，成对出现。
_DSML_BAR = r"[\uFF5C\u2502|]{2}"
_DSML_OPEN = r"<\s*" + _DSML_BAR + r"\s*DSML\s*" + _DSML_BAR
_DSML_CLOSE = r"</\s*" + _DSML_BAR + r"\s*DSML\s*" + _DSML_BAR

_DSML_INVOKE_RE = re.compile(
    _DSML_OPEN + r"\s*invoke\b([^>]*)>(.*?)"
    + _DSML_CLOSE + r"\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    _DSML_OPEN + r"\s*parameter\b([^>]*)>(.*?)"
    + _DSML_CLOSE + r"\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_TAG_RE = re.compile(_DSML_OPEN + r"\s*[a-z]+\b[^>]*>", re.IGNORECASE)
_DSML_CLOSE_TAG_RE = re.compile(_DSML_CLOSE + r"\s*[a-z]+\s*>", re.IGNORECASE)

#: 宽松 XML：桥接严格解析失败（如多余属性）时的残骸。
_XML_CALL_RE = re.compile(
    r"<\s*(?:call|invoke|tool_call)\b([^>]*)>(.*?)"
    r"</\s*(?:call|invoke|tool_call)\s*>",
    re.IGNORECASE | re.DOTALL,
)
_XML_ARGS_RE = re.compile(
    r"<\s*arguments\b[^>]*>(.*?)</\s*arguments\s*>",
    re.IGNORECASE | re.DOTALL,
)
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_NAME_ATTR_RE = re.compile(r"""\bname\s*=\s*(['"])(.*?)\1""", re.DOTALL)


@dataclass
class ToolAttempt:
    name: str
    args: Any
    start: int = 0
    end: int = 0
    kind: str = "dsml"  # dsml | xml
    meta: Dict[str, Any] = field(default_factory=dict)


def _attr_name(attr_text: str) -> str:
    match = _NAME_ATTR_RE.search(attr_text or "")
    return str(match.group(2) or "").strip() if match else ""


def find_dsml_calls(text: str) -> List[ToolAttempt]:
    """解析 DSML invoke 块。参数值保持原始字符串（归一化交给 repair）。"""
    value = str(text or "")
    if "dsml" not in value.lower():
        return []
    attempts: List[ToolAttempt] = []
    for match in _DSML_INVOKE_RE.finditer(value):
        name = _attr_name(match.group(1))
        if not name:
            continue
        args: Dict[str, str] = {}
        for param in _DSML_PARAM_RE.finditer(match.group(2) or ""):
            param_name = _attr_name(param.group(1))
            if not param_name:
                continue
            args[param_name] = str(param.group(2) or "").strip()
        attempts.append(ToolAttempt(name=name, args=args, start=match.start(),
                                    end=match.end(), kind="dsml"))
    return attempts


def find_lenient_xml_calls(text: str) -> List[ToolAttempt]:
    """宽松解析残留的 XML 工具调用（桥接严格解析未命中的情况）。"""
    value = str(text or "")
    attempts: List[ToolAttempt] = []
    for match in _XML_CALL_RE.finditer(value):
        name = _attr_name(match.group(1))
        if not name:
            continue
        args_match = _XML_ARGS_RE.search(match.group(2) or "")
        if not args_match:
            continue
        payload = str(args_match.group(1) or "").strip()
        cdata = _CDATA_RE.search(payload)
        if cdata:
            payload = str(cdata.group(1) or "").strip()
        try:
            decoded = json.loads(payload) if payload else {}
        except (TypeError, ValueError):
            continue
        if not isinstance(decoded, dict):
            continue
        attempts.append(ToolAttempt(name=name, args=decoded, start=match.start(),
                                    end=match.end(), kind="xml"))
    return attempts


def find_tool_attempts(text: str) -> List[ToolAttempt]:
    """DSML 优先，其次宽松 XML；重叠时只保留 DSML。"""
    dsml = find_dsml_calls(text)
    if dsml:
        return dsml
    return find_lenient_xml_calls(text)


def strip_attempt_blocks(text: str, attempts: List[ToolAttempt]) -> str:
    """去掉工具标记块（含残留的 DSML calls 包装标签），返回外部 prose。"""
    value = str(text or "")
    if not attempts:
        return value
    spans = sorted((a.start, a.end) for a in attempts)
    parts: List[str] = []
    last = 0
    for start, end in spans:
        if start > last:
            parts.append(value[last:start])
        last = max(last, end)
    parts.append(value[last:])
    stripped = "".join(parts)
    stripped = _DSML_TAG_RE.sub("", stripped)
    stripped = _DSML_CLOSE_TAG_RE.sub("", stripped)
    return stripped


def _attempts_and_outside(text: str) -> Tuple[List[ToolAttempt], str]:
    attempts = find_tool_attempts(text)
    outside = strip_attempt_blocks(text, attempts).strip() if attempts else str(text or "")
    return attempts, outside


def recover_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """纯标记终答 → OpenAI 风格 tool_call；不满足自动执行条件返回 None。"""
    from agent.repair import coerce_tool_args
    from agent.tools import TOOL_NAMES  # 延迟导入，避免循环依赖

    attempts, outside = _attempts_and_outside(text)
    if not attempts or len(outside) > OUTSIDE_PROSE_LIMIT:
        return None
    first = attempts[0]
    if first.name not in TOOL_NAMES:
        return None
    args, error = coerce_tool_args(first.name, first.args)
    if error:
        return None
    return {
        "id": f"call_recovered_{uuid.uuid4().hex[:8]}",
        "type": "function",
        "function": {"name": first.name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def needs_markup_retry(text: str) -> bool:
    """有纯标记但恢复失败（未知工具/坏参数）→ 值得 nudge 模型重发一次。"""
    attempts, outside = _attempts_and_outside(text)
    if not attempts or len(outside) > OUTSIDE_PROSE_LIMIT:
        return False
    return recover_tool_call(text) is None
