"""内置工具：read_file + bash（刻意只做两个）。

Schema 设计是“主攻参数 JSON 错误”的关键一环：

- 扁平对象：每个工具只有 1 个必填字符串 + 0~2 个可选整数；
- 无嵌套、无 anyOf、无 enum、无 additionalProperties:false——
  网页端模型最容易在这四类结构上产出非法 JSON；
- 多余字段服务端放行、客户端忽略，不整轮失败。

安全：read_file 默认沙箱在 workdir 内；bash 有超时、输出上限与灾难
命令拦截。但这只是护栏而非沙箱：不要在不可信任务中使用。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List

from agent.repair import coerce_tool_args

READ_FILE_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file under the working directory. "
            "Returns numbered lines. Use offset/limit for large files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path from the working directory, e.g. 'src/app.py'.",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based first line to read. Default 1.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max lines to return (1-2000). Default 200.",
                },
            },
            "required": ["path"],
        },
    },
}

BASH_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": (
            "Run one non-interactive shell command in the working directory. "
            "Returns exit code plus stdout/stderr. No TTY, no interaction, no sudo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Single command line, e.g. 'ls src && grep -rn foo src | head'.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (1-300). Default 30.",
                },
            },
            "required": ["command"],
        },
    },
}

TOOL_SCHEMAS: List[Dict[str, Any]] = [READ_FILE_SCHEMA, BASH_SCHEMA]
TOOL_NAMES: List[str] = ["read_file", "bash"]

MAX_READ_BYTES = 2 * 1024 * 1024
MAX_READ_OUTPUT_CHARS = 12000
MAX_BASH_OUTPUT_CHARS = 20000

#: 灾难命令拦截（正则，命中即拒绝执行）。护栏非沙箱。
_DANGEROUS_PATTERNS = (
    re.compile(r":\(\)\s*\{[^}]*\}\s*;"),  # fork 炸弹
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\b[^\n]*\bof\s*=\s*/dev/"),
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+/(?:\s|$)"),  # rm -rf /
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f?\s+/(?:bin|boot|etc|lib|proc|sbin|sys|usr|var)\b"),
    re.compile(r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b"),
    re.compile(r">\s*/dev/(?:sd[a-z]|hd[a-z]|nvme)", re.IGNORECASE),
)


@dataclass
class ToolResult:
    ok: bool
    text: str
    name: str = ""
    elapsed: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


def _resolve_inside(workdir: Path, raw_path: str, *, allow_outside: bool) -> Path:
    candidate = (workdir / raw_path).resolve() if not os.path.isabs(raw_path) else Path(raw_path).resolve()
    if allow_outside:
        return candidate
    try:
        candidate.relative_to(workdir.resolve())
    except ValueError:
        raise PermissionError(f"拒绝访问工作目录外文件：{raw_path}")
    return candidate


def execute_read_file(args: Dict[str, Any], workdir: str, *, allow_outside: bool = False) -> ToolResult:
    started = time.monotonic()
    name = "read_file"
    try:
        target = _resolve_inside(Path(workdir), str(args["path"]), allow_outside=allow_outside)
    except PermissionError as exc:
        return ToolResult(False, f"[read_file] ERROR: {exc}", name=name)
    if not target.exists():
        return ToolResult(False, f"[read_file] ERROR: 文件不存在：{args['path']}（工作目录 {workdir}）", name=name)
    if not target.is_file():
        return ToolResult(False, f"[read_file] ERROR: 不是普通文件：{args['path']}", name=name)
    try:
        if target.stat().st_size > MAX_READ_BYTES:
            return ToolResult(
                False,
                f"[read_file] ERROR: 文件过大（>{MAX_READ_BYTES // 1024}KB），请用 bash 分段查看：{args['path']}",
                name=name,
            )
        raw = target.read_bytes()
    except OSError as exc:
        return ToolResult(False, f"[read_file] ERROR: 读取失败：{exc}", name=name)
    if b"\x00" in raw[:8192]:
        return ToolResult(False, f"[read_file] ERROR: 疑似二进制文件，拒绝读取：{args['path']}", name=name)
    lines = raw.decode("utf-8", "replace").splitlines()
    total = len(lines)
    offset = max(1, int(args.get("offset", 1)))
    limit = max(1, int(args.get("limit", 200)))
    if total == 0:
        return ToolResult(True, f"[read_file] ok path={args['path']} (空文件)", name=name,
                           elapsed=time.monotonic() - started)
    if offset > total:
        return ToolResult(
            False,
            f"[read_file] ERROR: offset={offset} 超出总行数 {total}：{args['path']}",
            name=name, elapsed=time.monotonic() - started,
        )
    picked = lines[offset - 1: offset - 1 + limit]
    numbered = "\n".join(f"{number}| {line}" for number, line in enumerate(picked, start=offset))
    end = offset + len(picked) - 1
    hint = "" if end >= total else f" (还有 {total - end} 行，用 offset={end + 1} 继续读)"
    text = f"[read_file] ok path={args['path']} lines={offset}-{end}/{total}{hint}\n{numbered}"
    if len(text) > MAX_READ_OUTPUT_CHARS:
        text = text[:MAX_READ_OUTPUT_CHARS] + "\n…(输出截断，缩小 limit 或分段读取)"
    return ToolResult(True, text, name=name, elapsed=time.monotonic() - started,
                       meta={"lines": total, "offset": offset, "limit": limit})


def _blocked_command(command: str) -> str:
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            return pattern.pattern
    return ""


def execute_bash(args: Dict[str, Any], workdir: str) -> ToolResult:
    started = time.monotonic()
    name = "bash"
    command = str(args["command"])
    timeout = int(args.get("timeout", 30))
    blocked = _blocked_command(command)
    if blocked:
        return ToolResult(False, f"[bash] ERROR: 命令命中危险模式被拒绝执行：{blocked}", name=name)
    if sys.platform.startswith("win"):
        argv: List[str] = ["cmd", "/c", command]
    else:
        argv = ["bash", "-c", command]
    try:
        completed = subprocess.run(
            argv, cwd=workdir, capture_output=True, text=True,
            timeout=timeout, errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") + (exc.stderr or "")
        preview = partial[-2000:] if partial else "(无输出)"
        return ToolResult(
            False,
            f"[bash] ERROR: 执行超时（{timeout}s），进程已终止。末尾输出：\n{preview}",
            name=name, elapsed=time.monotonic() - started,
        )
    except OSError as exc:
        return ToolResult(False, f"[bash] ERROR: 启动失败：{exc}", name=name)
    elapsed = time.monotonic() - started
    stdout = (completed.stdout or "").rstrip()
    stderr = (completed.stderr or "").rstrip()
    parts = [f"[bash] exit={completed.returncode} elapsed={elapsed:.1f}s cmd={command[:200]}"]
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(f"--- stderr ---\n{stderr}" if stdout else stderr)
    if not stdout and not stderr:
        parts.append("(无输出)")
    text = "\n".join(parts)
    if len(text) > MAX_BASH_OUTPUT_CHARS:
        head = int(MAX_BASH_OUTPUT_CHARS * 0.7)
        text = text[:head] + f"\n…(输出 {len(text)} 字符已截断，请加过滤条件重查)" + text[-4000:]
    ok = completed.returncode == 0
    if not ok:
        text = text.replace("[bash]", "[bash] ERROR:", 1)
    return ToolResult(ok, text, name=name, elapsed=elapsed,
                       meta={"exit_code": completed.returncode})


Executor = Callable[[Dict[str, Any], str], ToolResult]


def dispatch(
    tool_name: str,
    raw_arguments: Any,
    workdir: str,
    *,
    allow_outside_workdir: bool = False,
) -> ToolResult:
    """统一入口：参数修复 → 执行。永远返回 ToolResult，不抛异常。"""
    started = time.monotonic()
    name = str(tool_name or "").strip()
    if name not in TOOL_NAMES:
        return ToolResult(
            False,
            f"[{name or 'tool'}] ERROR: 未知工具 {name!r}，可用工具只有：read_file, bash。"
            "请只用这两个工具之一重试（一次只调一个）。",
            name=name,
        )
    args, error = coerce_tool_args(name, raw_arguments)
    if error:
        return ToolResult(
            False,
            f"[{name}] ERROR: 参数错误：{error}。"
            f"正确示例：{example_args(name)}。请修正后重试（一次只调一个）。",
            name=name, elapsed=time.monotonic() - started,
        )
    try:
        if name == "read_file":
            return execute_read_file(args, workdir, allow_outside=allow_outside_workdir)
        return execute_bash(args, workdir)
    except Exception as exc:  # 防御：执行器永不把异常抛给主循环
        return ToolResult(False, f"[{name}] ERROR: 执行异常：{exc}", name=name,
                           elapsed=time.monotonic() - started)


def example_args(tool_name: str) -> str:
    if tool_name == "bash":
        return '{"command": "ls src | head -50"}'
    return '{"path": "README.md", "offset": 1, "limit": 100}'
