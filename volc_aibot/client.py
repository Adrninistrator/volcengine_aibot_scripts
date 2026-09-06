# -*- coding: utf-8 -*-
"""火山引擎智能外呼控制台 API 客户端（公共层，所有脚本与 MCP 服务共用）。

抓包依据：抓包记录/ 下 10 个目录（2026-09-03），协议要点：
- 认证：登录态全在 Cookie（chrome_capture_operate 实时查询获取），
  写操作需请求头 x-csrf-token（取 Cookie 中 csrfToken 的值）；
- 剧本 ID（AgentID，llm_xxx）经 agent/list 接口解析为数字坐标：
  ProjectID / ServiceID / GroupID，后续接口 URL 使用
  /console/api/v2/projects/{p}/products/9/services/{s}/xxx?group_id={g}；
- 响应两种风格并存：{"code":0,"data":...,"msg":"ok"} 与
  {"ResponseMetadata":...,"Result":...}，unwrap() 统一处理。

注意：本文件只做接口封装，不做结果文件落盘（由脚本/MCP 层负责）。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Callable

import requests
import urllib3

from .config import BASE_URL, DEFAULT_COOKIE_API, DEFAULT_TIMEOUT, \
    PERMISSION_RESOURCE, PRODUCT_ID
from .cookie_client import query_cookies
from .global_config import get_allowed_account, get_server_port

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36")

BOT_MGMT_REFERER = f"{BASE_URL}/aibot/bot-management-llm"

# 控制台页面 referer（不同功能所在页面路径不同，按抓包原样使用）
PAGE_MULTI_AGENT = "multi-agent"      # 剧本编辑/变量/发布 页
PAGE_TEXT_TESTING = "text-testing"    # 文本测试（对话）页

# 分析Agents（CloudLadder）服务域与类型标识（2026-09-06 抓包实证：
# 剧本配置页「信息抽取/线索定级/通话总结」下拉与 Agents 管理页均走
# igh.bytedance.com 的 Sca/CloudLadder 接口，鉴权用 JWT，非 Cookie）
CLOUD_LADDER_BASE = "https://igh.bytedance.com"
LADDER_AGENT_TYPES = {
    "DSA": "外呼-通话总结",
    "BDE": "外呼-信息抽取",
    "BLG": "外呼-线索定级",
}

# 剧本类型（agent/list 的 AgentMode 数值 -> 名称；批量导出清单记录，
# 批量搜索结果展示「剧本类型」列，prompt 2026-09-06）
AGENT_MODE_NAMES = {
    1: "纯PE型 Agent",
    2: "Multi Agents",
    3: "对话流程编排 Agent",
}


def agent_mode_name(mode) -> str:
    """AgentMode 数值 -> 剧本类型名称（未知值返回原值字符串）。"""
    return AGENT_MODE_NAMES.get(mode, str(mode or ""))

# 剧本 config DialogAnalysisCfg 中分析Agent挂载键名（2026-09-06 15:45
# 抓包实证：llm_tvok_cdjci 同时挂载 3 类，值均为字符串 AgentId）
LADDER_SCRIPT_AGENT_KEYS = {
    "DataExtractAgent": "外呼-信息抽取",    # BDE...
    "LeadsGradingAgent": "外呼-线索定级",   # BLG...
    "DialogSummaryAgent": "外呼-通话总结",  # DSA...
}


def _agent_id_list(value) -> list[str]:
    """DialogAnalysisCfg 键值 -> AgentId 列表（兼容 str 与 list 形态）。

    值为字符串直接判；为列表时逐项判（防同一类型挂多个）。仅接受
    BDE/BLG/DSA 前缀的分析Agent ID，其余（空值/其他域 ID）忽略。
    """
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out = []
    for v in items:
        s = str(v or "").strip()
        if s and re.match(r"^(BDE|BLG|DSA)[0-9A-Za-z]*$", s):
            out.append(s)
    return out


def ladder_type_alias_map() -> dict[str, str]:
    """分析Agent类型别名 -> TypeIdentifier（脚本/MCP 共用的类型入参归一）。

    接受三种写法（大小写敏感）：全名「外呼-信息抽取」、短名「信息抽取」
    （去掉“外呼-”前缀）、类型标识「BDE」——与脚本/MCP 文档中的写法一致。
    """
    alias: dict[str, str] = {}
    for tid, name in LADDER_AGENT_TYPES.items():
        alias[tid] = tid
        alias[name] = tid
        short = name.removeprefix("外呼-")
        alias[short] = tid
    return alias
LADDER_REFERER_AGENTS = (f"{CLOUD_LADDER_BASE}"
                         "/ladder/portal/agents/agent?inner=true&source=aibot")


class ApiError(RuntimeError):
    """接口业务错误（code != 0 / ResponseMetadata.Error / 校验失败等）。"""


class NotConfigured(RuntimeError):
    """全局配置未设置允许账号（需引导用户打开配置页）。"""


class AccountNotAllowed(RuntimeError):
    """当前登录账号不在允许操作范围，拒绝执行请求。"""


def unwrap(body: Any, what: str = "接口") -> Any:
    """统一拆两种响应风格：优先 Result，其次 data；失败抛 ApiError。

    - {"ResponseMetadata": {..., "Error": ...}} -> 抛错
    - {"code": 非0, "msg": ...}                   -> 抛错
    - {"ResponseMetadata":..., "Result": X}       -> X
    - {"code":0, "data": X, "msg":"ok"}           -> X
    """
    if not isinstance(body, dict):
        return body
    meta = body.get("ResponseMetadata") or {}
    err = meta.get("Error")
    if err:
        raise ApiError(f"{what}失败: {json.dumps(err, ensure_ascii=False)[:300]}")
    code = body.get("code")
    if code not in (None, 0):
        raise ApiError(f"{what}失败: code={code} msg={body.get('msg') or body.get('message')}")
    if "Result" in body:
        return body.get("Result")
    if "data" in body:
        return body.get("data")
    return body


def normalize_value(value: Any) -> str:
    """变量值统一转为字符串（抓包中所有值均为字符串，如 "333"、"无"）。

    布尔转小写 true/false（Boolean 类型变量），None 转 ""。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class VolcAIBotClient:
    """火山引擎智能外呼控制台客户端。"""

    def __init__(self, cookie_api: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 product: int = PRODUCT_ID,
                 logger: logging.Logger | None = None,
                 enforce_account: bool = True):
        self.cookie_api = cookie_api or DEFAULT_COOKIE_API
        self.timeout = timeout
        self.product = product
        self.logger = logger or logging.getLogger("volc_aibot")
        self._http_lock = threading.Lock()
        self._http: requests.Session | None = None
        self._resolve_lock = threading.Lock()
        self._resolved: dict[str, dict] = {}   # script_id -> coords（缓存）
        # ---- 账号守卫（prompt：所有请求前校验登录账号；cookie 未变化时免查） ----
        self.enforce_account = enforce_account
        self._checked_cookie: str = ""         # 已通过检查的 cookie 头快照
        self._checked_account: str = ""        # 该 cookie 对应的已校验账号
        # ---- CloudLadder JWT（分析Agents 域，与 Cookie 快照绑定缓存） ----
        self._ladder_token: tuple[str, str] = ("", "")
        # ---- 当前登录用户（与 Cookie 快照绑定缓存；结果目录名含账号） ----
        self._user_cache: tuple[str, dict] = ("", {})
        # ---- 项目组名映射/项目级热词表（client 级缓存，批量场景共享） ----
        self._group_names: dict[int, str] | None = None
        self._hotwords_cache: tuple[int, list] | None = None   # (project, tables)
        # 结果目录名含账号（prompt 约定）：注册账号提供者，new_result_dir
        # 惰性查询（get_current_user 命中时也会直接注入，见下）
        from .result import set_account_provider
        set_account_provider(self.get_current_account)

    # ---------------------------------------------------------------- 会话/Cookie

    def _build_http(self) -> requests.Session:
        """取 Cookie 构建 requests 会话（不写死任何 Cookie）。"""
        cookies, cookie_header = query_cookies(BASE_URL, self.cookie_api)
        s = requests.Session()
        s.verify = False  # 兼容自签/内网证书（api.md 约定）
        # 注意：content-type 不放在会话默认头（multipart 上传时需由 requests 自动生成）
        s.headers.update({
            "accept": "application/json, text/plain, */*",
            "accept-language": "zh-CN,zh;q=0.9",
            "cookie": cookie_header,
            "origin": BASE_URL,
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": USER_AGENT,
        })
        csrf = next((c.get("value") for c in cookies
                     if c.get("name") == "csrfToken"), "")
        if csrf:
            s.headers["x-csrf-token"] = csrf
        return s

    def _get_http(self) -> requests.Session:
        with self._http_lock:
            if self._http is None:
                self._http = self._build_http()
            return self._http

    def _refresh_http(self) -> requests.Session:
        """Cookie 失效（401/403）后重取 Cookie 重建会话。"""
        with self._http_lock:
            self._http = self._build_http()
            return self._http

    # ---------------------------------------------------------------- 账号守卫

    def get_current_user(self) -> dict:
        """获取当前登录的账号（GET /console/api/v2/user，返回 id 即账号）。

        响应 data：{id: 账号(数字), username, email, type, avatar}。
        与 Cookie 快照绑定缓存（同一登录态不重复请求）；命中后把账号
        注入结果目录命名（prompt：{时间_账号_功能描述}）。
        """
        snap = self._cookie_snapshot()
        if self._user_cache[0] and self._user_cache[0] == snap:
            return self._user_cache[1]
        body = self._request_json(
            "GET", f"{BASE_URL}/console/api/v2/user",
            referer=BOT_MGMT_REFERER, skip_account_guard=True,
            what="获取当前登录账号")
        data = unwrap(body, "获取当前登录账号") or {}
        if not isinstance(data, dict) or data.get("id") is None:
            raise ApiError(f"获取当前登录账号返回结构异常: {str(data)[:200]}")
        self._user_cache = (snap, data)
        try:
            from .result import set_result_account
            set_result_account(str(data.get("id")))
        except Exception:  # noqa: BLE001 - 注入失败不影响业务返回
            pass
        return data

    def get_current_account(self) -> str:
        """当前登录账号（数字字符串；同一登录态走缓存）。"""
        return str(self.get_current_user().get("id"))

    def _cookie_snapshot(self) -> str:
        """当前会话的 cookie 头快照（判断 cookie 是否变化）。"""
        s = self._get_http()
        return s.headers.get("cookie") or ""

    _config_page_opened: bool = False   # 类级：同一进程只弹一次配置页

    def _open_config_page(self) -> None:
        """未配置允许账号时弹出网页配置页（自动打开浏览器）。

        - Web 服务在运行（端口可连通）-> webbrowser 打开配置页；
        - 服务未运行 -> 提示用户先启动服务（start.bat）再从托盘打开；
        - 同一进程只弹一次（后续仅文字提示），失败不影响异常抛出。
        """
        import webbrowser
        if VolcAIBotClient._config_page_opened:
            return
        VolcAIBotClient._config_page_opened = True
        port = get_server_port()
        url = f"http://127.0.0.1:{port}/"
        try:
            import socket as _socket
            with _socket.socket() as s:
                s.settimeout(0.5)
                service_running = s.connect_ex(("127.0.0.1", port)) == 0
        except OSError:
            service_running = False
        if service_running:
            try:
                webbrowser.open(url)
                self.logger.info("已自动打开配置页: %s", url)
            except Exception as e:  # noqa: BLE001 - 弹页失败不影响报错
                self.logger.warning("自动打开配置页失败(%s)，请手动访问 %s",
                                    e, url)
        else:
            self.logger.warning(
                "Web 服务未运行（端口 %s 无监听），无法自动弹出配置页；"
                "请先运行 start.bat 启动服务，再从系统托盘双击打开配置页",
                port)

    def ensure_account_allowed(self, mutating: bool = False) -> None:
        """账号守卫：**修改操作**执行前校验当前登录账号是否为允许的账号。

        规则（prompt 约定，2026-09-04 更新）：
        - 仅**修改类操作**（发布/变量修改/变量赋值）需要校验；查询类不拦截；
        - 校验时调 /console/api/v2/user 获取登录账号；
        - 同一 cookie 头（未变化）已通过过检查 -> 跳过重复检查；
        - 全局配置 allowed_account 为空 -> 抛 NotConfigured，并**自动弹出
          网页配置参数页面**提醒用户配置（Web 服务未运行或弹页失败时仅
          文字提示；同一进程只弹一次，避免反复打扰）；
        - 登录账号 != 允许账号 -> 抛 AccountNotAllowed，不执行请求。
        - mutating=False 时只做“预检提示”：账号已配置但不匹配时仅记日志
          警告，不拦截（查询类不受限制）。
        """
        if not self.enforce_account:
            return
        allowed = get_allowed_account()
        if not allowed:
            if mutating:
                self._open_config_page()
                raise NotConfigured(
                    "未配置修改操作允许执行的账号：请在弹出的配置页"
                    f"（或手动打开 http://127.0.0.1:{get_server_port()}/ ，"
                    "系统托盘双击可达）设置“修改操作允许执行的账号”。"
                    "建议配置测试环境的账号，以保证生产环境账号数据"
                    "不被误修改")
            return   # 查询类：未配置账号不拦截
        snap = self._cookie_snapshot()
        if snap and snap == self._checked_cookie and self._checked_account:
            if self._checked_account == allowed:
                return  # cookie 未变化且已通过检查
        try:
            account = self.get_current_account()
        except ApiError:
            if mutating:
                raise
            return   # 查询类：取账号失败不拦截（由后续业务请求自身报错）
        self._checked_cookie = snap
        self._checked_account = account
        if account != allowed:
            if not mutating:
                self.logger.warning(
                    "当前登录账号 %s 与允许账号 %s 不一致（查询不受影响，"
                    "修改类操作将被拒绝）", account, allowed)
                return
            raise AccountNotAllowed(
                f"当前登录账号 {account} 不在允许修改操作范围（允许: {allowed}），"
                f"已拒绝执行修改请求。请用允许的账号登录 Chrome，或修改全局配置"
                "（建议配置测试环境账号）")
        self.logger.info("账号守卫通过: %s", account)

    # ---------------------------------------------------------------- 底层请求

    def _request_raw(self, method: str, url: str,
                     params: dict | None = None,
                     payload: dict | None = None,
                     files: dict | None = None,
                     referer: str = "",
                     accept: str | None = None,
                     skip_account_guard: bool = False,
                     mutating: bool = False,
                     what: str = "") -> requests.Response:
        """发 HTTP 请求；遇 401/403 刷新 Cookie 后重试一次。

        skip_account_guard：仅 /user 接口自身（获取账号）与内部调用设 True。
        mutating：修改类操作（发布/变量修改/赋值），账号守卫对未配置/
        不匹配账号直接拦截；查询类只预检提示不拦截。
        what：请求用途描述（日志与进度事件用；空则用 method+path）。
        """
        if not skip_account_guard and url.startswith(BASE_URL):
            self.ensure_account_allowed(mutating=mutating)
        body: bytes | None = None
        headers: dict[str, str] = {}
        if referer:
            headers["referer"] = referer
        if files is not None:
            # multipart 上传：content-type 由 requests 自动生成（含 boundary）
            headers["accept"] = accept or "application/json"
        elif payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["content-type"] = "application/json; charset=UTF-8"
            headers["accept"] = accept or "application/json"
        elif accept:
            headers["accept"] = accept

        # 请求级日志与进度事件（prompt：长耗时功能记录当前请求与执行耗时）
        # 日志约定：发起记一条（方法+URL）；返回/超时/异常再记一条
        # （方法+URL+HTTP码/超时标记+耗时）
        from . import progress
        path = urllib.parse.urlsplit(url).path or url
        label = what or f"{method} {path}"
        self.logger.info("[请求] 发起 %s %s%s", method, url,
                         f"（{what}）" if what else "")
        progress.report_request_start(label, method, path)
        start = time.perf_counter()
        resp = None
        try:
            for attempt in range(2):
                s = self._get_http() if attempt == 0 else self._refresh_http()
                resp = s.request(method, url, params=params, data=body,
                                 files=files, headers=headers,
                                 timeout=self.timeout)
                if resp.status_code in (401, 403) and attempt == 0:
                    self.logger.info("HTTP %s，刷新 Cookie 重试: %s",
                                     resp.status_code, url)
                    continue
                break
            assert resp is not None
            if resp.status_code != 200:
                raise ApiError(f"{method} {url} HTTP {resp.status_code}: "
                               f"{resp.text[:300]}")
            return resp
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            exc = sys.exc_info()[1]
            if resp is not None:
                code = str(resp.status_code)
                size = len(resp.content or b"")
            elif isinstance(exc, requests.Timeout):
                code, size = "超时", -1
            elif exc is not None:
                code, size = "异常", -1
            else:
                code, size = "无响应", -1
            size_text = f"{size} B" if size >= 0 else "-"
            self.logger.info(
                "[请求] 返回 %s %s HTTP %s 耗时 %.0f ms 大小 %s%s",
                method, url, code, elapsed_ms, size_text,
                f"（{what}）" if what else "")
            progress.report_request_done(label, method, path, elapsed_ms,
                                         status=code, bytes=size)

    def _request_json(self, method: str, url: str,
                      params: dict | None = None,
                      payload: dict | None = None,
                      files: dict | None = None,
                      referer: str = "",
                      accept: str | None = None,
                      skip_account_guard: bool = False,
                      mutating: bool = False,
                      what: str = "") -> dict:
        resp = self._request_raw(method, url, params=params, payload=payload,
                                 files=files, referer=referer, accept=accept,
                                 skip_account_guard=skip_account_guard,
                                 mutating=mutating, what=what)
        try:
            return resp.json()
        except ValueError:
            raise ApiError(f"{url} 返回非 JSON: {resp.text[:300]}")

    # ---------------------------------------------------------------- URL 构造

    def _service_url(self, coords: dict, suffix: str) -> str:
        return (f"{BASE_URL}/console/api/v2/projects/{coords['project']}"
                f"/products/{self.product}/services/{coords['service']}"
                f"/{suffix}")

    def _page_referer(self, coords: dict, page: str) -> str:
        return (f"{BASE_URL}/aibot/{page}/projects/{coords['project']}"
                f"/groups/{coords['group']}/products/{self.product}"
                f"/services/{coords['service']}")

    def _service_json(self, method: str, coords: dict, suffix: str,
                      payload: dict | None = None,
                      page: str = PAGE_MULTI_AGENT,
                      with_group: bool = True,
                      params_extra: dict | None = None,
                      what: str = "接口",
                      mutating: bool = False) -> Any:
        url = self._service_url(coords, suffix)
        params: dict[str, Any] | None = None
        if with_group or params_extra:
            params = {"group_id": coords["group"]} if with_group else {}
            if params_extra:
                params.update(params_extra)
        body = self._request_json(method, url, params=params, payload=payload,
                                  referer=self._page_referer(coords, page),
                                  mutating=mutating, what=what)
        return unwrap(body, what)

    # ---------------------------------------------------------------- 项目组

    def query_project_groups(self) -> list[dict]:
        """查询项目组列表（casbin 权限分组，含组名与组ID）。

        返回 [{"id","group_name","parent_group_id","level",...}, ...]。
        注意：树形结构用 parent_group_id 组装（根为 0），level 字段语义
        反直觉（根=1、子=0），勿用 level 判断层级。
        """
        body = self._request_json(
            "GET", f"{BASE_URL}/console/api/v2/casbin/permission/groups",
            params={"resource": PERMISSION_RESOURCE},
            referer=BOT_MGMT_REFERER, what="查询项目组")
        data = unwrap(body, "查询项目组")
        if not isinstance(data, list):
            raise ApiError(f"查询项目组返回结构异常: {str(data)[:200]}")
        return data

    def find_group(self, group_name: str) -> dict:
        """按项目组名称精确匹配返回组信息；不存在时抛错并列出可用组。

        注意必须精确等值匹配：如“电销项目组_测试”(3149) 与
        “电销项目组_测试_火山调优”(3235) 并存，子串匹配会误命中。
        """
        groups = self.query_project_groups()
        for g in groups:
            if g.get("group_name") == group_name:
                return g
        listed = "; ".join(f"{g.get('group_name')}({g.get('id')})"
                           for g in groups)
        raise ApiError(f"项目组不存在: {group_name}。可用项目组: {listed}")

    # ---------------------------------------------------------------- 剧本查询/搜索

    def list_agents(self, name: str | None = None,
                    group_id: int | None = None,
                    service_id: int | None = None,
                    page: int = 1, page_size: int = 12) -> tuple[list[dict], int]:
        """GET /llm/agent/list：按名称/ID模糊搜索，返回 (本页Agents, Total)。"""
        params: dict[str, Any] = {"PageIndex": page, "PageSize": page_size}
        if name is not None:
            params["Name"] = name
        if group_id is not None:
            params["GroupID"] = group_id
        if service_id is not None:
            params["ServiceID"] = service_id
        body = self._request_json(
            "GET", f"{BASE_URL}/console/api/v2/llm/agent/list",
            params=params, referer=BOT_MGMT_REFERER, what="剧本列表查询")
        result = unwrap(body, "剧本列表查询") or {}
        agents = result.get("Agents") or []
        return agents, result.get("Total") or len(agents)

    def search_scripts(self, keyword: str,
                       max_pages: int = 20) -> list[dict]:
        """按关键字搜索剧本（Name 同时匹配剧本名称与 AgentID，模糊）。

        自动翻页收集全部命中（Total 可能大于单页 12 条）。
        """
        collected: list[dict] = []
        total: int | None = None
        for page in range(1, max_pages + 1):
            agents, total = self.list_agents(name=keyword, page=page)
            if not agents:
                break
            collected.extend(agents)
            if len(collected) >= (total or 0):
                break
        return collected

    def list_group_all_scripts(self, group_id: int,
                               page_size: int = 100) -> list[dict]:
        """按项目组ID收集其下全部剧本（agent/list 翻页，含未发布剧本）。"""
        collected: list[dict] = []
        page = 1
        while True:
            agents, total = self.list_agents(group_id=group_id, page=page,
                                             page_size=page_size)
            if not agents:
                break
            collected.extend(agents)
            if len(collected) >= total:
                break
            page += 1
        return collected

    def resolve_script(self, script_id: str, refresh: bool = False) -> dict:
        """剧本ID（AgentID，llm_xxx）-> 数字坐标（带缓存）。

        返回 {"project","service","group","agent_id","agent_name",
              "preview_version","online_version","preview_status"}。
        对返回 Agents 做 AgentID 精确匹配，防模糊误命中。
        """
        if not refresh:
            with self._resolve_lock:
                cached = self._resolved.get(script_id)
            if cached:
                return cached
        agents, _ = self.list_agents(name=script_id)
        exact = [a for a in agents if a.get("AgentID") == script_id]
        if not exact:
            if agents:
                listed = "; ".join(
                    f"{a.get('AgentID')}({a.get('AgentName', '')})"
                    for a in agents[:12])
                raise ApiError(f"剧本ID {script_id} 无精确匹配，相近候选: {listed}")
            raise ApiError(f"未搜索到剧本: {script_id}（确认剧本ID拼写/账号权限）")
        a = exact[0]
        coords = {
            "project": a["ProjectID"], "service": a["ServiceID"],
            "group": a["GroupID"], "agent_id": a.get("AgentID", script_id),
            "agent_name": a.get("AgentName", ""),
            "preview_version": a.get("PreviewVersion"),
            "online_version": a.get("OnlineVersion"),
            "preview_status": a.get("PreviewStatus"),
        }
        with self._resolve_lock:
            self._resolved[script_id] = coords
        return coords

    def query_script(self, script_id: str) -> dict:
        """根据剧本ID查询剧本详情（返回 agent/list 的完整原始字段）。"""
        agents, _ = self.list_agents(name=script_id)
        exact = [a for a in agents if a.get("AgentID") == script_id]
        if not exact:
            raise ApiError(f"未搜索到剧本: {script_id}")
        return exact[0]

    # ---------------------------------------------------------------- 导出/导入

    def export_script(self, script_id: str) -> tuple[str, bytes]:
        """导出剧本：返回 (文件名, 文件内容JSON字节)。

        响应为 application/octet-stream 文件下载流（attachment），
        文件名从 content-disposition 解析（RFC5987 filename*），兜底用剧本名。
        """
        coords = self.resolve_script(script_id)
        resp = self._request_raw(
            "GET", self._service_url(coords, "llm/dialogue_flow_script/export"),
            params={"group_id": coords["group"]},
            referer=self._page_referer(coords, PAGE_MULTI_AGENT))
        filename = ""
        cd = resp.headers.get("content-disposition") or ""
        m = re.search(r"filename\*=utf-8''([^;]+)", cd)
        if m:
            filename = urllib.parse.unquote(m.group(1).strip())
        if not filename:
            m = re.search(r'filename="?([^";]+)"?', cd)
            if m:
                filename = m.group(1).strip()
        if not filename:
            filename = f"{coords.get('agent_name') or script_id}.json"
        return filename, resp.content

    def import_script(self, file: str | Path,
                      group_name: str | None = None,
                      group_id: int | None = None) -> dict:
        """导入剧本：multipart 上传导出 JSON 文件到指定项目组。

        - file: 导出的剧本 JSON 文件路径；
        - group_name / group_id 二选一（group_name 经项目组接口精确解析）；
        返回 {group_id, group_name, source_file, ServiceID, ServiceName,
               new_agent_id(剧本ID), ...}。
        注意：导入响应不含 AgentID，需再查 agent/list（按 ServiceID 精确）获取。
        """
        path = Path(file)
        if not path.is_file():
            raise ApiError(f"导入文件不存在: {path}")
        if group_name:
            group = self.find_group(group_name)
            group_id = group["id"]
            group_name = group.get("group_name")
        if group_id is None:
            raise ApiError("必须指定目标项目组（group_name 或 group_id）")

        content = path.read_bytes()
        files = {"File": (path.name, content, "application/json")}
        body = self._request_json(
            "POST", f"{BASE_URL}/console/api/v2/llm/agent/import",
            params={"GroupID": group_id}, files=files,
            referer=BOT_MGMT_REFERER, accept="application/json",
            what="导入剧本")
        result = unwrap(body, "导入剧本") or {}
        service_id = result.get("ServiceID")
        service_name = result.get("ServiceName") or path.stem

        # 新剧本的 AgentID：优先按 ServiceID 精确查询
        new_agent_id = ""
        new_agent: dict = {}
        if service_id is not None:
            agents, _ = self.list_agents(service_id=service_id, page_size=1)
            if agents:
                new_agent = agents[0]
                new_agent_id = new_agent.get("AgentID") or ""
        if not new_agent_id:
            # 兜底：按新名称搜索并精确匹配（名称含服务端时间戳后缀，应唯一）
            agents, _ = self.list_agents(name=service_name)
            exact = [a for a in agents if a.get("AgentName") == service_name]
            if exact:
                new_agent = exact[0]
                new_agent_id = new_agent.get("AgentID") or ""

        return {
            "group_id": group_id,
            "group_name": group_name,
            "source_file": str(path),
            "source_file_name": path.name,
            "new_service_id": service_id,
            "new_script_name": service_name,
            "new_agent_id": new_agent_id,
            "new_agent": new_agent,
        }

    # ---------------------------------------------------------------- 发布

    def get_release_launch(self, script_id: str | None = None,
                           coords: dict | None = None) -> dict:
        """GET release-launch：发布状态（train_info=测试版本，online_info=线上版本）。"""
        coords = coords or self.resolve_script(script_id or "")
        data = self._service_json("GET", coords, "release-launch",
                                  page=PAGE_MULTI_AGENT, what="查询发布状态")
        return data or {}

    def publish_preview(self, script_id: str, description: str,
                        timeout: float = 600.0, interval: float = 3.0,
                        on_poll: Callable[[dict], None] | None = None) -> dict:
        """发布剧本测试版本，并轮询至完成。

        流程（抓包验证）：
        1. 记录提交前 release-launch 的 train_info.version（当前版本）；
        2. POST /training {"description": ...}（响应 data 为空，版本号服务端自动+1）；
        3. 轮询 release-launch，当 train_info.version == 当前+1 且
           status == "FINISHED" 判定发布成功（prompt 约定判据）。

        若并发发布导致 version 跳变超过 +1 且 FINISHED，也视为成功（附提示）。
        """
        coords = self.resolve_script(script_id)
        launch = self.get_release_launch(coords=coords)
        before = launch.get("train_info") or {}
        current_version = before.get("version") or 0
        if before and before.get("status") not in ("FINISHED", ""):
            self.logger.warning("当前测试版本状态为 %s（非 FINISHED），仍尝试提交发布",
                                before.get("status"))

        body = self._request_json(
            "POST", self._service_url(coords, "training"),
            payload={"description": description},
            referer=self._page_referer(coords, PAGE_MULTI_AGENT),
            mutating=True, what="发布测试版本")
        unwrap(body, "发布测试版本")
        self.logger.info("已提交发布（剧本 %s 当前版本 V%s，描述: %s），开始轮询...",
                         script_id, current_version, description)

        target = current_version + 1
        deadline = time.time() + timeout
        last: dict = {}
        while True:
            if time.time() >= deadline:
                raise ApiError(
                    f"发布超时（>{timeout:.0f}s）：目标 version={target}，"
                    f"最后状态={last}；请到控制台确认或调大 --timeout")
            time.sleep(interval)
            launch = self.get_release_launch(coords=coords)
            ti = launch.get("train_info") or {}
            last = {"version": ti.get("version"), "status": ti.get("status"),
                    "message": ti.get("message")}
            if on_poll:
                on_poll(last)
            version = ti.get("version")
            status = str(ti.get("status") or "")
            if version is not None and version == target and status == "FINISHED":
                return {
                    "script_id": script_id,
                    "agent_name": coords.get("agent_name"),
                    "before_version": current_version,
                    "new_version": version,
                    "status": "FINISHED",
                    "description": description,
                    "update_time": ti.get("update_time"),
                    "message": ti.get("message"),
                    "note": "",
                }
            if version is not None and version > target and status == "FINISHED":
                # 并发发布导致版本跳变超过 +1（少见），视为成功并提示
                return {
                    "script_id": script_id,
                    "agent_name": coords.get("agent_name"),
                    "before_version": current_version,
                    "new_version": version,
                    "status": "FINISHED",
                    "description": description,
                    "update_time": ti.get("update_time"),
                    "message": ti.get("message"),
                    "note": f"版本从 V{current_version} 跳到 V{version}"
                            f"（超过 +1，可能存在并发发布）",
                }
            if version is not None and version > current_version \
                    and status.upper() in ("FAILED", "ERROR"):
                raise ApiError(
                    f"发布失败: version={version} status={status} "
                    f"message={ti.get('message')}")

    # ---------------------------------------------------------------- 剧本变量（定义 CRUD）

    def query_variables(self, script_id: str) -> list[dict]:
        """查询剧本变量定义列表（global_variables，全量无分页）。

        返回 [{"id","name","key","IsRequired","VariableType"}, ...]。
        VariableType: 1=String 2=Integer 3=Float 4=Boolean。
        注意：抓包中无“变量描述”字段（控制台该弹窗不支持描述）。
        """
        coords = self.resolve_script(script_id)
        result = self._service_json("GET", coords, "llm/global_variables",
                                    what="查询剧本变量")
        return (result or {}).get("Variables") or []

    def modify_variables(self, script_id: str,
                         add: list[dict] | None = None,
                         update: list[dict] | None = None,
                         delete_ids: list[int] | None = None) -> dict:
        """剧本变量新增/修改/删除（同一个 POST 的 diff 三段结构）。

        - add:    [{"name","key","IsRequired","VariableType"}]（不传 id）
        - update: [{"id","name","key","IsRequired","VariableType"}]（必须带 id）
        - delete: [变量数字ID, ...]（不是调用名称）
        实现按抓包原样：update 数组全量回传所有未删除的存量变量。

        返回 {"before","after","payload"}：POST 响应恒为空 Result，
        成功判据以回查 GET 为准（调用方负责比对/落盘）。
        """
        add = [dict(x) for x in (add or [])]
        update = [dict(x) for x in (update or [])]
        delete_ids = list(delete_ids or [])

        coords = self.resolve_script(script_id)
        before = self.query_variables(script_id)
        current_ids = {v.get("id") for v in before}
        for u in update:
            if u.get("id") not in current_ids:
                raise ApiError(f"修改变量失败：变量ID {u.get('id')} 不存在")
        for d in delete_ids:
            if d not in current_ids:
                raise ApiError(f"删除变量失败：变量ID {d} 不存在")

        update_payload: list[dict] = []
        for v in before:
            if v.get("id") in set(delete_ids):
                continue  # 被删除的变量不再出现在 update 数组
            item = {"id": v.get("id"), "name": v.get("name"),
                    "key": v.get("key"), "IsRequired": v.get("IsRequired"),
                    "VariableType": v.get("VariableType")}
            for u in update:
                if u.get("id") == v.get("id"):
                    for field in ("name", "key", "IsRequired", "VariableType"):
                        if field in u:
                            item[field] = u[field]
            update_payload.append(item)

        payload = {"Variables": {"delete": delete_ids,
                                 "add": add,
                                 "update": update_payload}}
        self._service_json("POST", coords, "llm/global_variables",
                           payload=payload, what="剧本变量修改",
                           mutating=True)
        after = self.query_variables(script_id)
        return {"before": before, "after": after, "payload": payload}

    # ---------------------------------------------------------------- 测试版本全局变量（赋值）

    def get_script_variables(self, script_id: str | None = None,
                             coords: dict | None = None) -> dict:
        """GET llm/script_variables：测试/线上两套变量及取值。

        返回 {"PreviewVariables":[{key,value,name,is_required,variable_type}],
               "OnlineVariables":[...], "PreviewCallParams":{...}, ...}
        """
        coords = coords or self.resolve_script(script_id or "")
        result = self._service_json("GET", coords, "llm/script_variables",
                                    what="查询测试版本全局变量")
        return result or {}

    def fetch_preview_variable_values(self, script_id: str | None = None,
                                      coords: dict | None = None) -> dict:
        """取测试版本变量取值 {key: value}（文本对话 context 的默认值来源）。"""
        result = self.get_script_variables(script_id=script_id, coords=coords)
        return {v["key"]: v.get("value", "")
                for v in result.get("PreviewVariables") or []}

    def set_preview_variables(self, script_id: str,
                              values: dict[str, Any]) -> dict:
        """测试版本全局变量赋值（全量提交 PreviewVariables，仅改目标 key）。

        values: {变量调用名称: 新值}；值会经 normalize_value 转字符串。
        **必填变量（is_required=true）的赋值不能为空**——传入空值
        （""/None/"无"）会抛 ApiError 提示（prompt 约定）。
        返回 {"before","after","values","verify_failed"}；verify_failed
        非空表示回读后仍有变量未生效（POST 响应为空，以回读为准）。
        """
        norm = {str(k): normalize_value(v) for k, v in values.items()}
        if not norm:
            raise ApiError("未提供任何要赋值的变量")

        coords = self.resolve_script(script_id)
        before_result = self.get_script_variables(coords=coords)
        before = before_result.get("PreviewVariables") or []
        before_map = {v.get("key"): v for v in before}
        missing = [k for k in norm if k not in before_map]
        if missing:
            raise ApiError(
                f"以下变量不存在于该剧本的测试版本变量中: {missing}；"
                f"可先调用查询剧本变量确认调用名称")

        # 必填变量赋值不能为空（prompt 约定；抓包实证：必填空值提交
        # 会被服务端拒绝 code=101 "变量未赋值：xxx"）
        empty_required = [k for k, v in norm.items()
                          if not v.strip()
                          and (before_map.get(k) or {}).get("is_required")]
        if empty_required:
            raise ApiError(
                f"以下变量为必填变量，赋值时值不能为空: {empty_required}；"
                f"请为它们提供非空值（查询变量时 is_required=true 的均为必填）")

        preview = []
        for v in before:
            item = dict(v)
            if item.get("key") in norm:
                item["value"] = norm[item["key"]]
            preview.append(item)

        self._service_json("POST", coords, "llm/script_variables",
                           payload={"PreviewVariables": preview},
                           what="测试版本全局变量赋值", mutating=True)
        after_result = self.get_script_variables(coords=coords)
        after = after_result.get("PreviewVariables") or []
        after_map = {v.get("key"): v.get("value") for v in after}
        verify_failed = {k: {"expect": norm[k], "actual": after_map.get(k)}
                         for k in norm if after_map.get(k) != norm[k]}
        return {"before": before, "after": after,
                "values": norm, "verify_failed": verify_failed}

    # ---------------------------------------------------------------- 文本对话测试

    def talk(self, coords: dict, context_str: str, query: str,
             session_id: str, round_index: int) -> dict:
        """POST talk：一轮对话（同步 HTTP，实测 0.9~2.7 秒/轮）。

        - 开始对话（拿开场白）：query=""，round_index=0（首轮输入由剧本配置
          LLMFirstRoundInput 提供，不要自己传"你好"）；
        - 客户发言：query=客户话术，round_index 从 1 递增；
        - session_id 客户端生成（UUIDv4）整段对话复用；uuid 每轮新生成；
        - context 为“字符串化的 JSON”（双重序列化），键按字母排序。
        返回响应中的 data（含 skill_results / rsp_ctx / debug_info）。
        """
        payload = {"data": {
            "context": context_str,
            "query": query,
            "session_id": session_id,
            "uuid": str(uuid.uuid4()),
            "round_index": round_index,
        }}
        # 瞬态错误重试：服务端 LLM 推理后端偶发流式连接中断
        # （code=111 "recv ds stream response error ... EOF peer close"，
        #   实测为火山侧豆包推理服务的间歇性故障，重试即可恢复）
        last_err = None
        for attempt in range(3):
            payload["data"]["uuid"] = str(uuid.uuid4())  # 每次重试换新 uuid
            body = self._request_json(
                "POST", self._service_url(coords, "talk"),
                params={"group_id": coords["group"]}, payload=payload,
                referer=self._page_referer(coords, PAGE_TEXT_TESTING),
                what=f"文本对话第{round_index}轮")
            code = body.get("code")
            if code == 0:
                break
            last_err = ApiError(
                f"talk 第{round_index}轮 code={code}: "
                f"{json.dumps(body, ensure_ascii=False)[:300]}")
            transient = code in (111,) or "EOF peer close" in str(
                body.get("msg") or "")
            if not transient or attempt == 2:
                raise last_err
            self.logger.warning("talk 第%d轮服务端瞬态错误(code=%s)，%.1fs 后重试(%d/2)",
                                round_index, code, 2.0, attempt + 1)
            time.sleep(2.0)
        else:
            raise last_err
        data = body.get("data") or {}
        if data.get("code") not in (None, 0):
            raise ApiError(f"talk 第{round_index}轮业务code={data.get('code')}: "
                           f"{json.dumps(data, ensure_ascii=False)[:300]}")
        return data

    @staticmethod
    def extract_reply(data: dict) -> tuple[list[str], str, bool]:
        """从 talk 响应提取 (机器人话术列表, 当前节点名, 会话是否被机器人结束)。

        - 话术：data.skill_results[*].tts_data[*].text（兜底 debug_info.raw_text）
        - 节点：skill_results[0].extra_params(JSON字符串).path_trace[-1].node_name
        - 挂机：data.rsp_ctx.session_completed == true
        """
        texts: list[str] = []
        node = ""
        for skill in data.get("skill_results") or []:
            for tts in skill.get("tts_data") or []:
                if tts.get("text"):
                    texts.append(tts["text"])
            try:
                trace = json.loads(skill.get("extra_params") or "{}") \
                    .get("path_trace") or []
                if trace:
                    node = trace[-1].get("node_name") or node
            except (ValueError, TypeError):
                pass
        if not texts:
            raw = (data.get("debug_info") or {}).get("raw_text")
            if raw:
                texts.append(str(raw))
        completed = bool((data.get("rsp_ctx") or {}).get("session_completed"))
        return texts, node, completed

    def check_preview_published(self, coords: dict) -> dict:
        """检查测试版本是否已发布完成（文本对话测试的前置条件）。"""
        launch = self.get_release_launch(coords=coords)
        ti = launch.get("train_info") or {}
        return {
            "published": str(ti.get("status") or "") == "FINISHED" and
            (ti.get("version") or 0) > 0,
            "train_info": ti,
        }

    def dialog_analysis(self, coords: dict,
                        dialog_items: list[dict]) -> dict:
        """POST dialog_analysis：结束测试分析（意向评级 + 对话摘要）。

        dialog_items: [{"Speaker":1|2, "Content":...}]，1=机器人 2=客户，
        按对话时间顺序；机器人挂机那轮的结束语不计入（与页面行为一致）。
        返回 Result：可能为 {}（2026-09-03 抓包疑点：分析结果为空），
        也可能含 LeadsGrading / DialogSummary（2026-08-02 抓包行为），
        调用方需兼容两种情况。
        """
        payload = {
            "Dialogs": {"DialogItems": [
                {"Speaker": it["Speaker"], "Content": it["Content"]}
                for it in dialog_items]},
            "NeedSlotExtract": True,
            "NeedLeadsGrading": True,
            "NeedDialogSummary": True,
        }
        return self._service_json(
            "POST", coords, "llm/dialog/analysis/dialog_analysis",
            payload=payload, page=PAGE_TEXT_TESTING, what="对话分析") or {}

    # ------------------------------------------------- 剧本基本信息（2026-09-06 抓包）

    def get_script_config(self, script_id: str | None = None,
                          coords: dict | None = None,
                          sub_agent_id: str | None = None) -> dict:
        """GET services/{s}/config[?SubAgentID=]：剧本基础配置。

        返回 Result：DialogControlCfg（MaxDialogueRounds 最大对话轮次 /
        MaxModelErrorCount 最大模型出错次数 / HangupTextList 挂机关键词）、
        AsrHotwordID（引用热词表ID，名称需查 list_hotword_tables）、
        AsrContextCfg.Enabled（ASR 上传上下文开关）、
        DialogAnalysisCfg（挂载的分析Agent ID）等。
        带 SubAgentID 时返回该 Sub Agent 的独立配置（Multi Agents 剧本）。
        """
        coords = coords or self.resolve_script(script_id or "")
        params: dict[str, Any] = {}
        if sub_agent_id:
            params["SubAgentID"] = sub_agent_id
        return self._service_json(
            "GET", coords, "config", payload=None,
            with_group=False, params_extra=params,
            what="查询剧本配置") or {}

    def get_prompt_config(self, script_id: str | None = None,
                          coords: dict | None = None,
                          sub_agent_id: str | None = None) -> dict:
        """GET /console/api/v2/llm/prompt_config?ServiceID={s}[&SubAgentID={id}]。

        注意：console 根路径接口（非 services 路径）、无 group_id 参数。
        返回 Result：AgentMode（1=纯PE型 2=Multi Agents 3=对话流程编排，
        抓包实证）、ModelType（LLM 模型名）及对应模式的提示词配置
        （PromptConfig / MultiPromptConfig / PromptOnlyConfig）。
        带 SubAgentID 返回该 Sub Agent 的 ModelType 与提示词（约 60KB）。
        """
        coords = coords or self.resolve_script(script_id or "")
        params: dict[str, Any] = {"ServiceID": coords["service"]}
        if sub_agent_id:
            params["SubAgentID"] = sub_agent_id
        body = self._request_json(
            "GET", f"{BASE_URL}/console/api/v2/llm/prompt_config",
            params=params, referer=self._page_referer(coords, PAGE_MULTI_AGENT),
            what="查询提示词配置")
        return unwrap(body, "查询提示词配置") or {}

    def get_sub_agents(self, script_id: str | None = None,
                       coords: dict | None = None) -> list[dict]:
        """GET /console/api/v2/llm/multi_agent/sub_agents?ServiceID={s}：
        Multi Agents 剧本的 Sub Agent 清单（SubAgentID/SubAgentName/AgentMode，
        返回顺序即页面上从上到下的顺序）。
        非 Multi Agents 剧本该接口返回空列表或报错，调用方容错处理。
        """
        coords = coords or self.resolve_script(script_id or "")
        body = self._request_json(
            "GET", f"{BASE_URL}/console/api/v2/llm/multi_agent/sub_agents",
            params={"ServiceID": coords["service"]},
            referer=self._page_referer(coords, PAGE_MULTI_AGENT),
            what="查询Sub Agent列表")
        result = unwrap(body, "查询Sub Agent列表") or {}
        return result.get("SubAgents") or []

    def list_hotword_tables(self, script_id: str | None = None,
                            coords: dict | None = None,
                            refresh: bool = False) -> list[dict]:
        """GET /console/api/v2/projects/{p}/bigasr/hotword_tables：
        项目级热词表清单（id/name，用于 AsrHotwordID -> 名称映射）。

        项目级数据批量查询期间不变，client 级缓存避免逐剧本重复请求。
        """
        coords = coords or self.resolve_script(script_id or "")
        project = coords["project"]
        with self._resolve_lock:
            if (self._hotwords_cache is not None
                    and self._hotwords_cache[0] == project and not refresh):
                return self._hotwords_cache[1]
        body = self._request_json(
            "GET", f"{BASE_URL}/console/api/v2/projects/{project}"
                   f"/bigasr/hotword_tables",
            referer=self._page_referer(coords, PAGE_MULTI_AGENT),
            what="查询热词表列表")
        result = unwrap(body, "查询热词表列表") or {}
        tables = result.get("data") or []
        with self._resolve_lock:
            self._hotwords_cache = (project, tables)
        return tables

    def group_name_map(self, refresh: bool = False) -> dict[int, str]:
        """项目组ID -> 名称映射（casbin 分组接口反查；client 级缓存）。

        批量查询几十个剧本时映射不变，缓存避免每剧本重复请求。
        """
        with self._resolve_lock:
            if self._group_names is not None and not refresh:
                return self._group_names
        names = {g.get("id"): g.get("group_name")
                 for g in self.query_project_groups()}
        with self._resolve_lock:
            self._group_names = names
        return names

    # ------------------------------------------------- 分析Agents（CloudLadder）

    def get_cloud_ladder_token(self, refresh: bool = False) -> str:
        """GET /console/api/v2/cloud_ladder/token（Cookie 鉴权）-> JWTToken。

        CloudLadder（igh.bytedance.com）接口鉴权头 x-jwt-token 使用。
        token 与 Cookie 快照绑定缓存：同一登录态不重复获取。
        """
        with self._resolve_lock:
            cached = self._ladder_token
            snap = self._cookie_snapshot()
        if cached and cached[0] == snap and not refresh:
            return cached[1]
        body = self._request_json(
            "GET", f"{BASE_URL}/console/api/v2/cloud_ladder/token",
            referer=f"{BASE_URL}/aibot/agents",
            what="获取CloudLadder token")
        token = (unwrap(body, "获取CloudLadder token") or {}).get("JWTToken") or ""
        if not token:
            raise ApiError("获取 CloudLadder token 失败：响应无 JWTToken")
        with self._resolve_lock:
            self._ladder_token = (snap, token)
        return token

    def _ladder_get(self, path: str, params: dict | None = None,
                    referer: str = LADDER_REFERER_AGENTS,
                    what: str = "CloudLadder接口") -> Any:
        """CloudLadder GET（x-jwt-token 头鉴权；401 时刷新 token 重试一次）。"""
        from . import progress
        url = f"{CLOUD_LADDER_BASE}{path}"
        self.logger.info("[请求] 发起 GET %s（%s）", url, what)
        progress.report_request_start(what, "GET", path)
        start = time.perf_counter()
        last_err = ""
        resp = None
        try:
            for attempt in range(2):
                token = self.get_cloud_ladder_token(refresh=attempt > 0)
                resp = requests.get(
                    url, params=params, timeout=self.timeout, verify=False,
                    headers={
                        "accept": "application/json, text/plain, */*",
                        "accept-language": "zh-CN,zh;q=0.9",
                        "user-agent": USER_AGENT,
                        "referer": referer,
                        "x-jwt-token": token,
                        "x-im-internal-access-token": "",
                    })
                if resp.status_code == 200:
                    try:
                        return unwrap(resp.json(), what)
                    except ValueError:
                        raise ApiError(f"{url} 返回非 JSON: {resp.text[:300]}")
                last_err = (f"GET {path} HTTP {resp.status_code}: "
                            f"{resp.text[:300]}")
                if resp.status_code not in (401, 403) or attempt:
                    raise ApiError(f"{what}失败: {last_err}")
                self.logger.info("CloudLadder token 失效(HTTP %s)，刷新后重试",
                                 resp.status_code)
            raise ApiError(f"{what}失败: {last_err}")
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            exc = sys.exc_info()[1]
            if resp is not None:
                code = str(resp.status_code)
                size = len(resp.content or b"")
            elif isinstance(exc, requests.Timeout):
                code, size = "超时", -1
            elif exc is not None:
                code, size = "异常", -1
            else:
                code, size = "无响应", -1
            size_text = f"{size} B" if size >= 0 else "-"
            self.logger.info(
                "[请求] 返回 GET %s HTTP %s 耗时 %.0f ms 大小 %s（%s）",
                url, code, elapsed_ms, size_text, what)
            progress.report_request_done(what, "GET", path, elapsed_ms,
                                         status=code, bytes=size)

    def list_cloud_ladder_agents(
            self, type_identifiers: list[str] | None = None,
            agent_ids: list[str] | None = None,
            max_pages: int = 50) -> tuple[list[dict], int]:
        """GET /Sca/CloudLadder/Agent/List：分析Agent列表（自动翻页收集全部）。

        - type_identifiers：类型标识过滤（DSA=外呼-通话总结 BDE=外呼-信息抽取
          BLG=外呼-线索定级；为空查全部）；
        - agent_ids：按 AgentId 精确查询（如剧本挂载的分析Agent反查）；
        - 返回 (Agents列表, Total)。Agent 字段：AgentId/Name/Type(中文类型名)/
          Status(0=未发布 1=已发布)/UpdateTime(ms时间戳)/PublishTime/
          TypeIdentifier。
        """
        collected: list[dict] = []
        total = 0
        for page in range(1, max_pages + 1):
            params: list[tuple[str, Any]] = [
                ("OrderBy", 4), ("Page", page), ("PageSize", 100),
                # 与页面请求一致：排除工作流(WF)类，避免“查全部”混入
                # 非分析Agent（分析Agents 仅 DSA/BDE/BLG 三类）
                ("ExcludeTypeIdentifiers", "WF")]
            for t in (type_identifiers or []):
                params.append(("TypeIdentifiers", t))
            if agent_ids is not None:
                for aid in agent_ids:
                    params.append(("AgentIds", aid))
            result = self._ladder_get(
                "/Sca/CloudLadder/Agent/List", params=params,
                what="查询分析Agent列表") or {}
            agents = result.get("Agents") or []
            collected.extend(agents)
            total = result.get("Total") or 0
            if not agents or len(collected) >= total:
                break
        return collected, total

    @staticmethod
    def ladder_agent_status(agent: dict) -> str:
        """分析Agent 状态数值 -> 描述（0=未发布 1=已发布，抓包实证）。"""
        return "已发布" if agent.get("Status") == 1 else "未发布"

    def get_cloud_ladder_agent_config(self, agent_id: str) -> dict:
        """GET /Sca/CloudLadder/Agent/Config?AgentId=：分析Agent配置详情。

        返回 Result.AgentConfig：
        - GeneralAgentConfig.SummaryAgentConfig.InputTmpls：提示词模板列表，
          Role=1 系统提示词、Role=2 用户提示词（如 "{{.Input}}"）；
        - ModelParam：模型参数（ModelName/Endpoint/Temperature 等）。
        """
        result = self._ladder_get(
            "/Sca/CloudLadder/Agent/Config", params={"AgentId": agent_id},
            referer=f"{CLOUD_LADDER_BASE}/ladder/agent/{agent_id}/agent-arrange",
            what="查询分析Agent配置") or {}
        return result.get("AgentConfig") or result

    def get_script_analysis_agents(self, script_id: str | None = None,
                                   coords: dict | None = None,
                                   config: dict | None = None) -> dict:
        """取剧本挂载的分析Agent并反查名称/状态/更新时间。

        数据链路：script config -> DialogAnalysisCfg 中的分析Agent ID ->
        CloudLadder Agent/List?AgentIds= 批量反查。
        返回 {agent_id: {name,type,status,update_time,publish_time}}；
        未挂载任何分析Agent时返回 {}。

        键名（2026-09-06 15:45 抓包实证，剧本 llm_tvok_cdjci 挂载 3 类）：
        - DataExtractAgent   = 信息抽取（BDE...）
        - LeadsGradingAgent  = 线索定级（BLG...）
        - DialogSummaryAgent = 通话总结（DSA...）
        值均为字符串 AgentId。另有兜底：未识别的 *Agent 键按值形态
        （BDE/BLG/DSA 前缀，兼容 str 与 list）识别，防新增键漏报。

        config：可选传入已取的剧本 config（get_script_info 批量路径复用，
        省一次重复请求）。
        """
        coords = coords or self.resolve_script(script_id or "")
        if config is None:
            config = self.get_script_config(coords=coords)
        da = config.get("DialogAnalysisCfg") or {}
        ids: list[str] = []
        for key, value in da.items():
            if key in LADDER_SCRIPT_AGENT_KEYS:
                ids.extend(_agent_id_list(value))
            elif key.endswith("Agent"):
                # 未识别键：值形态识别兜底（BDE/BLG/DSA 前缀 AgentId）
                ids.extend(_agent_id_list(value))
        # 去重（同键重复/兜底与已知键重复）
        ids = list(dict.fromkeys(ids))
        if not ids:
            return {}
        agents, _ = self.list_cloud_ladder_agents(agent_ids=ids)
        found: dict[str, dict] = {}
        for a in agents:
            found[a.get("AgentId") or ""] = {
                "name": a.get("Name") or "",
                "type": a.get("Type") or "",
                "status": self.ladder_agent_status(a),
                "update_time": a.get("UpdateTime"),
                "publish_time": a.get("PublishTime"),
            }
        # 未反查到的挂载ID也保留（带提示），避免静默丢失
        for i in ids:
            if i not in found:
                found[i] = {"name": "", "type": "", "status": "未知",
                            "update_time": None, "publish_time": None,
                            "note": "CloudLadder未查到该Agent"}
        return found

    def get_script_info(self, script_id: str,
                        coords: dict | None = None,
                        agent: dict | None = None) -> dict:
        """汇总剧本基本信息（prompt 输出字段全量）。

        组合：agent/list（版本/状态）+ config（基础配置/ASR/分析Agent挂载）
        + prompt_config（LLM模型）+ hotword_tables（热词表名称）
        + release-launch（测试/线上版本发布详情）+ CloudLadder（分析Agent
        名称/状态/更新时间）。

        agent：可选传入 agent/list 的清单项（批量查询翻页收集时已含
        AgentName/版本等字段），可省一次 query_script 请求；coords 需
        一并提供（由清单项 ProjectID/ServiceID/GroupID 组装）。
        """
        if agent is not None and coords is not None:
            pass   # 复用调用方数据（批量路径：省 query_script）
        else:
            coords = coords or self.resolve_script(script_id)
            agent = self.query_script(script_id)
        config = self.get_script_config(coords=coords)
        prompt_cfg = self.get_prompt_config(coords=coords)
        groups = self.group_name_map()

        dc = config.get("DialogControlCfg") or {}
        hotword_id = config.get("AsrHotwordID")
        asr_ctx = (config.get("AsrContextCfg") or {}).get("Enabled")
        hotword_name = ""
        if hotword_id:
            for t in self.list_hotword_tables(coords=coords):
                if t.get("id") == hotword_id:
                    hotword_name = t.get("name") or ""
                    break
        analysis = self.get_script_analysis_agents(coords=coords,
                                                   config=config)
        launch = self.get_release_launch(coords=coords)
        ti = launch.get("train_info") or {}
        oi = launch.get("online_info") or {}

        # 分析Agent按中文类型名归类（类型名来自 CloudLadder 响应，不硬编码剧本侧键名）
        by_type: dict[str, dict] = {}
        for aid, info in analysis.items():
            t = info.get("type")
            if t:
                by_type[t] = dict(info, id=aid)
        agent_mode = prompt_cfg.get("AgentMode")
        # 非真人接听识别（prompt 2026-09-06：config 的 AnswerRecognizeCfg；
        # 抓包+在线实证三种类型均返回，全部输出——页面 UI 仅在对话流程编排
        # 提供编辑入口）
        ar = config.get("AnswerRecognizeCfg") or {}
        answer_recognize_enabled = bool(ar.get("IsEnabled"))
        answer_recognize_text = str(ar.get("HangupText") or "")

        return {
            "project_group": groups.get(coords.get("group"), ""),
            "script_id": script_id,
            "script_name": agent.get("AgentName") or coords.get("agent_name"),
            "agent_mode": agent_mode,
            "agent_mode_name": agent_mode_name(agent_mode),
            "max_dialogue_rounds": dc.get("MaxDialogueRounds"),
            "max_model_error_count": dc.get("MaxModelErrorCount"),
            "hangup_keywords": dc.get("HangupTextList") or [],
            "llm_model": prompt_cfg.get("ModelType") or "",
            "asr_hotword_table": hotword_name or
                (f"(ID:{hotword_id})" if hotword_id else ""),
            "asr_context_enabled": "开启" if asr_ctx else "未开启",
            "answer_recognize_enabled":
                "开启" if answer_recognize_enabled else "未开启",
            "answer_recognize_text": answer_recognize_text,
            "analysis_agents": {t: info for t, info in by_type.items()},
            "preview_publish": {
                "version": ti.get("version"),
                "update_time": ti.get("update_time"),
                "status": ti.get("status"),
            },
            "online_publish": {
                "version": oi.get("version"),
                "update_time": oi.get("update_time"),
                "status": oi.get("status"),
            },
        }
