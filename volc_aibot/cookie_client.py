# -*- coding: utf-8 -*-
"""chrome_capture_operate Cookie 查询客户端。

接口文档见 D:\\gitee-dir\\pri-code\\chrome_capture_operate\\api\\api.md：
    GET {cookie_api}/api/cookies/query   日常 Chrome 经插件推送的快照
                                        （profile=Chrome 实例编号）
    GET {cookie_api}/api/cookies/cdp     经 CDP 实时读抓包 Chrome（含
                                        httpOnly；port=CDP 调试端口）
两接口返回均含 {ok, cookies[], cookie_header}（cdp 另含 authorization）；
非 200 视为未获取到 Cookie。

2026-11-02 起，使用哪个来源由全局配置决定（配置参数页可增删、页面右上角
可切换，切换后下一次取 Cookie 立即生效）：
    cookie_sources        [{id, name, type: extension|cdp, profile|port,
                            enabled, fixed}]
    cookie_source_current 当前来源 id
默认来源为日常 Chrome 插件 profile=0，不可修改/删除。不在来源之间自动
回退——当前来源取不到 Cookie 时直接报错，避免误用另一个 Chrome 的登录态
（两个 Chrome 可能登录不同账号）。
"""

from __future__ import annotations

import logging

import requests

DEFAULT_COOKIE_API = "http://127.0.0.1:33445"
logger = logging.getLogger(__name__)


def _query(target_url: str, api_base: str, timeout: float, path: str,
           params: dict, source: str) -> tuple[list[dict], str]:
    """调用一个 Cookie 端点并解析（两端点返回结构兼容）。"""
    url = f"{api_base.rstrip('/')}{path}"
    # trust_env=False：绕过系统代理，避免代理劫持 127.0.0.1
    s = requests.Session()
    s.trust_env = False
    try:
        r = s.get(url, params=params, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError(
            f"Cookie 服务不可达: {e}\n"
            f"请检查 chrome_capture_operate 是否正在正常运行"
            f"（默认 {api_base}，工具地址见 "
            "https://github.com/Adrninistrator/chrome_capture_operate）。"
            "使用本工具期间需要保证它在运行。")
    if r.status_code != 200:  # 404 等按 api.md 视为“未获取到 Cookie”
        try:
            err = r.json().get("error") or r.text[:200]
        except ValueError:
            err = r.text[:200]
        raise RuntimeError(
            f"获取 Cookie 失败（{source}，HTTP {r.status_code}）: {err}")
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Cookie 接口返回非 JSON: {r.text[:200]}")
    if not data.get("ok"):
        raise RuntimeError(
            f"获取 Cookie 失败（{source}）: {data.get('error')}")
    return data.get("cookies") or [], data.get("cookie_header") or ""


def query_cookies(target_url: str, api_base: str = DEFAULT_COOKIE_API,
                  timeout: float = 10.0,
                  source: dict | None = None) -> tuple[list[dict], str]:
    """查询浏览器访问 target_url 时真实会带的全部 cookie。

    返回 (cookies, cookie_header)：
    - cookies: [{name, value, domain, path, secure, httpOnly, expires}, ...]
    - cookie_header: 已拼好的 "a=1; b=2" 字符串，可直接放进请求头

    source 为空时读取全局配置的当前来源（每次调用都重新解析，因此页面
    右上角切换后立即生效）。source 结构见模块说明；不跨来源回退。
    """
    if source is None:
        from . import global_config
        source = global_config.resolve_cookie_source()
    stype = str(source.get("type") or "extension").strip().lower()
    if stype == "cdp":
        port = source.get("port")
        params: dict = {"url": target_url}
        if port not in (None, ""):
            params["port"] = int(port)
            label = f"CDP 实时（抓包 Chrome，端口 {port}）"
        else:
            label = "CDP 实时（抓包 Chrome，默认端口）"
        return _query(target_url, api_base, timeout,
                      "/api/cookies/cdp", params, label)
    profile = int(source.get("profile") or 0)
    return _query(target_url, api_base, timeout, "/api/cookies/query",
                  {"url": target_url, "profile": profile},
                  f"日常 Chrome 插件快照（profile {profile}）")


def get_cookie_header(target_url: str, api_base: str = DEFAULT_COOKIE_API,
                      timeout: float = 10.0) -> str:
    """便捷封装：只要 cookie_header 字符串（来源取全局配置当前值）。"""
    return query_cookies(target_url, api_base, timeout)[1]
