"""uwa-agent 命令行：run / chat / doctor / tools。

退出码：0 完成（含降级总结）；2 受控停止（max_steps/loop/wall_time）；
1 其他错误（连接/认证/参数/致命失败）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Sequence

from agent import __version__
from agent.client import BridgeClient, BridgeError
from agent.config import DEFAULT_SERVER, AgentConfig
from agent.display import Printer
from agent.loop import AgentLoop
from agent.tools import TOOL_SCHEMAS

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_STOPPED = 2


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name, default) or "").strip()


def _common_parser() -> argparse.ArgumentParser:
    """子命令复用的全局选项（default=SUPPRESS：不覆盖主解析器的值）。

    这样 ``agent --server X run ...`` 与 ``agent run --server X ...`` 都能用。
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--server", default=argparse.SUPPRESS,
                        help="桥接服务根地址（默认 http://127.0.0.1:8199；也接受 …/v1 形式）")
    common.add_argument("--route", default=argparse.SUPPRESS,
                        help="路由前缀，如 /url/gemini.google.com、/tab/2、/group/abc")
    common.add_argument("--api-key", default=argparse.SUPPRESS,
                        help="服务 API 令牌（AUTH_ENABLED=true 时必填）")
    common.add_argument("--model", default=argparse.SUPPRESS,
                        help="模型 id，auto 表示从 /v1/models 自动选择")
    common.add_argument("--workdir", default=argparse.SUPPRESS,
                        help="工具工作目录（默认当前目录）")
    common.add_argument("--max-steps", type=int, default=argparse.SUPPRESS)
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                        help="单轮 LLM 请求超时秒数（桥接慢，默认 300）")
    common.add_argument("--max-wall", type=float, default=argparse.SUPPRESS,
                        help="任务墙钟预算秒数（默认 1200）")
    common.add_argument("--allow-outside-workdir", action="store_true", default=argparse.SUPPRESS,
                        help="允许 read_file 读取工作目录外的文件（默认禁止）")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="uwa-agent",
        description="为 universal-web-api 网页桥接特调的最小 CLI Agent（read_file + bash）。",
    )
    parser.add_argument("--version", action="version", version=f"uwa-agent {__version__}")
    parser.add_argument("--server", default=_env("UWA_SERVER") or _env("UWA_BASE_URL") or DEFAULT_SERVER,
                        help="桥接服务根地址（默认 http://127.0.0.1:8199；也接受 …/v1 形式）")
    parser.add_argument("--route", default=_env("UWA_ROUTE"),
                        help="路由前缀，如 /url/gemini.google.com、/tab/2、/group/abc")
    parser.add_argument("--api-key", default=_env("UWA_API_KEY"),
                        help="服务 API 令牌（AUTH_ENABLED=true 时必填）")
    parser.add_argument("--model", default=_env("UWA_MODEL") or "auto",
                        help="模型 id，auto 表示从 /v1/models 自动选择")
    parser.add_argument("--workdir", default=_env("UWA_WORKDIR") or ".",
                        help="工具工作目录（默认当前目录）")
    parser.add_argument("--max-steps", type=int, default=int(_env("UWA_MAX_STEPS") or 25))
    parser.add_argument("--timeout", type=float, default=float(_env("UWA_TIMEOUT") or 300),
                        help="单轮 LLM 请求超时秒数（桥接慢，默认 300）")
    parser.add_argument("--max-wall", type=float, default=float(_env("UWA_MAX_WALL") or 1200),
                        help="任务墙钟预算秒数（默认 1200）")
    parser.add_argument("--allow-outside-workdir", action="store_true",
                        help="允许 read_file 读取工作目录外的文件（默认禁止）")
    parser.add_argument("-v", "--verbose", action="store_true")

    common = _common_parser()
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", parents=[common], help="执行单个任务并输出最终答案")
    run_p.add_argument("task", nargs="*", help="任务描述")
    run_p.add_argument("--file", help="从文件读取任务描述（与 task 二选一）")

    sub.add_parser("chat", parents=[common], help="交互式多轮对话（/reset /tools /quit）")
    doctor_p = sub.add_parser("doctor", parents=[common], help="检查服务连通性、模型与延迟")
    doctor_p.add_argument("--smoke", action="store_true",
                          help="多做一轮真实工具调用冒烟（慢，约几十秒）")
    sub.add_parser("tools", parents=[common], help="打印内置工具的 JSON Schema")
    return parser


def make_config(args: argparse.Namespace) -> AgentConfig:
    return AgentConfig(
        server=args.server,
        route=args.route,
        api_key=args.api_key,
        model=args.model,
        workdir=args.workdir,
        max_steps=args.max_steps,
        max_wall_seconds=args.max_wall,
        request_timeout=args.timeout,
        allow_outside_workdir=args.allow_outside_workdir,
        verbose=args.verbose,
    )


def cmd_tools(printer: Printer) -> int:
    print(json.dumps(TOOL_SCHEMAS, ensure_ascii=False, indent=2))
    printer.line(printer.dim("提示：schema 刻意保持扁平，不要自行加嵌套字段。"))
    return EXIT_OK


def cmd_doctor(config: AgentConfig, printer: Printer, *, smoke: bool) -> int:
    client = BridgeClient(config)
    printer.line(f"服务：{config.server}  路由：{config.route or '(默认)'}  模型：{config.model}")
    try:
        health, latency = client.health()
    except BridgeError as exc:
        printer.line(printer.red(f"✗ 健康检查失败：{exc}"))
        printer.line("  请确认：1) 服务已启动（uv run python main.py）；"
                     "2) 受控浏览器在线；3) --server/--api-key 正确。")
        return EXIT_ERROR
    printer.line(f"✓ 健康检查 ok（{latency * 1000:.0f}ms）：{Printer.preview(health, 160)}")
    try:
        models = client.list_models()
    except BridgeError as exc:
        printer.line(printer.red(f"✗ 模型列表失败：{exc}"))
        return EXIT_ERROR
    if not models:
        printer.line(printer.red("✗ 模型列表为空：受控浏览器可能没有可用标签页。"))
        return EXIT_ERROR
    printer.line(f"✓ 模型 {len(models)} 个：{', '.join(models[:8])}"
                 + (" …" if len(models) > 8 else ""))
    try:
        resolved = client.resolve_model()
    except BridgeError as exc:
        printer.line(printer.red(f"✗ 模型解析失败：{exc}"))
        return EXIT_ERROR
    printer.line(f"✓ 本次将使用模型：{resolved}")
    if not smoke:
        printer.line(printer.dim("加 --smoke 可再做一轮真实工具调用冒烟测试（慢）。"))
        return EXIT_OK
    # 冒烟：让模型读一个我们刚写好的标记文件，验证端到端工具调用。
    marker = Path(config.workdir) / ".uwa_smoke.txt"
    try:
        marker.write_text("uwa-smoke-ok\n", encoding="utf-8")
        from agent.loop import AgentLoop as Loop
        loop = Loop(client, config, on_event=printer.on_event,
                    workdir=str(Path(config.workdir).resolve()))
        started = time.monotonic()
        result = loop.run(f"用 read_file 读 {marker.name}，然后只回答文件内容。")
        elapsed = time.monotonic() - started
    finally:
        try:
            marker.unlink()
        except OSError:
            pass
    if result.stop_reason in ("final", "degraded") and "uwa-smoke-ok" in result.text:
        printer.line(printer.green(f"✓ 冒烟通过（{elapsed:.0f}s，{result.tool_uses} 次工具调用）"))
        return EXIT_OK
    printer.line(printer.red(f"✗ 冒烟失败（stop={result.stop_reason}）：{result.text[:300]}"))
    return EXIT_ERROR


def cmd_run(config: AgentConfig, printer: Printer, task: str) -> int:
    task = task.strip()
    if not task:
        printer.line(printer.red("任务为空。"))
        return EXIT_ERROR
    workdir = str(Path(config.workdir).resolve())
    if not Path(workdir).is_dir():
        printer.line(printer.red(f"工作目录不存在：{workdir}"))
        return EXIT_ERROR
    client = BridgeClient(config)
    try:
        model = client.resolve_model()
    except BridgeError as exc:
        printer.line(printer.red(f"模型解析失败：{exc}"))
        return EXIT_ERROR
    printer.line(f"模型：{model}  工作目录：{workdir}  步数上限：{config.max_steps}")
    loop = AgentLoop(client, config, on_event=printer.on_event, workdir=workdir)
    try:
        result = loop.run(task)
    except KeyboardInterrupt:
        printer.line(printer.yellow("\n已中断。"))
        return EXIT_STOPPED
    # 最终答案走 stdout，进度走 stderr。
    print(result.text)
    printer.line(printer.dim(f"[done] stop={result.stop_reason} "
                             f"steps={result.steps} tools={result.tool_uses}"))
    if result.stop_reason in ("final", "degraded"):
        return EXIT_OK
    if result.stop_reason in ("max_steps", "loop", "wall_time"):
        return EXIT_STOPPED
    return EXIT_ERROR


def cmd_chat(config: AgentConfig, printer: Printer) -> int:
    workdir = str(Path(config.workdir).resolve())
    if not Path(workdir).is_dir():
        printer.line(printer.red(f"工作目录不存在：{workdir}"))
        return EXIT_ERROR
    client = BridgeClient(config)
    try:
        model = client.resolve_model()
    except BridgeError as exc:
        printer.line(printer.red(f"模型解析失败：{exc}"))
        return EXIT_ERROR
    printer.line(f"uwa-agent chat（模型 {model}，工作目录 {workdir}）")
    printer.line("命令：/reset 清空历史  /tools 查看工具  /quit 退出")
    loop = AgentLoop(client, config, on_event=printer.on_event, workdir=workdir)
    history: List[dict] = []
    while True:
        try:
            user_input = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            printer.line("\n再见。")
            return EXIT_OK
        if not user_input:
            continue
        if user_input in ("/quit", "/exit", "/q"):
            return EXIT_OK
        if user_input == "/reset":
            history = []
            printer.line(printer.dim("历史已清空。"))
            continue
        if user_input == "/tools":
            printer.line(json.dumps([s["function"]["name"] for s in TOOL_SCHEMAS]))
            continue
        try:
            result = loop.run(user_input, history=history)
        except KeyboardInterrupt:
            printer.line(printer.yellow("\n本轮已中断（历史保留）。"))
            continue
        print(f"\n{result.text}")
        printer.line(printer.dim(f"[done] stop={result.stop_reason} "
                                 f"steps={result.steps} tools={result.tool_uses}"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = make_config(args)
    printer = Printer(verbose=config.verbose)
    if args.command == "tools":
        return cmd_tools(printer)
    if args.command == "doctor":
        return cmd_doctor(config, printer, smoke=args.smoke)
    if args.command == "chat":
        return cmd_chat(config, printer)
    if args.command == "run":
        if args.file:
            try:
                task = Path(args.file).read_text(encoding="utf-8")
            except OSError as exc:
                printer.line(printer.red(f"读取任务文件失败：{exc}"))
                return EXIT_ERROR
        else:
            task = " ".join(args.task)
        return cmd_run(config, printer, task)
    parser.print_help()
    return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
