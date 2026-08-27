"""一键恢复 NapCat 的 songbot 事件上报配置（httpClients -> 127.0.0.1:8090/event）。

背景（S7 运维项）：NapCat Desktop 重启后可能把 ``bot.json`` 的 ``connect.httpClients``
覆盖回空（Desktop 自管该文件，S6 工作日志已记载），导致 songbot 收不到 ``@bot`` 事件。
本脚本经 NapCat WebUI API（OB11Config SetConfig）把运行时配置补回 songbot 上报条目，
无需重启 NapCat、不影响 M7 新闻模块与 3000 发送通道。

用法：
    python scripts/restore_napcat_webhook.py [--token <webui token>] [--url <上报地址>]
    （缺省自动从 ``C:\\ProgramData\\NapCatQQ Desktop\\components\\NapCatQQ\\config\\webui.json`` 读 token）

依赖：httpx（vendor 回退）或标准库 urllib（自动降级）。
"""

import base64
import hashlib
import json
import os
import sys
from typing import Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "vendor"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

WEBUI_CONFIG = r"C:\ProgramData\NapCatQQ Desktop\components\NapCatQQ\config\webui.json"
DEFAULT_TARGET = "http://127.0.0.1:8090/event"
OB11_BASE = "http://127.0.0.1:6099"


def _read_webui_token() -> str:
    with open(WEBUI_CONFIG, encoding="utf-8") as fh:
        data = json.load(fh)
    return str(data.get("token") or "").strip()


def _request(method: str, url: str, *, json_body: Optional[dict] = None,
             headers: Optional[dict] = None, timeout: float = 10.0):
    """httpx 优先，缺依赖降级 urllib（只支持本脚本用到的 POST/JSON）。"""
    try:
        import httpx  # type: ignore
    except ImportError:  # pragma: no cover — 降级 stdlib
        import urllib.request  # type: ignore
        req = urllib.request.Request(url, method=method, headers=headers or {})
        if json_body is not None:
            req.data = json.dumps(json_body).encode("utf-8")
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    return getattr(httpx, method.lower())(
        url, json=json_body, headers=headers, timeout=timeout).json()


def _login(token: str) -> str:
    """WebUI 登录：hash = sha256(token + '.napcat')，返回 Bearer Credential（有效期 1h）。"""
    digest = hashlib.sha256((token + ".napcat").encode("utf-8")).hexdigest()
    resp = _request("POST", OB11_BASE + "/api/auth/login", json_body={"hash": digest})
    cred = (resp.get("data") or {}).get("Credential")
    if not cred:
        raise RuntimeError(f"WebUI 登录失败: {resp.get('message')}")
    return cred


def restore_webhook(target_url: str = DEFAULT_TARGET, token: Optional[str] = None) -> dict:
    """确保运行时 OB11 配置的 httpClients 含 songbot 上报条目；返回新配置的 network 摘要。"""
    token = token or _read_webui_token()
    if not token:
        raise RuntimeError(f"未找到 WebUI token（{WEBUI_CONFIG}）")
    auth = {"Authorization": "Bearer " + _login(token)}

    cfg = _request("POST", OB11_BASE + "/api/OB11Config/GetConfig", headers=auth)
    if cfg.get("code") != 0:
        raise RuntimeError(f"GetConfig 失败: {cfg.get('message')}")
    data = cfg["data"]
    clients = data.setdefault("network", {}).setdefault("httpClients", [])
    existed = any(c.get("name") == "songbot" for c in clients if isinstance(c, dict))
    if not existed:
        clients.append({
            "name": "songbot", "url": target_url, "enable": True,
            "messagePostFormat": "array", "reportSelfMessage": False,
            "token": "", "debug": False,
        })
    result = _request("POST", OB11_BASE + "/api/OB11Config/SetConfig",
                      headers=auth, json_body={"config": json.dumps(data, ensure_ascii=False)})
    if result.get("code") != 0:
        raise RuntimeError(f"SetConfig 失败: {result.get('message')}")
    return {
        "action": "已存在（无需改动）" if existed else "已追加",
        "target": target_url,
        "httpClients": [c.get("name") for c in clients if isinstance(c, dict)],
    }


def main() -> int:
    token = None
    url = DEFAULT_TARGET
    args = sys.argv[1:]
    while args:
        a = args.pop(0)
        if a == "--token" and args:
            token = args.pop(0)
        elif a == "--url" and args:
            url = args.pop(0)
        else:
            print(f"未知参数: {a}")
            return 2
    try:
        info = restore_webhook(url, token)
        print(f"[OK] NapCat 上报配置{info['action']}：{info['target']}")
        print(f"     当前 httpClients: {info['httpClients']}")
        return 0
    except Exception as exc:  # noqa: BLE001 — 一键脚本，打印明确错误
        print(f"[ERROR] {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
