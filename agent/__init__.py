"""uwa-agent：为 universal-web-api 网页桥接特调的最小 CLI Agent。

设计要点（详见 docs/agent.md）：
- 工具极简：只有 read_file / bash 两个扁平 schema，从根上降低参数 JSON 错误率；
- 单步单工具：永远 parallel_tool_calls=false，一次只执行一个 tool_call；
- 慢桥接适配：非流式主循环、历史压缩、预算与卡死检测、校验失败本地重试。

本包只依赖 Python 标准库（json_repair 如已安装则用于参数修复兜底），
因此无需安装服务端依赖即可运行::

    python -m agent doctor
    python -m agent run "总结 README.md 的快速开始步骤"
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
