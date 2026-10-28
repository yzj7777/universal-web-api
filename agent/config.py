"""Agent 配置：CLI 参数 > 环境变量 > 默认值。"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_SERVER = "http://127.0.0.1:8199"
DEFAULT_MODEL = "auto"


def normalize_server(value: str) -> str:
    """接受 ``http://host:port`` 或 ``http://host:port/v1``，统一为服务根地址。"""
    server = str(value or "").strip().rstrip("/")
    if server.lower().endswith("/v1"):
        server = server[: -len("/v1")].rstrip("/")
    return server or DEFAULT_SERVER


def normalize_route(value: str) -> str:
    """路由前缀：``/url/<domain>``、``/tab/<n>``、``/group/<id>`` 或空字符串。"""
    route = str(value or "").strip()
    if not route:
        return ""
    if not route.startswith("/"):
        route = "/" + route
    return route.rstrip("/")


@dataclass
class AgentConfig:
    server: str = DEFAULT_SERVER
    route: str = ""
    api_key: str = ""
    model: str = DEFAULT_MODEL
    workdir: str = "."
    max_steps: int = 25
    max_wall_seconds: float = 1200.0
    request_timeout: float = 300.0
    # 单条工具结果进历史前截断；总历史字符预算；最近保留消息数。
    max_tool_result_chars: int = 6000
    max_history_chars: int = 60000
    keep_recent_messages: int = 12
    # 校验失败（500 tool_call_validation_exhausted）时，本轮本地简化重试次数。
    validation_retries: int = 2
    # 传输层可重试错误（429/503/超时/断连）的重试次数。
    transport_retries: int = 3
    # 步间等待秒数：Agent 每步都是一次浏览器 roundtrip，连打会被站点限流
    # （DeepSeek 网页端约 7 轮后报“发送过于频繁”）。0 表示关闭。
    step_delay: float = 3.0
    allow_outside_workdir: bool = False
    verbose: bool = False

    def __post_init__(self) -> None:
        self.server = normalize_server(self.server)
        self.route = normalize_route(self.route)
        self.model = str(self.model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
        self.max_steps = max(1, int(self.max_steps or 1))
        self.max_wall_seconds = max(30.0, float(self.max_wall_seconds or 0))
        self.request_timeout = max(10.0, float(self.request_timeout or 0))
        self.max_tool_result_chars = max(500, int(self.max_tool_result_chars or 0))
        self.max_history_chars = max(4000, int(self.max_history_chars or 0))
        self.keep_recent_messages = max(4, int(self.keep_recent_messages or 0))
        self.validation_retries = max(0, min(5, int(self.validation_retries or 0)))
        self.transport_retries = max(0, min(10, int(self.transport_retries or 0)))
        self.step_delay = max(0.0, min(120.0, float(self.step_delay or 0)))

    @property
    def chat_url(self) -> str:
        return f"{self.server}{self.route}/v1/chat/completions"

    @property
    def models_url(self) -> str:
        return f"{self.server}/v1/models"

    @property
    def health_url(self) -> str:
        return f"{self.server}/health"

    @property
    def auth_headers(self) -> dict:
        if not str(self.api_key or "").strip():
            return {}
        return {"Authorization": f"Bearer {self.api_key.strip()}"}
