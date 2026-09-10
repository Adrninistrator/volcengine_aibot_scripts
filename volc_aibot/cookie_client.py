# -*- coding: utf-8 -*-
"""chrome_capture_operate Cookie 查询客户端（复用自 bak_old 已验证实现）。

接口文档见 D:\\gitee-dir\\pri-code\\chrome_capture_operate\\api\\api.md：
    GET {cookie_api}/api/cookies/query?url=<目标URL>
返回 {ok, cookies[], cookie_header}；非 200 视为未获取到 Cookie。
"""

from __future__ import annotations

import requests

DEFAULT_COOKIE_API = "http://127.0.0.1:33445"


def query_cookies(target_url: str, api_base: str = DEFAULT_COOKIE_API,
                  timeout: float = 10.0) -> tuple[list[dict], str]:
    """查询浏览器访问 target_url 时真实会带的全部 cookie。

    返回 (cookies, cookie_header)：
    - cookies: [{name, value, domain, path, secure, httpOnly, expires}, ...]
    - cookie_header: 已拼好的 "a=1; b=2" 字符串，可直接放进请求头

    失败（服务不可达 / 未推送 / 无匹配）抛 RuntimeError，错误信息含原因。
    """
    url = f"{api_base.rstrip('/')}/api/cookies/query"
    # trust_env=False：绕过系统代理，避免代理劫持 127.0.0.1
    s = requests.Session()
    s.trust_env = False
    try:
        r = s.get(url, params={"url": target_url}, timeout=timeout)
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
            f"获取 Cookie 失败（HTTP {r.status_code}）: {err}\n"
            "请检查：Chrome 是否已登录火山引擎控制台（插件会把登录 "
            "Cookie 推送给 chrome_capture_operate）、插件 Cookie 推送"
            f"范围是否允许 volcengine.com、chrome_capture_operate 服务"
            f"是否正在运行（默认 {api_base}）。")
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Cookie 接口返回非 JSON: {r.text[:200]}")
    if not data.get("ok"):
        raise RuntimeError(
            f"获取 Cookie 失败: {data.get('error')}\n"
            "请检查：Chrome 是否已登录火山引擎控制台（插件会把登录 "
            "Cookie 推送给 chrome_capture_operate）、插件 Cookie 推送"
            f"范围是否允许 volcengine.com、chrome_capture_operate 服务"
            f"是否正在运行（默认 {api_base}）。")
    return data.get("cookies") or [], data.get("cookie_header") or ""


def get_cookie_header(target_url: str, api_base: str = DEFAULT_COOKIE_API,
                      timeout: float = 10.0) -> str:
    """便捷封装：只要 cookie_header 字符串。"""
    return query_cookies(target_url, api_base, timeout)[1]
