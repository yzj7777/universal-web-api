"""``app/api/system`` 的 health 分组（原 app/api/system.py 的一部分）。"""

import time
from app import __version__ as APP_VERSION
from fastapi import Request, Depends
from fastapi.responses import JSONResponse
from app.core.config import AppConfig, log_collector
from app.api.deps import verify_dashboard_auth as verify_auth
from app.services.config_engine import config_engine
from app.api.system import runtime
from app.api.system.common import (
    logger,
    router,
)

def _public_health_response() -> JSONResponse:
    """S7：匿名远程调用者的最小健康响应。

    不调用 ``browser.health_check()``（它会主动连接/重连浏览器），只读当前连接标志；
    保留控制面板登录前需要的 ``dashboard_auth_enabled``，以及原有 200/503 语义。
    """
    try:
        browser = runtime.get_browser(auto_connect=False)
        connected = bool(getattr(browser, "_connected", False)) and bool(
            getattr(browser, "browser_handle", None) is not None
        )
    except Exception:
        connected = False
    dashboard_auth = AppConfig.is_dashboard_auth_enabled()
    response = {
        "service": "healthy",
        "browser": {"connected": connected},
        "config": {
            "auth_enabled": dashboard_auth,
            "dashboard_auth_enabled": dashboard_auth,
        },
        "detail": "restricted",
        "timestamp": int(time.time()),
    }
    return JSONResponse(content=response, status_code=200 if connected else 503)


@router.get("/health")
async def health_check(request: Request):
    """服务健康检查（本机直连或带有效令牌时返回详细诊断，否则最小响应）"""
    from app.utils.diagnostics_access import request_is_privileged

    if not request_is_privileged(request):
        return _public_health_response()

    try:
        browser = runtime.get_browser(auto_connect=False)
        browser_health = browser.health_check()
    except Exception as e:
        browser_health = {"connected": False, "error": str(e)}

    rm_status = runtime.request_manager.get_status()

    response = {
        "service": "healthy",
        "version": APP_VERSION,
        "browser": browser_health,
        "request_manager": rm_status,
        "config": {
            "sites_loaded": len(config_engine.sites),
            "auth_enabled": AppConfig.is_dashboard_auth_enabled(),
            "dashboard_auth_enabled": AppConfig.is_dashboard_auth_enabled(),
            "service_auth_enabled": AppConfig.is_auth_enabled(),
        },
        "timestamp": int(time.time())
    }

    status_code = 200 if browser_health.get("connected") else 503
    return JSONResponse(content=response, status_code=status_code)


@router.get("/api/logs")
async def get_logs(
    since: float = 0,
    after_seq: int = 0,
    authenticated: bool = Depends(verify_auth),
):
    """获取日志"""
    logs, next_seq, cleared = log_collector.get_recent(since=since, after_seq=after_seq)
    return {"logs": logs, "timestamp": time.time(), "next_seq": next_seq, "cleared": cleared}


@router.delete("/api/logs")
async def clear_logs(authenticated: bool = Depends(verify_auth)):
    """清除日志"""
    log_collector.clear()
    return {"status": "success"}
