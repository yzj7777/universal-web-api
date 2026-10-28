"""uwa-agent 库用法示例：编程式配置 + 订阅事件。

运行（仓库根目录，服务需已启动）::

    python examples/agent_library.py "列出 agent/ 下的文件"

只依赖标准库。
"""

from __future__ import annotations

import sys

from agent.client import BridgeClient
from agent.config import AgentConfig
from agent.display import Printer
from agent.loop import AgentLoop


def main(task: str) -> int:
    config = AgentConfig(
        server="http://127.0.0.1:8199",
        model="auto",
        workdir=".",
        max_steps=10,
    )
    printer = Printer(verbose=False)
    events: list = []

    def on_event(event: dict) -> None:
        events.append(event)
        printer.on_event(event)

    client = BridgeClient(config)
    print(f"using model: {client.resolve_model()}", file=sys.stderr)
    result = AgentLoop(client, config, on_event=on_event).run(task)
    print(result.text)
    print(
        f"[done] stop={result.stop_reason} steps={result.steps} "
        f"tools={result.tool_uses} events={len(events)}",
        file=sys.stderr,
    )
    return 0 if result.stop_reason in ("final", "degraded") else 1


if __name__ == "__main__":
    sys.exit(main(" ".join(sys.argv[1:]) or "列出 agent/ 下的 py 文件"))
