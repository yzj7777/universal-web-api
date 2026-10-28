"""工具参数的客户端侧修复与归一化（主攻“参数 JSON 错误”失败）。

服务端已经做过一轮 XML 解析 + JSON Schema 校验（失败会直接 500），但
网页端模型仍可能产出“勉强过校验、实际难用”的参数，例如::

    offset: "10"          # 字符串数字
    limit: 10.0           # 浮点数
    path: "  ./a.py\\n"   # 多余空白/引号
    timeout: "30s"        # 带单位

本模块把这些修成干净参数；修不好时返回一条精确的错误文本，作为 tool
结果喂回模型——把“Agent 崩溃”变成“多花一轮自我修正”，这是 ReAct 类
Agent 对弱结构化模型的标准做法。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

try:  # 可选依赖：服务端运行时自带，纯 CLI 环境没有也不影响。
    from json_repair import loads as _json_repair_loads
except Exception:  # pragma: no cover - 缺依赖时退化为标准库
    _json_repair_loads = None


_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)\s*```", re.DOTALL)
_INT_LIKE_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_LIKE_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")


def strip_code_fences(text: str) -> str:
    """去掉模型偶尔加上的 markdown 代码围栏，取首个围栏内容。"""
    value = str(text or "").strip()
    match = _FENCE_RE.search(value)
    if match:
        return match.group(1).strip()
    if value.startswith("```"):
        return value.strip("`").strip()
    return value


def parse_arguments(raw: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """把 arguments 解析为 dict；失败返回 (None, 错误说明)。"""
    if raw is None:
        return {}, ""
    if isinstance(raw, dict):
        return raw, ""
    text = strip_code_fences(raw if isinstance(raw, str) else str(raw))
    if not text:
        return {}, ""
    try:
        decoded = json.loads(text)
    except (TypeError, ValueError):
        decoded = None
    if not isinstance(decoded, dict) and _json_repair_loads is not None:
        try:
            repaired = _json_repair_loads(text)
        except Exception:
            repaired = None
        if isinstance(repaired, dict):
            decoded = repaired
    if isinstance(decoded, dict):
        return decoded, ""
    if isinstance(decoded, list):
        return None, "arguments 必须是 JSON 对象，不能是数组"
    preview = text[:160].replace("\n", "\\n")
    return None, f"arguments 不是合法 JSON 对象（收到：{preview}…）"


def coerce_int(value: Any, *, field: str, default: int, minimum: int, maximum: int) -> Tuple[int, str]:
    """把“10”“10.0”“30s”这类写法归一化为整数，越界则夹紧。"""
    if value is None or value == "":
        return default, ""
    if isinstance(value, bool):
        return default, f"{field} 需要整数，不要传布尔值"
    if isinstance(value, int):
        number = value
    elif isinstance(value, float):
        if value != int(value):
            return default, f"{field} 需要整数（收到小数 {value}）"
        number = int(value)
    elif isinstance(value, str):
        text = value.strip().lower().rstrip("s").strip()
        if _INT_LIKE_RE.match(text or ""):
            number = int(text)
        elif _FLOAT_LIKE_RE.match(text or "") and float(text) == int(float(text)):
            number = int(float(text))
        else:
            return default, f"{field} 需要整数（收到 {value!r}）"
    else:
        return default, f"{field} 需要整数（收到 {type(value).__name__}）"
    return max(minimum, min(maximum, number)), ""


def clean_path(value: Any) -> Tuple[str, str]:
    """清理路径参数：去空白/引号/换行，拒绝空路径。"""
    if not isinstance(value, str):
        return "", f"path 需要字符串（收到 {type(value).__name__}）"
    cleaned = value.strip().strip("\"'").strip()
    cleaned = cleaned.splitlines()[0].strip() if cleaned else ""
    if not cleaned:
        return "", "path 不能为空"
    if "\x00" in cleaned:
        return "", "path 非法"
    return cleaned, ""


def coerce_read_file_args(args: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    path, error = clean_path(args.get("path"))
    if error:
        return {}, error
    offset, error = coerce_int(args.get("offset"), field="offset", default=1, minimum=1, maximum=10_000_000)
    if error:
        return {}, error
    limit, error = coerce_int(args.get("limit"), field="limit", default=200, minimum=1, maximum=2000)
    if error:
        return {}, error
    # 多余字段直接忽略（弱模型常自作主张加字段，不要因此整轮失败）。
    return {"path": path, "offset": offset, "limit": limit}, ""


def coerce_bash_args(args: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    command = args.get("command")
    if not isinstance(command, str) or not command.strip():
        return {}, "command 需要非空字符串"
    cleaned = command.strip()
    if len(cleaned) > 8000:
        return {}, "command 过长（>8000 字符），请拆小执行"
    timeout, error = coerce_int(args.get("timeout"), field="timeout", default=30, minimum=1, maximum=300)
    if error:
        return {}, error
    return {"command": cleaned, "timeout": timeout}, ""


def coerce_tool_args(tool_name: str, raw_arguments: Any) -> Tuple[Dict[str, Any], str]:
    """入口：解析 + 按工具归一化，返回 (干净参数, 错误文本）。"""
    args, error = parse_arguments(raw_arguments)
    if error or args is None:
        return {}, error or "arguments 解析失败"
    if tool_name == "read_file":
        return coerce_read_file_args(args)
    if tool_name == "bash":
        return coerce_bash_args(args)
    return {}, f"未知工具 {tool_name!r}（可用：read_file, bash）"
