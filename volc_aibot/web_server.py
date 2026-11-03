# -*- coding: utf-8 -*-
"""Web 页面 + MCP SSE 同端口服务（prompt 要求）。

页面结构（标签页，宽度 100%）：
- MCP 接口：工具下拉框（名称+中文描述）选择后展示参数等详情；
- 配置参数：监听端口（重启提示/双服务说明/SSE URL）、是否允许执行修改
  操作开关（prompt 2026-09-07，默认关闭）、允许执行修改操作的账号；
- 使用说明 / 适用场景 / 提示词示例：读取项目 md/ 目录的 md 文件渲染，
  左侧标题目录 + 锚点跳转（布局参考 chrome_capture_operate 的 index.html）。

后端路由：
    GET  /               页面（HTML）
    GET  /api/config     读取全局配置
    POST /api/config     保存配置（端口 / 修改操作开关 / 允许账号）
    GET  /api/autostart  查询系统自启动状态（prompt 2026-09-22）
    POST /api/autostart  设置系统自启动（prompt 2026-09-22：HTTP 接口
                         供 AI 查询/设置，如 curl POST {"enabled": true}）
    GET  /api/tools      MCP 工具清单（名称/描述/参数）
    GET  /api/md?name=x  读取 md 目录下的 md 文件内容
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import global_config
from . import progress

MD_DIR = Path(__file__).resolve().parents[1] / "md"
MD_PAGES = ["使用说明", "适用场景", "提示词示例"]

# 由 mcp_server 启动时注入的 MCP 实例（避免 web_server -> mcp_server 循环依赖）
_MCP_INSTANCE = None


def set_mcp_instance(mcp) -> None:
    """mcp_server 启动时调用，注入 MCP server 实例供 /api/tools 查询。"""
    global _MCP_INSTANCE
    _MCP_INSTANCE = mcp


async def _list_mcp_tools() -> list[dict] | None:
    """列出 MCP 工具：名称、描述、参数（含类型与是否必填）。"""
    if _MCP_INSTANCE is None:
        return None
    try:
        tools = await _MCP_INSTANCE.list_tools()
    except Exception:  # noqa: BLE001
        return None
    out: list[dict] = []
    for t in tools:
        params: list[dict] = []
        # 兼容 mcp 1.x(inputSchema)/2.x(input_schema) 属性名
        schema = getattr(t, "input_schema", None) or \
            getattr(t, "inputSchema", None) or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        for name, spec in props.items():
            spec = spec if isinstance(spec, dict) else {}
            params.append({
                "name": name,
                "type": str(spec.get("type", "")),
                "required": name in required,
                "description": str(spec.get("description", "")),
            })
        out.append({
            "name": t.name,
            "description": str(getattr(t, "description", "") or "").strip(),
            "params": params,
        })
    return out


# ---------- 页面代码（独立文件，2026-09-10 拆分） ----------
# 整页 HTML（含内联 <style>/<script>）不再内嵌本文件，改放独立文件：
#     volc_aibot/web/index.html
# page() 每次请求从磁盘读取——修改页面后浏览器刷新即生效，无需重启服务。
# PAGE_HTML 保留为导入时的整页快照，供测试断言使用
# （test/test_account_guard.py、test/test_progress_ws.py）。
WEB_DIR = Path(__file__).resolve().parent / "web"


def _load_page() -> str:
    """读取页面 HTML 文件（独立文件，支持运行期修改后刷新生效）。"""
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


PAGE_HTML = _load_page()


def _ws_enqueue(queue: asyncio.Queue, data: dict) -> None:
    """事件放入连接队列（满则丢——仅状态展示，允许丢旧）。"""
    try:
        queue.put_nowait(data)
    except asyncio.QueueFull:
        pass


def build_web_routes() -> list[Route]:
    """Web 页面相关路由（挂到主应用）。"""

    # get_account 用线程池执行（避免阻塞事件循环）；与
    # build_quick_routes 一致采用函数内 import（见其内注释）
    from starlette.concurrency import run_in_threadpool

    # ---------------- WebSocket：执行状态实时推送（prompt 2026-09-06 新增） ----------------
    async def progress_ws(websocket: WebSocket) -> None:
        """推送执行状态事件（当前请求/阶段与剧本等参数）到页面。

        客户端断开后自动重连（前端 2s 退避）；服务端广播经事件总线
        progress（线程安全：线程池中的长任务 -> call_soon_threadsafe）。
        """
        await websocket.accept()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        def on_event(data: dict) -> None:
            try:
                loop.call_soon_threadsafe(_ws_enqueue, queue, data)
            except RuntimeError:
                pass   # 事件循环已关闭（服务停止）

        unsubscribe = progress.subscribe(on_event)
        try:
            while True:
                data = await queue.get()
                await websocket.send_json(data)
        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()

    async def page(request: Request) -> Response:
        return HTMLResponse(_load_page())

    async def get_cfg(request: Request) -> Response:
        cfg = global_config.load_config()
        port = global_config.get_server_port()
        from .autostart import is_autostart_enabled, other_autostart_entries
        sources = cfg.get("cookie_sources") or []
        current = cfg.get("cookie_source_current") or \
            global_config.COOKIE_SOURCE_DEFAULT_ID
        current_src = next((s for s in sources if s.get("id") == current), {})
        return JSONResponse({
            "server_port": port,
            "allow_mutation": global_config.get_allow_mutation(),
            "allowed_account": cfg.get("allowed_account", ""),
            "sse_url": f"http://127.0.0.1:{port}/sse",
            "config_path": global_config.config_path(),
            "autostart": is_autostart_enabled(),
            # 其他目录残留的自启动项（项目拷贝/迁移未跟随），页面提示迁移
            "autostart_other": other_autostart_entries(),
            # Cookie 获取来源（2026-11-02，页面右上角切换）
            "cookie_sources": sources,
            "cookie_source_current": current,
            "cookie_source_current_name": current_src.get("name", ""),
        })

    async def get_autostart(request: Request) -> Response:
        """查询系统自启动状态（prompt 2026-09-22：HTTP 接口供 AI 查询）。

        返回 {autostart, command, reg_name, other_entries}，与 GET
        /api/config 中 autostart 字段一致但更完整（含自启动命令与
        其他目录残留项）；供 AI 在设置前先查询当前状态。
        """
        from .autostart import autostart_status
        return JSONResponse(autostart_status())

    async def post_autostart(request: Request) -> Response:
        """设置系统自启动（注册表 Run 键，默认关闭）。

        prompt 2026-09-22：配置参数提供 HTTP 接口以便 AI 能够设置系统
        自启动——请求体 {"enabled": true/false}（AI 可经 curl 调用，
        端口见全局配置 server_port；配置页保存按钮走同一接口）。
        """
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        enabled = bool(data.get("enabled"))
        from .autostart import set_autostart
        r = set_autostart(enabled)
        if r.get("error"):
            return JSONResponse({"error": r["error"]}, status_code=500)
        return JSONResponse(r)

    async def post_cfg(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        cfg: dict = {}
        if "server_port" in data:
            try:
                port = int(data["server_port"])
            except (TypeError, ValueError):
                return JSONResponse({"error": "端口需为数字"}, status_code=400)
            if not (1024 <= port <= 65535):
                return JSONResponse(
                    {"error": "端口需在 1024~65535"}, status_code=400)
            cfg["server_port"] = port
        if "allowed_account" in data:
            account = str(data["allowed_account"] or "").strip()
            if account and not account.isdigit():
                return JSONResponse(
                    {"error": "账号应为数字（账号ID）"}, status_code=400)
            cfg["allowed_account"] = account
        if "allow_mutation" in data:
            # 修改操作开关（prompt 2026-09-07：默认关闭，只允许人工修改）
            cfg["allow_mutation"] = bool(data["allow_mutation"])
        if "cookie_sources" in data:
            # Cookie 来源列表（默认来源由 global_config 强制保留，不可改删）
            try:
                cfg["cookie_sources"] = global_config.normalize_cookie_sources(
                    data["cookie_sources"], strict=True)
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
        if "cookie_source_current" in data:
            cfg["cookie_source_current"] = str(
                data["cookie_source_current"] or "").strip()
        if not cfg:
            return JSONResponse({"error": "无有效配置项"}, status_code=400)
        old_port = global_config.get_server_port()
        try:
            saved = global_config.save_config(cfg)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        port_changed = (saved["server_port"] != old_port)
        return JSONResponse({
            "server_port": saved["server_port"],
            "allow_mutation": saved["allow_mutation"],
            "allowed_account": saved["allowed_account"],
            "cookie_sources": saved["cookie_sources"],
            "cookie_source_current": saved["cookie_source_current"],
            "restart_required": port_changed,
            "message": ("已保存（端口修改需重启服务生效）"
                        if port_changed else "已保存"),
        })

    async def post_cookie_source(request: Request) -> Response:
        """切换当前 Cookie 来源（页面右上角；保存后下一次取 Cookie 生效）。

        请求体 {"id": "<来源 id>"}；只允许切换到已启用的来源。
        """
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        try:
            src = global_config.set_cookie_source_current(
                str(data.get("id") or ""))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return JSONResponse({
            "ok": True,
            "cookie_source": src,
            "cookie_source_current": src["id"],
            "cookie_source_current_name": src.get("name", ""),
        })

    async def get_tools(request: Request) -> Response:
        """当前 MCP 服务的工具清单（名称/描述/参数）。"""
        tools = await _list_mcp_tools() if _MCP_INSTANCE else None
        if tools is None:
            return JSONResponse({"tools": [], "error": "MCP 实例未就绪"})
        return JSONResponse({"tools": tools, "count": len(tools)})

    async def get_md(request: Request) -> Response:
        """读取 md/ 目录下的内容页（仅白名单内文件）。"""
        name = (request.query_params.get("name") or "").strip()
        if name not in MD_PAGES:
            return JSONResponse({"error": f"未知内容页: {name}"},
                                status_code=400)
        path = MD_DIR / f"{name}.md"
        if not path.is_file():
            return JSONResponse(
                {"error": f"内容文件不存在: md/{name}.md"}, status_code=404)
        content = path.read_text(encoding="utf-8")
        # 服务端图片改写（2026-09-07）：md 内 ![alt](pics/xxx.png) /
        # ![alt](/pics/xxx.png) -> 静态路由 /pics/xxx.png 的 HTML img 标签
        # （放 md/pics/ 的图片可直接引用；相对路径统一改写为 /pics/…）
        import re as _re
        content = _re.sub(
            r"!\[([^\]]*)\]\(\s*(?:/)?(?:pics/|pics\\)([^)\s]+)\s*\)",
            r'<img src="/pics/\2" alt="\1" style="max-width:100%">',
            content)
        return JSONResponse({"name": name, "content": content})

    async def get_account(request: Request) -> Response:
        """当前登录火山账号（页面顶部展示；未登录/获取失败=未登录）。"""
        try:
            account = await run_in_threadpool(
                _get_quick_client().get_current_account)
            username = ""
            try:
                user = await run_in_threadpool(
                    _get_quick_client().get_current_user)
                username = str(user.get("username") or "")
            except Exception:  # noqa: BLE001 - 用户名取不到不影响账号展示
                pass
            return JSONResponse({"account": str(account),
                                 "username": username, "logged_in": True})
        except Exception as e:  # noqa: BLE001 - 未登录/Cookie 服务未运行
            return JSONResponse({"account": "", "username": "",
                                 "logged_in": False,
                                 "error": str(e)[:800]})

    return [
        Route("/", page),
        Route("/api/config", get_cfg, methods=["GET"]),
        Route("/api/config", post_cfg, methods=["POST"]),
        Route("/api/cookie-source", post_cookie_source, methods=["POST"]),
        Route("/api/tools", get_tools, methods=["GET"]),
        Route("/api/md", get_md, methods=["GET"]),
        Route("/api/account", get_account, methods=["POST"]),
        Route("/api/autostart", get_autostart, methods=["GET"]),
        Route("/api/autostart", post_autostart, methods=["POST"]),
        WebSocketRoute("/ws", progress_ws),
        *build_quick_routes(),
    ]


# ---------------------------------------------------------------- 快捷工具（2026-09-06 新增）

_QUICK_CLIENT = None


def _get_quick_client():
    """快捷工具后端用的客户端（懒加载；web_server 不得 import mcp_server）。"""
    global _QUICK_CLIENT
    if _QUICK_CLIENT is None:
        from .client import VolcAIBotClient
        _QUICK_CLIENT = VolcAIBotClient()
    return _QUICK_CLIENT


# ---------------------------------------------------------------- 批量下载分析Agent（prompt 2026-09-06 快捷工具）

def batch_download_agents(agent_type: str = "",
                          concurrency: int | None = None) -> dict:
    """批量获取分析Agent系统提示词，落盘 result/{时间_账号}_批量下载分析Agent/。

    范围（prompt 2026-09-06 终版：不需要指定项目组）：
    - 直接查 CloudLadder 全部分析Agent（账号级列表，prompt 输入仅类型）；
    - agent_type：类型过滤（空=全部；通话总结/信息抽取/线索定级及
      全名/DSA 等标识均可）；
    - concurrency：提示词下载的并发数（默认 5）。

    目录内：各分析Agent 系统提示词 .md 文件（文件名 名称_ID.md）+
    清单.json（agent_id/name/type/status/export_file 映射，供搜索定位）。
    """
    import time as _time
    from .client import ladder_type_alias_map
    from .concurrency import clamp_concurrency, run_parallel
    from .result import (AGENTS_DIR_SUFFIX, new_result_dir,
                         safe_filename, write_json)
    c = clamp_concurrency(concurrency)
    progress.set_source("agents")
    start = _time.perf_counter()
    manifest: list[dict] = []
    downloaded = failed = 0
    out_dir = None
    try:
        client = _get_quick_client()
        account = client.get_current_account()

        # 1. 全部分析Agent（按类型过滤；进度按分析Agent数量计）
        tid = ladder_type_alias_map().get(agent_type or "") \
            if agent_type else None
        agents, _ = client.list_cloud_ladder_agents(
            type_identifiers=[tid] if tid else None)
        if not agents:
            return {"error": f"无 {agent_type or ''} 类型的分析Agent"}

        out_dir = new_result_dir(AGENTS_DIR_SUFFIX)

        # 2. 并发下载系统提示词（进度按分析Agent数量计，prompt 约定）
        def download_one(a: dict) -> dict:
            cfg = client.get_cloud_ladder_agent_config(a["AgentId"])
            summary = ((cfg.get("GeneralAgentConfig") or {})
                       .get("SummaryAgentConfig") or {})
            sys_prompt = next((t.get("Text") for t in
                               summary.get("InputTmpls") or []
                               if t.get("Role") == 1), "")
            return {"system_prompt": sys_prompt or ""}

        def on_dl_progress(a: dict, index: int, total: int) -> None:
            progress.set_source("agents")
            progress.report_stage(
                f"下载分析Agent提示词 {a.get('AgentId') or ''}",
                script_id=a.get("AgentId") or "",
                script_name=a.get("Name") or "",
                index=index, total=total)

        results = run_parallel(agents, download_one, concurrency=c,
                               progress=on_dl_progress)

        for a, r in zip(agents, results):
            aid = a.get("AgentId") or ""
            name = a.get("Name") or ""
            row = {"agent_id": aid, "name": name,
                   "type": a.get("Type") or "",
                   "status": client.ladder_agent_status(a)
                   if a.get("Status") is not None else "未知"}
            if isinstance(r, tuple) and r and r[0] == "error":
                row["error"] = str(r[1])[:800]
                failed += 1
            else:
                fname = safe_filename(f"{name}_{aid}") + ".md"
                content = (f"# 分析Agent {name}（{aid}）系统提示词\n\n"
                           + (r.get("system_prompt") or ""))
                (out_dir / fname).write_text(content, encoding="utf-8")
                row["export_file"] = fname
                row["size_bytes"] = len(content.encode("utf-8"))
                downloaded += 1
            manifest.append(row)

        write_json(out_dir / "清单.json", {
            "account": str(account), "total": len(manifest),
            "downloaded": downloaded, "failed": failed,
            "agents": manifest})
        return {"download_dir": str(out_dir), "account": str(account),
                "total": len(manifest), "downloaded": downloaded,
                "failed": failed}
    finally:
        elapsed_s = round(_time.perf_counter() - start, 1)
        if manifest:
            summary = (f"批量下载分析Agent完成：成功 {downloaded}/"
                       f"{len(manifest)}")
            if failed:
                summary += f"，{failed} 个失败"
            progress.report_done(
                summary, elapsed_s=elapsed_s,
                total=len(manifest), downloaded=downloaded,
                download_dir=str(out_dir or ""))
        progress.set_source("")


AGENT_SEARCH_HEADERS = ["分析AgentID", "分析Agent名称", "关键字",
                        "是否出现", "出现次数"]


def search_downloaded_agents(dir_name: str, keyword: str,
                             with_count: bool) -> dict:
    """在「批量下载分析Agent」目录的提示词 md 文件中按行搜索关键字。"""
    import json as _json
    import time as _time
    from .result import AGENTS_DIR_SUFFIX, find_result_dir
    progress.set_source("agents")
    start = _time.perf_counter()
    rows_out: list = []
    hits = 0
    try:
        # 目录解析兼容新旧结构（result/{日期}/{目录名} 与 result/{目录名}，
        # 2026-09-08 修复：此前 RESULT_DIR/name 只命中旧结构）
        target = find_result_dir(dir_name)
        if not target or not target.name.endswith(AGENTS_DIR_SUFFIX):
            return {"error": "下载目录不存在（先执行第 1 步批量下载）"}
        mf = target / "清单.json"
        manifest = {}
        if mf.is_file():
            manifest = {row.get("export_file", ""): row for row in
                        _json.loads(mf.read_text(encoding="utf-8"))
                        .get("agents", [])}
        progress.report_stage(f"搜索分析Agent提示词关键字 {keyword!r}",
                              keyword=keyword, dir=dir_name)
        for p in sorted(target.iterdir()):
            if not p.is_file() or p.suffix != ".md" or p.name == "清单.md":
                continue
            meta = manifest.get(p.name) or {}
            found, count = False, 0
            try:
                with p.open("r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if keyword in line:
                            found = True
                            count += line.count(keyword)
            except OSError:
                pass
            rows_out.append([meta.get("agent_id", ""),
                             meta.get("name", ""),
                             keyword, "是" if found else "否",
                             count if with_count else ""])
        hits = sum(1 for r in rows_out if r[3] == "是")
        return {"headers": AGENT_SEARCH_HEADERS, "rows": rows_out,
                "total": len(rows_out), "hits": hits}
    finally:
        elapsed_s = round(_time.perf_counter() - start, 1)
        progress.report_done(
            f"分析Agent搜索完成：命中 {hits}/{len(rows_out)} 个",
            elapsed_s=elapsed_s, total=len(rows_out), hits=hits)
        progress.set_source("")


# ---------------------------------------------------------------- 批量删除剧本（prompt 2026-09-07 快捷工具四）

DELETE_SUFFIX = "_由AI修改"


def list_deletable_scripts(group: str = "") -> dict:
    """查询项目组下名称以 _由AI修改 结尾的剧本（供批量删除选择）。

    展示字段：剧本ID/剧本名称/剧本类型/测试版本号/更新时间；
    其余剧本（不带后缀）不展示（prompt：仅展示可删除的 AI 修改剧本）。
    """
    import time as _time
    progress.set_source("delete")
    start = _time.perf_counter()
    try:
        from .client import agent_mode_name, has_ai_modified_suffix
        client = _get_quick_client()
        groups = client.query_project_groups()
        if group:
            selected = [g for g in groups if g.get("group_name") == group]
            if not selected:
                return {"error": f"项目组不存在: {group}"}
        else:
            selected = groups
        rows: list = []
        for g in selected:
            gname = g.get("group_name") or str(g.get("id"))
            progress.report_stage(
                f"查询项目组下的剧本 {gname}", group=gname)
            for s in client.list_group_all_scripts(g["id"]):
                name = s.get("AgentName") or ""
                if not has_ai_modified_suffix(name):
                    continue
                rows.append([gname, s.get("AgentID") or "", name,
                             agent_mode_name(s.get("AgentMode")),
                             s.get("PreviewVersion") or 0,
                             _ts_text(s.get("UpdateTime"))])
        return {"headers": ["项目组", "剧本ID", "剧本名称", "剧本类型",
                            "测试版本号", "更新时间"],
                "rows": rows, "total": len(rows)}
    finally:
        progress.set_source("")


def _ts_text(ts) -> str:
    """毫秒时间戳 -> 'YYYY-MM-DD HH:MM:SS'。"""
    if not ts:
        return ""
    import datetime
    try:
        return datetime.datetime.fromtimestamp(int(ts) / 1000).strftime(
            "%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError, OverflowError):
        return str(ts)


def batch_delete_scripts(script_ids: list[str]) -> dict:
    """批量删除剧本（逐个调 client.delete_script，双重后缀校验）。"""
    import time as _time
    progress.set_source("delete")
    start = _time.perf_counter()
    results: list = []
    deleted = failed = 0
    try:
        client = _get_quick_client()
        for i, sid in enumerate(script_ids, 1):
            progress.report_stage(
                f"删除剧本 {sid}", script_id=sid, index=i,
                total=len(script_ids))
            try:
                r = client.delete_script(sid)
                ok = bool(r.get("deleted"))
                if ok:
                    deleted += 1
                results.append({"script_id": sid,
                                "script_name": r.get("script_name"),
                                "ok": ok,
                                "detail": "" if ok
                                else f"回查仍存在 remain={r.get('remain_count')}"})
            except Exception as e:  # noqa: BLE001 - 单个失败不中断
                failed += 1
                results.append({"script_id": sid, "ok": False,
                                "error": str(e)[:800]})
        return {"total": len(script_ids), "deleted": deleted,
                "failed": failed, "results": results}
    finally:
        elapsed_s = round(_time.perf_counter() - start, 1)
        summary = f"批量删除完成：成功 {deleted}/{len(script_ids)}"
        if failed:
            summary += f"，{failed} 个失败"
        progress.report_done(summary, elapsed_s=elapsed_s,
                             total=len(script_ids), deleted=deleted)
        progress.set_source("")


# 批量查询剧本变量：表头（llm/global_variables GET 全字段，
# 2026-10-10 晚：含 IsPreset（前置传入）/Description（变量描述）/
# IsMasked/MaskContent；是否必填受前置传入联动约束）
SCRIPT_VARIABLES_HEADERS = [
    "项目组", "剧本ID", "剧本名称", "剧本类型",
    "变量ID", "变量名称", "调用名称", "前置传入", "是否必填", "变量类型",
    "变量描述", "是否掩码并替换", "掩码替换文本",
]


def list_group_scripts(group: str = "") -> dict:
    """查询项目组下的剧本清单（供批量查询剧本变量页选择剧本）。

    返回 {"scripts": [{group, script_id, script_name, agent_mode,
    coords}]}；coords 为数字坐标（批量查询变量直传，省逐个解析）。
    group 为空时返回全部项目组的剧本。
    """
    from .client import agent_mode_name
    client = _get_quick_client()
    groups = client.query_project_groups()
    if group:
        selected = [g for g in groups if g.get("group_name") == group]
        if not selected:
            return {"error": f"项目组不存在: {group}"}
    else:
        selected = groups
    scripts: list[dict] = []
    for g in selected:
        gname = g.get("group_name") or str(g.get("id"))
        progress.report_stage(f"查询项目组下的剧本 {gname}", group=gname)
        for s in client.list_group_all_scripts(g["id"]):
            scripts.append({
                "group": gname,
                "script_id": s.get("AgentID") or "",
                "script_name": s.get("AgentName") or "",
                "agent_mode": agent_mode_name(s.get("AgentMode")),
                "coords": {"project": s.get("ProjectID"),
                           "service": s.get("ServiceID"),
                           "group": s.get("GroupID"),
                           "agent_id": s.get("AgentID") or "",
                           "agent_name": s.get("AgentName") or ""},
            })
    return {"scripts": scripts}


def _variable_row(v: dict) -> list:
    """GET llm/global_variables 单个变量 -> 行（列序同后 9 列表头）。"""
    from .config import VARIABLE_TYPES
    t = v.get("VariableType")
    type_text = f"{t} {VARIABLE_TYPES.get(t, '')}".strip() if t else ""
    return [
        v.get("id", ""), v.get("name", ""), v.get("key", ""),
        "是" if v.get("IsPreset") else "否",
        "是" if v.get("IsRequired") else "否",
        type_text,
        v.get("Description", ""),
        "是" if v.get("IsMasked") else "否",
        v.get("MaskContent", ""),
    ]


def _filter_scripts_by_ids(scripts: list, script_id: str) -> tuple:
    """按剧本ID过滤清单（prompt 2026-10-13：支持半角逗号分隔多个ID，
    页面新增剧本ID输入框）。返回 (过滤后清单, 错误信息或 None)。"""
    wanted = {x.strip() for x in str(script_id or "").split(",")
              if x.strip()}
    if not wanted:
        return scripts, None
    out = [s for s in scripts if s.get("script_id") in wanted]
    if not out:
        return [], (f"剧本 {script_id} 不在所选项目组范围内（多个剧本ID"
                    "以半角逗号分隔）")
    return out, None


def query_script_variables(group: str = "", script_id: str = "",
                           concurrency: int | None = None) -> dict:
    """批量查询剧本变量（llm/global_variables 全字段，含掩码字段）。

    - group：项目组名（空=全部项目组）；
    - script_id：剧本ID（空=所选范围内全部剧本）；
    - 行结构：前 4 列剧本维度（项目组/剧本ID/剧本名称/剧本类型），
      后 9 列同 SCRIPT_VARIABLES_HEADERS[4:]。
    返回 {headers, rows, total, errors}（脚本数与变量行数）。
    """
    import time as _time
    from .concurrency import clamp_concurrency, run_parallel
    c = clamp_concurrency(concurrency)
    # 本线程及并发 worker 的全部事件归属「批量查询剧本变量」面板
    progress.set_source("variables")
    start = _time.perf_counter()
    rows: list = []
    scripts: list = []
    errors = 0
    try:
        client = _get_quick_client()
        listing = list_group_scripts(group)
        if isinstance(listing, dict) and listing.get("error"):
            return listing
        scripts = listing["scripts"]
        scripts, _err = _filter_scripts_by_ids(scripts, script_id)
        if _err:
            return {"error": _err}

        def query_one(s: dict):
            variables = client.query_variables(s["script_id"],
                                               coords=s["coords"])
            return [[s["group"], s["script_id"], s["script_name"],
                     s["agent_mode"]] + _variable_row(v)
                    for v in variables]

        def on_progress(s: dict, index: int, total: int) -> None:
            # worker 线程：先标记来源，使 HTTP 请求事件同样归属
            progress.set_source("variables")
            progress.report_stage(
                f"查询剧本变量 {s.get('script_id') or ''}",
                group=s.get("group"),
                script_id=s.get("script_id") or "",
                script_name=s.get("script_name") or "",
                index=index, total=total)

        # 并发查询（与批量查询剧本信息同口径）
        results = run_parallel(scripts, query_one, concurrency=c,
                               progress=on_progress)
        for s, r in zip(scripts, results):
            if isinstance(r, tuple) and r and r[0] == "error":
                errors += 1
                rows.append([s["group"], s["script_id"],
                             s["script_name"], s["agent_mode"],
                             "（查询失败）"] + [""] * 7)
            else:
                rows.extend(r)
        return {"headers": SCRIPT_VARIABLES_HEADERS, "rows": rows,
                "total": len(scripts), "variables": len(rows),
                "errors": errors}
    finally:
        elapsed_s = round(_time.perf_counter() - start, 1)
        summary = (f"批量查询变量完成：{len(scripts)} 个剧本"
                   f" {len(rows)} 个变量")
        if errors:
            summary += f"，{errors} 个剧本失败"
        progress.report_done(summary, elapsed_s=elapsed_s,
                             total=len(scripts), variables=len(rows),
                             errors=errors)
        progress.set_source("")


# 查询未掩码/掩码不合规变量：表头（prompt 2026-10-13：未开启掩码，或
# 掩码文本不等于变量名称仅保留中文+英文+数字的结果）
UNMASKED_VAR_HEADERS = [
    "项目组", "剧本ID", "剧本名称", "剧本类型",
    "变量ID", "变量名称", "调用名称", "变量类型",
    "是否掩码并替换", "当前掩码替换文本", "问题",
]


def query_unmasked_variables(group: str = "", script_id: str = "",
                             concurrency: int | None = None) -> dict:
    """批量查询未掩码/掩码不合规的变量（查询类，与批量查询剧本变量同
    并发口径；prompt 2026-10-13）。

    **只检查前置传入=是（IsPreset=true）的变量**（prompt 2026-10-13：
    对话测试/实际外呼只需为前置传入变量指定值与掩码，非前置传入变量
    不参与）。在前置传入变量中查出满足任一条件的：
    - 未开启掩码（IsMasked=false）；
    - 掩码文本不满足要求（IsMasked=true 但 MaskContent != 变量名称
      仅保留中文/英文/数字的结果）；
    - 变量名称无法清洗出合法掩码文本（全非法字符，掩码文本不可能合规）。
    行结构：前 4 列剧本基本信息 + 变量详细信息（含问题原因与应为的
    合规掩码文本）。
    返回 {headers, rows, total(违规行数), scripts, variables, errors}
    （variables 为检查的前置传入变量数）。
    """
    import time as _time
    from .client import mask_text_from_name
    from .concurrency import clamp_concurrency, run_parallel
    c = clamp_concurrency(concurrency)
    # 归属「批量查询剧本变量」面板（同菜单按钮）
    progress.set_source("variables")
    start = _time.perf_counter()
    rows: list = []
    scripts: list = []
    errors = 0
    stats = {"variables": 0}
    try:
        client = _get_quick_client()
        listing = list_group_scripts(group)
        if isinstance(listing, dict) and listing.get("error"):
            return listing
        scripts = listing["scripts"]
        scripts, _err = _filter_scripts_by_ids(scripts, script_id)
        if _err:
            return {"error": _err}

        def check_one(s: dict):
            from .config import VARIABLE_TYPES
            variables = client.query_variables(s["script_id"],
                                               coords=s["coords"])
            out = []
            preset = [v for v in variables if v.get("IsPreset")]
            for v in preset:   # 只检查前置传入=是的变量（prompt 2026-10-13）
                masked = bool(v.get("IsMasked"))
                cur = v.get("MaskContent") or ""
                problems = []
                expected = ""
                try:
                    expected = mask_text_from_name(v.get("name"))
                except ValueError:
                    # 名称清洗后为空：掩码文本不可能合规
                    out.append([s["group"], s["script_id"],
                                s["script_name"], s["agent_mode"],
                                v.get("id", ""), v.get("name", ""),
                                v.get("key", ""),
                                f"{v.get('VariableType')} "
                                f"{VARIABLE_TYPES.get(v.get('VariableType'), '')}".strip(),
                                "是" if masked else "否", cur,
                                "变量名称仅保留中文/英文/数字后为空，"
                                "无法生成合法掩码文本"])
                    continue
                if not masked:
                    problems.append("未开启掩码")
                elif cur != expected:
                    problems.append(
                        f"掩码文本不满足要求（当前={cur!r}，"
                        f"应为变量名称清洗结果={expected!r}）")
                if not problems:
                    continue
                out.append([s["group"], s["script_id"], s["script_name"],
                            s["agent_mode"],
                            v.get("id", ""), v.get("name", ""),
                            v.get("key", ""),
                            f"{v.get('VariableType')} "
                            f"{VARIABLE_TYPES.get(v.get('VariableType'), '')}".strip(),
                            "是" if masked else "否", cur,
                            "；".join(problems)])
            # 统计随结果返回（主线程聚合，避免并发计数竞争）；
            # variables=本次检查的前置传入变量数（不含非前置传入）
            return {"rows": out, "variables": len(preset)}

        def on_progress(s: dict, index: int, total: int) -> None:
            progress.set_source("variables")
            progress.report_stage(
                f"查询未掩码变量 {s.get('script_id') or ''}",
                group=s.get("group"),
                script_id=s.get("script_id") or "",
                script_name=s.get("script_name") or "",
                index=index, total=total)

        results = run_parallel(scripts, check_one, concurrency=c,
                               progress=on_progress)
        for s, r in zip(scripts, results):
            if isinstance(r, tuple) and r and r[0] == "error":
                errors += 1
            else:
                rows.extend(r["rows"])
                stats["variables"] += r["variables"]
        rows.sort(key=lambda x: (x[0], x[1], str(x[5])))
        return {"headers": UNMASKED_VAR_HEADERS, "rows": rows,
                "total": len(rows), "scripts": len(scripts),
                "variables": stats["variables"], "errors": errors}
    finally:
        elapsed_s = round(_time.perf_counter() - start, 1)
        summary = (f"查询未掩码/掩码不合规变量完成：{len(scripts)} 个剧本 "
                   f"{stats['variables']} 个前置传入变量，"
                   f"{len(rows)} 个不合规")
        if errors:
            summary += f"，{errors} 个剧本查询失败"
        progress.report_done(summary, elapsed_s=elapsed_s,
                             total=len(scripts), violations=len(rows),
                             errors=errors)
        progress.set_source("")


# 批量开启变量掩码：结果表头（prompt 2026-10-13：全部变量开启掩码开关，
# 掩码文本=变量名称仅保留中文/英文/数字；串行执行不并行）
BATCH_MASK_HEADERS = [
    "项目组", "剧本ID", "剧本名称", "剧本类型",
    "变量总数", "本次开启", "已开启跳过", "名称无法清洗跳过", "结果",
]


# 批量修改类操作互斥锁（prompt 2026-10-13：批量掩码/批量发布后台不允许
# 同时执行——非阻塞抢占：执行中再收到请求直接拒绝）
_batch_ops_lock = threading.Lock()


def batch_mask_variables(group: str = "", script_id: str = "") -> dict:
    """批量开启剧本变量掩码（全部变量 IsMasked=true，掩码文本=变量名称
    仅保留中文/英文/数字的结果）。

    范围：group 空=全部项目组；script_id 空=范围内全部剧本。
    **串行执行（prompt 2026-10-13：不并行）**——逐剧本 查询变量→
    构造更新（仅处理需要变化的：未开启掩码或掩码文本非清洗名称）→
    client.modify_variables（全字段回传+联动修正）。
    ⚠ 互斥（prompt 2026-10-13）：与批量发布共用非阻塞锁，已有批量操作
    执行中时直接拒绝（不允许同时执行）。
    修改只保存为草稿，**不自动发布**（发布为独立操作）。
    返回 {headers, rows, total, errors, masked, skipped}。
    """
    import time as _time
    from .client import mask_text_from_name
    if not _batch_ops_lock.acquire(blocking=False):
        return {"error": "已有批量操作（批量掩码/批量发布）正在执行中，"
                         "请等待完成后再执行（后台不允许同时执行）"}
    # 本线程全部事件归属「批量修改变量掩码设置」面板
    progress.set_source("maskbatch")
    start = _time.perf_counter()
    rows: list = []
    scripts: list = []
    errors = 0
    masked = skipped_same = skipped_bad = 0
    try:
        client = _get_quick_client()
        # 修改守卫前置校验（2026-10-13 自查修复：开关未开/账号不符时
        # 直接拒绝整个批量，不开始执行——此前逐剧本失败或幂等"成功"）
        try:
            client.ensure_account_allowed(mutating=True)
        except Exception as e:  # noqa: BLE001 - 守卫拒绝转为错误返回
            return {"error": str(e)}
        listing = list_group_scripts(group)
        if isinstance(listing, dict) and listing.get("error"):
            return listing
        scripts = listing["scripts"]
        scripts, _err = _filter_scripts_by_ids(scripts, script_id)
        if _err:
            return {"error": _err}

        for i, s in enumerate(scripts):   # 串行（prompt：不并行）
            sid = s["script_id"]
            progress.report_stage(f"开启变量掩码 {sid}", group=s["group"],
                                  script_id=sid,
                                  script_name=s.get("script_name") or "",
                                  index=i + 1, total=len(scripts))
            try:
                variables = client.query_variables(sid, coords=s["coords"])
                items = []
                s_same = s_bad = 0
                for v in variables:
                    try:
                        text = mask_text_from_name(v.get("name"))
                    except ValueError:
                        s_bad += 1      # 名称清洗后为空（全非法字符）
                        continue
                    if v.get("IsMasked") and v.get("MaskContent") == text:
                        s_same += 1     # 已是目标状态，跳过（幂等）
                        continue
                    items.append({"id": v["id"], "IsMasked": True,
                                  "MaskContent": text})
                if items:
                    client.modify_variables(sid, update=items)
                masked += len(items)
                skipped_same += s_same
                skipped_bad += s_bad
                rows.append([s["group"], sid, s["script_name"],
                             s["agent_mode"], len(variables), len(items),
                             s_same, s_bad, "成功"])
            except Exception as e:  # noqa: BLE001
                errors += 1
                rows.append([s["group"], sid, s["script_name"],
                             s["agent_mode"], "", "", "", "",
                             f"失败: {str(e)[:80]}"])
        return {"headers": BATCH_MASK_HEADERS, "rows": rows,
                "total": len(scripts), "errors": errors, "masked": masked,
                "skipped_same": skipped_same, "skipped_bad": skipped_bad}
    finally:
        elapsed_s = round(_time.perf_counter() - start, 1)
        summary = (f"批量开启变量掩码完成：{len(scripts)} 个剧本，"
                   f"开启 {masked} 个变量（已开启跳过 {skipped_same}、"
                   f"名称无法清洗跳过 {skipped_bad}）")
        if errors:
            summary += f"，{errors} 个剧本失败"
        progress.report_done(summary, elapsed_s=elapsed_s,
                             total=len(scripts), masked=masked,
                             errors=errors)
        progress.set_source("")
        _batch_ops_lock.release()
# 批量发布剧本：结果表头（prompt 2026-10-13；串行执行不并行）
BATCH_PUBLISH_HEADERS = [
    "项目组", "剧本ID", "剧本名称", "剧本类型",
    "测试版本发布", "线上版本发布", "结果",
]

# 发布线上版本的防误操作确认码（内存缓存，prompt 2026-10-13：
# 用户名_随机6位数字，后台生成缓存并返回前端显示、要求用户输入确认）
_publish_confirm: dict = {"code": "", "expires": 0.0}
PUBLISH_CONFIRM_TTL = 600.0   # 确认码有效期（秒）


def _new_publish_confirm() -> dict:
    """生成发布确认码：当前登录用户名_随机6位数字（内存缓存，新码覆盖旧码）。返回 {username, code, expires_in}。"""
    import random
    import time as _time
    client = _get_quick_client()
    user = client.get_current_user()
    username = str(user.get("username") or "").strip()
    if not username:
        raise RuntimeError("无法获取当前登录用户名（未登录或 Cookie 失效），"
                           "不能生成发布确认码")
    code = f"{username}_{random.randint(0, 999999):06d}"
    _publish_confirm.update(code=code, expires=_time.time() + PUBLISH_CONFIRM_TTL)
    return {"username": username, "code": code,
            "expires_in": int(PUBLISH_CONFIRM_TTL)}


def _check_publish_confirm(code: str) -> tuple[bool, str]:
    """校验发布确认码（匹配且未过期，通过后即失效——一次性使用）。"""
    import time as _time
    code = str(code or "").strip()
    if not code:
        return False, "未输入确认码"
    if not _publish_confirm["code"]:
        return False, "确认码不存在（请先点击按钮获取）"
    if _time.time() > _publish_confirm["expires"]:
        _publish_confirm.update(code="", expires=0.0)
        return False, "确认码已失效，请重新获取"
    if code != _publish_confirm["code"]:
        return False, "确认码不匹配（请按页面显示的确认码输入）"
    _publish_confirm.update(code="", expires=0.0)   # 一次性使用
    return True, ""


def batch_publish_scripts(group: str = "", script_id: str = "",
                          mode: str = "preview",
                          confirm_code: str = "") -> dict:
    """批量发布剧本（prompt 2026-10-13；**串行执行不并行**）。

    - mode="preview"：逐剧本发布测试版本；
    - mode="preview_online"：逐剧本发布测试版本→发布上线（生产变更，
      受配置参数「是否允许执行修改操作」与「允许执行修改操作的账号」
      控制——client 修改守卫自动生效；并需通过防误操作确认码
      confirm_code：先调 publish_confirm 接口生成「用户名_随机6位数字」
      并在前端显示（可复制），用户输入一致才执行，一次性使用）。
    发布均为修改操作（走 client 修改守卫）；逐剧本串行发布并轮询至完成。
    ⚠ **发布时不判断剧本修改时间**（2026-10-13 决策）：修改时间
    （agent/list 的 UpdateTime）不随变量修改变化（仅发布提交/话术编排
    保存触碰，实测修改变量后 90 秒不变）——以修改时间判断"是否需要
    发布"会漏掉纯变量修改（含批量掩码设置）导致误跳过，故所有选中
    剧本均无条件执行发布。
    返回 {headers, rows, total, errors}。
    """
    import time as _time
    if mode not in ("preview", "preview_online"):
        return {"error": f"mode 必须是 preview 或 preview_online: {mode!r}"}
    if not _batch_ops_lock.acquire(blocking=False):
        return {"error": "已有批量操作（批量掩码/批量发布）正在执行中，"
                         "请等待完成后再执行（后台不允许同时执行）"}
    desc = "批量发布测试版本-" + _time.strftime("%Y%m%d%H%M%S")
    progress.set_source("publishbatch")
    start = _time.perf_counter()
    rows: list = []
    scripts: list = []
    errors = 0
    try:
        # 修改守卫前置校验（2026-10-13 自查修复：开关未开/账号不符时
        # 直接拒绝整个批量，不开始执行——此前逐剧本失败或幂等"成功"）
        try:
            _get_quick_client().ensure_account_allowed(mutating=True)
        except Exception as e:  # noqa: BLE001 - 守卫拒绝转为错误返回
            return {"error": str(e)}
        # 确认码校验放在互斥与守卫之后（通过即消费一次性码）：放在
        # 之前时互斥忙/守卫拒绝会让码被消费但发布未执行（自查修复；
        # 校验失败走本 return，锁由 finally 释放）
        if mode == "preview_online":
            ok, err = _check_publish_confirm(confirm_code)
            if not ok:
                return {"error": f"发布确认未通过: {err}"}
        # ⚠ 发布时不判断剧本修改时间（prompt 2026-10-13 决策）：
        # 修改时间（agent/list 的 UpdateTime）不随变量修改变化（仅发布
        # 提交/话术编排保存触碰，实测修改变量后等 90 秒不变）——若以
        # "修改时间早于发布时间则跳过"判断，纯变量修改（含批量掩码设置）
        # 会被误判"无需发布"跳过、修改无法随发布生效，故所有选中剧本
        # 均无条件执行发布。
        client = _get_quick_client()
        listing = list_group_scripts(group)
        if isinstance(listing, dict) and listing.get("error"):
            return listing
        scripts = listing["scripts"]
        scripts, _err = _filter_scripts_by_ids(scripts, script_id)
        if _err:
            return {"error": _err}

        for i, s in enumerate(scripts):   # 串行（prompt：不并行）
            sid = s["script_id"]
            progress.report_stage(f"发布剧本 {sid}", group=s["group"],
                                  script_id=sid,
                                  script_name=s.get("script_name") or "",
                                  index=i + 1, total=len(scripts))
            preview_txt = online_txt = ""
            try:
                pub = client.publish_preview(sid, desc)
                preview_txt = (f"V{pub['before_version']} -> "
                               f"V{pub['new_version']}")
                if mode == "preview_online":
                    on = client.publish_online(sid)
                    online_txt = (f"V{on['before_online_version']} -> "
                                  f"V{on['new_version']}")
                rows.append([s["group"], sid, s["script_name"],
                             s["agent_mode"], preview_txt, online_txt,
                             "成功"])
            except Exception as e:  # noqa: BLE001
                errors += 1
                rows.append([s["group"], sid, s["script_name"],
                             s["agent_mode"], preview_txt, online_txt,
                             f"失败: {str(e)[:100]}"])
        return {"headers": BATCH_PUBLISH_HEADERS, "rows": rows,
                "total": len(scripts), "errors": errors,
                "mode": mode}
    finally:
        elapsed_s = round(_time.perf_counter() - start, 1)
        label = "测试版本" if mode == "preview" else "测试+线上版本"
        summary = f"批量发布（{label}）完成：{len(scripts)} 个剧本"
        if errors:
            summary += f"，{errors} 个失败"
        progress.report_done(summary, elapsed_s=elapsed_s,
                             total=len(scripts), errors=errors)
        progress.set_source("")
        _batch_ops_lock.release()


# 修改后未发布的剧本：精简表头（prompt 2026-10-12：查询结果只需基本的
# 项目/剧本信息 + 修改/发布时间与发布判断，不含配置/分析Agents等全量列）
UNPUBLISHED_SCRIPT_HEADERS = [
    "项目组", "剧本ID", "剧本名称", "剧本类型",
    "剧本修改时间", "测试版本发布时间", "线上版本发布时间",
    "修改后是否有发布",
]

# 批量查询剧本信息：表头（与「查询剧本基本信息」输出字段一致）
SCRIPT_INFO_HEADERS = [
    "项目组", "剧本ID", "剧本名称", "剧本类型",
    "最大对话轮次", "最大模型出错次数", "Agent回复自动挂机关键词",
    "LLM模型", "语音识别（ASR）设置-引用热词表",
    "语音识别（ASR）设置-上传上下文",
    "非真人接听识别开关", "非真人接听识别后播报内容",
    "信息抽取-ID", "信息抽取-名称", "信息抽取-状态", "信息抽取-更新时间",
    "线索定级-ID", "线索定级-名称", "线索定级-状态", "线索定级-更新时间",
    "通话总结-ID", "通话总结-名称", "通话总结-状态", "通话总结-更新时间",
    "测试版本发布-版本号", "测试版本发布时间",
    "线上版本发布-版本号", "线上版本发布时间",
    # 2026-10-12 新增（prompt）：剧本修改时间与修改后是否有发布
    # （修改时间<=线上版本发布时间+60秒容差=是；口径见 client.get_script_info）
    "剧本修改时间", "修改后是否有发布",
]


def _script_info_row(info: dict) -> list:
    """get_script_info 结果 -> 一行（列序同 SCRIPT_INFO_HEADERS）。"""
    import datetime

    from .client import agent_mode_name

    def ts_text(ts):
        if not ts:
            return ""
        try:
            return datetime.datetime.fromtimestamp(int(ts) / 1000).strftime(
                "%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError, OverflowError):
            return str(ts)

    def agent_cells(type_zh):
        a = (info.get("analysis_agents") or {}).get(type_zh) or {}
        return [a.get("id", ""), a.get("name", ""), a.get("status", ""),
                ts_text(a.get("update_time"))]

    pv = info.get("preview_publish") or {}
    ov = info.get("online_publish") or {}
    from .client import fmt_launch_time
    return [
        info.get("project_group", ""), info.get("script_id", ""),
        info.get("script_name", ""), info.get("agent_mode_name", ""),
        info.get("max_dialogue_rounds"), info.get("max_model_error_count"),
        ";".join(str(k) for k in info.get("hangup_keywords") or []),
        info.get("llm_model", ""), info.get("asr_hotword_table", ""),
        info.get("asr_context_enabled", ""),
        info.get("answer_recognize_enabled", ""),
        info.get("answer_recognize_text", ""),
        *agent_cells("外呼-信息抽取"),
        *agent_cells("外呼-线索定级"),
        *agent_cells("外呼-通话总结"),
        pv.get("version"), fmt_launch_time(pv.get("update_time")),
        ov.get("version"), fmt_launch_time(ov.get("update_time")),
        info.get("modify_time", ""),
        info.get("modified_published", ""),
    ]


def make_xlsx(headers: list, rows: list, sheet_name: str = "Sheet1") -> bytes:
    """无依赖生成最小可用 .xlsx（内联字符串单元格）。

    结构：[Content_Types].xml / _rels/.rels / xl/workbook.xml /
    xl/_rels/workbook.xml.rels / xl/worksheets/sheet1.xml。
    首行冻结 + 自动筛选（prompt 2026-09-06：sheetViews/pane ySplit=1、
    autoFilter 引用表头行到最后一列；Excel/WPS 均识别）。
    """
    import io
    import zipfile
    from xml.sax.saxutils import escape

    def col_name(idx: int) -> str:
        s = ""
        while idx:
            idx, r = divmod(idx - 1, 26)
            s = chr(65 + r) + s
        return s or "A"

    cells = []
    for c, h in enumerate(headers, 1):
        cells.append(f'<c r="{col_name(c)}1" t="inlineStr"><is><t '
                     f'xml:space="preserve">{escape(str(h))}</t></is></c>')
    for r, row in enumerate(rows, 2):
        for c, v in enumerate(row, 1):
            cells.append(f'<c r="{col_name(c)}{r}" t="inlineStr"><is><t '
                         f'xml:space="preserve">{escape(str(v))}</t></is></c>')
    # cells 按行拼接：第 1 行为表头，数据行从 r=2 起
    row_parts = []
    offset = 0
    for r, count in enumerate([len(headers)] + [len(r) for r in rows], 1):
        chunk = cells[offset:offset + count]
        offset += count
        row_parts.append(f'<row r="{r}">' + "".join(chunk) + "</row>")
    # 首行冻结（ySplit=1，滚动时表头恒可见）+ 表头行自动筛选
    last_col = col_name(len(headers)) if headers else "A"
    sheet_views = ('<sheetViews><sheetView workbookViewId="0">'
                   '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft"'
                   ' state="frozen"/></sheetView></sheetViews>')
    auto_filter = (f'<autoFilter ref="A1:{last_col}1"/>' if headers else '')
    # 注意 OOXML schema 顺序：sheetViews -> sheetData -> autoFilter
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        '2006/main">'
        + sheet_views +
        '<sheetData>' + "".join(row_parts) + "</sheetData>"
        + auto_filter +
        "</worksheet>")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/'
                   '2006/content-types"><Default Extension="rels" '
                   'ContentType="application/vnd.openxmlformats-package.'
                   'relationships+xml"/><Default Extension="xml" '
                   'ContentType="application/xml"/><Override PartName="/xl/'
                   'workbook.xml" ContentType="application/vnd.'
                   'openxmlformats-officedocument.spreadsheetml.sheet.'
                   'main+xml"/><Override PartName="/xl/worksheets/sheet1.xml"'
                   ' ContentType="application/vnd.openxmlformats-officedocument'
                   '.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="rId1" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/'
                   '2006/relationships/officeDocument" '
                   'Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/'
                   'spreadsheetml/2006/main" xmlns:r="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships">'
                   f'<sheets><sheet name="{escape(sheet_name)}" sheetId="1" '
                   'r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="rId1" '
                   'Type="http://schemas.openxmlformats.org/officeDocument/'
                   '2006/relationships/worksheet" '
                   'Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


def build_quick_routes() -> list[Route]:
    """快捷工具标签页的后端路由（重活走线程池，避免阻塞事件循环）。"""
    from starlette.concurrency import run_in_threadpool

    # 闭包内多处使用（批量导出清单记录剧本类型等）；模块顶层 import 会
    # 与 client -> result 的初始化顺序耦合，函数内 import 更稳
    from .client import agent_mode_name

    def _log_quick_error(e: Exception) -> None:
        """快捷工具后端异常：完整堆栈写日志（prompt：记录详细异常堆栈）。"""
        import logging
        logging.getLogger("volc_aibot").exception(
            "快捷工具后端执行异常: %s", e)

    async def get_groups(request: Request) -> Response:
        try:
            groups = await run_in_threadpool(
                _get_quick_client().query_project_groups)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)[:800]}, status_code=500)
        return JSONResponse({"groups": [
            {"id": g.get("id"), "name": g.get("group_name")} for g in groups]})

    def _query_all(group: str, script_id: str = "",
                   concurrency: int | None = None,
                   only_unpublished: bool = False) -> dict:
        import time as _time
        from .concurrency import clamp_concurrency, run_parallel
        c = clamp_concurrency(concurrency)
        # 本线程及并发 worker 的全部事件归属「批量查询剧本信息」面板
        progress.set_source("query")
        start = _time.perf_counter()
        try:
            client = _get_quick_client()
            groups = client.query_project_groups()
            if group:
                selected = [g for g in groups
                            if g.get("group_name") == group]
                if not selected:
                    return {"error": f"项目组不存在: {group}"}
            else:
                selected = groups
            rows: list = []
            errors = 0
            # 先收集全部项目组的剧本（prompt 2026-10-13：提前统计总体数量，
            # 进度百分比按总量显示——不按组分段上报，避免百分比反复归零）
            tasks: list[tuple[str, dict]] = []
            for g in selected:
                gname = g.get("group_name") or str(g.get("id"))
                progress.report_stage("查询项目组下的剧本", group=gname)
                for s in client.list_group_all_scripts(g["id"]):
                    tasks.append((gname, s))
            # 剧本ID过滤（prompt 2026-10-13：页面剧本ID输入框，支持
            # 半角逗号分隔多个；空=范围内全部剧本）
            if script_id:
                wanted = {x.strip()
                          for x in script_id.split(",") if x.strip()}
                tasks = [t for t in tasks if t[1].get("AgentID") in wanted]
                if not tasks:
                    return {"error": f"剧本 {script_id} 不在所选项目组"
                                     "范围内（多个剧本ID以半角逗号分隔）"}

            def query_one(item: tuple):
                gname, s = item
                # 传入清单项：省 query_script（agent/list 数据已在 s 中）
                sid = s.get("AgentID") or ""
                coords = {"project": s.get("ProjectID"),
                          "service": s.get("ServiceID"),
                          "group": s.get("GroupID"),
                          "agent_id": sid,
                          "agent_name": s.get("AgentName") or ""}
                return _script_info_row(client.get_script_info(
                    sid, coords=coords, agent=s))

            def on_progress(item: tuple, index: int, total: int) -> None:
                # worker 线程：先标记来源，使 HTTP 请求事件同样归属；
                # index/total 为全部项目组剧本的总体进度（单轮递增不归零）
                gname, s = item
                progress.set_source("query")
                progress.report_stage(
                    f"查询剧本基本信息 {s.get('AgentID') or ''}",
                    group=gname,
                    script_id=s.get("AgentID") or "",
                    script_name=s.get("AgentName") or "",
                    index=index, total=total)

            # 并发查询（prompt 2026-09-06：获得剧本后并发查询剧本信息）
            results = run_parallel(tasks, query_one, concurrency=c,
                                   progress=on_progress)
            for (gname, s), r in zip(tasks, results):
                sid = s.get("AgentID") or ""
                if isinstance(r, tuple) and r and r[0] == "error":
                    errors += 1
                    if not only_unpublished:
                        rows.append([gname, sid, s.get("AgentName") or "",
                                     "（查询失败）"] + [""] * 26)
                else:
                    rows.append(r)
            if only_unpublished:
                # 仅保留修改后未发布的剧本（最后一列"修改后是否有发布"=否）
                # total_all 含查询失败的剧本（"共检查 N 个"不漏计）
                total_all = len(rows) + errors
                rows = [r for r in rows if str(r[-1]) == "否"]
                unpublished = len(rows)
                # 精简为基本项目/剧本信息 + 三个时间 + 发布判断
                # （prompt 2026-10-12：按表头名取列，避免索引漂移）
                idx = {h: i for i, h in enumerate(SCRIPT_INFO_HEADERS)}
                keep = [idx[h] for h in UNPUBLISHED_SCRIPT_HEADERS]
                rows = [[r[i] for i in keep] for r in rows]
                headers = UNPUBLISHED_SCRIPT_HEADERS
            else:
                total_all = unpublished = len(rows)
                headers = SCRIPT_INFO_HEADERS
            return {"headers": headers, "rows": rows,
                    "total": len(rows), "errors": errors,
                    "total_all": total_all, "unpublished": unpublished}
        finally:
            elapsed_s = round(_time.perf_counter() - start, 1)
            # 完成事件：清空面板计时、显示完成提示（页面未聚焦时闪烁提醒）
            if only_unpublished:
                summary = (f"查询修改后未发布剧本完成：共 {len(rows)} 个"
                           f"（检查 {total_all} 个）")
            else:
                summary = f"批量查询完成：共 {len(rows)} 个剧本"
            if errors:
                summary += f"，{errors} 个失败"
            progress.report_done(summary, elapsed_s=elapsed_s,
                                 total=total_all, unpublished=unpublished,
                                 errors=errors)
            progress.set_source("")

    async def post_query_scripts(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        group = str(data.get("group") or "").strip()
        script_id = str(data.get("script_id") or "").strip()
        try:
            concurrency = int(data.get("concurrency") or 0)
        except (TypeError, ValueError):
            concurrency = 0
        try:
            result = await run_in_threadpool(
                _query_all, group, script_id, concurrency)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def post_query_unpublished(request: Request) -> Response:
        """单独查询修改后未发布的剧本（prompt 2026-10-12：全量查询后
        过滤"修改后是否有发布"=否的行，含从未发布过的剧本；结果精简为
        基本项目/剧本信息 + 剧本修改时间/测试版本发布时间/线上版本发布
        时间/修改后是否有发布列，UNPUBLISHED_SCRIPT_HEADERS 共 8 列）。"""
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        group = str(data.get("group") or "").strip()
        script_id = str(data.get("script_id") or "").strip()
        try:
            concurrency = int(data.get("concurrency") or 0)
        except (TypeError, ValueError):
            concurrency = 0
        try:
            result = await run_in_threadpool(
                _query_all, group, script_id, concurrency, True)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def post_query_unmasked_vars(request: Request) -> Response:
        """查询未掩码/掩码不合规变量（prompt 2026-10-13：未开启掩码，或
        掩码文本不等于变量名称清洗结果；查询类并发口径）。"""
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        group = str(data.get("group") or "").strip()
        script_id = str(data.get("script_id") or "").strip()
        try:
            concurrency = int(data.get("concurrency") or 0)
        except (TypeError, ValueError):
            concurrency = 0
        try:
            result = await run_in_threadpool(
                query_unmasked_variables, group, script_id, concurrency)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def post_batch_mask(request: Request) -> Response:
        """批量开启变量掩码（prompt 2026-10-13：全部变量 IsMasked=true，
        掩码文本=变量名称清洗；串行执行）。"""
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        group = str(data.get("group") or "").strip()
        script_id = str(data.get("script_id") or "").strip()
        try:
            result = await run_in_threadpool(
                batch_mask_variables, group, script_id)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def post_publish_confirm(request: Request) -> Response:
        """生成发布线上版本的防误操作确认码（用户名_随机6位数字，
        内存缓存，返回前端显示（可复制）并要求用户输入确认）。"""
        try:
            result = await run_in_threadpool(_new_publish_confirm)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def post_batch_publish(request: Request) -> Response:
        """批量发布剧本（prompt 2026-10-13：串行；preview=仅测试版本、
        preview_online=测试+线上（修改操作受配置守卫控制，需确认码）。"""
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        group = str(data.get("group") or "").strip()
        script_id = str(data.get("script_id") or "").strip()
        mode = str(data.get("mode") or "preview").strip()
        confirm_code = str(data.get("confirm_code") or "").strip()
        try:
            result = await run_in_threadpool(
                batch_publish_scripts, group, script_id, mode, confirm_code)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    def _batch_export(group: str, concurrency: int | None = None) -> dict:
        import time as _time
        from .concurrency import clamp_concurrency, run_parallel
        from .result import EXPORT_DIR_SUFFIX, new_result_dir, \
            safe_filename, write_json
        c = clamp_concurrency(concurrency)
        # 本线程及并发 worker 的全部事件归属「批量导出」面板（在批量搜索页）
        progress.set_source("export")
        start = _time.perf_counter()
        result = None
        try:
            client = _get_quick_client()
            groups = client.query_project_groups()
            if group:
                selected = [g for g in groups
                            if g.get("group_name") == group]
                if not selected:
                    return {"error": f"项目组不存在: {group}"}
            else:
                selected = groups
            # 账号写入目录名（prompt 约定，区分环境；清单.json 中亦记录）
            account = client.get_current_account()
            out_dir = new_result_dir(EXPORT_DIR_SUFFIX)
            manifest: list[dict] = []
            exported = failed = 0
            # 先收集全部项目组的剧本（prompt 2026-10-13：提前统计总体数量，
            # 进度百分比按总量显示——不按组分段上报，避免百分比反复归零）
            tasks: list[tuple[str, dict]] = []
            for g in selected:
                gname = g.get("group_name") or str(g.get("id"))
                progress.report_stage("查询项目组下的剧本", group=gname)
                for s in client.list_group_all_scripts(g["id"]):
                    tasks.append((gname, s))

            def export_one(item: tuple):
                gname, s = item
                sid = s.get("AgentID") or ""
                filename, content = client.export_script(sid)
                path = out_dir / safe_filename(
                    f"{safe_filename(gname)}_{safe_filename(filename)}")
                path.write_bytes(content)
                return {"export_file": path.name,
                        "size_bytes": len(content)}

            def on_progress(item: tuple, index: int, total: int) -> None:
                # worker 线程：先标记来源，使 HTTP 请求事件同样归属；
                # index/total 为全部项目组剧本的总体进度（单轮递增不归零）
                gname, s = item
                progress.set_source("export")
                progress.report_stage(
                    f"导出剧本 {s.get('AgentID') or ''}", group=gname,
                    script_id=s.get("AgentID") or "",
                    script_name=s.get("AgentName") or "",
                    index=index, total=total)

            # 并发导出（prompt 2026-09-06：获得剧本后并行执行导出）
            results = run_parallel(tasks, export_one, concurrency=c,
                                   progress=on_progress)
            for (gname, s), r in zip(tasks, results):
                sid = s.get("AgentID") or ""
                row = {"project_group": gname, "script_id": sid,
                       "script_name": s.get("AgentName") or "",
                       "agent_mode_name": agent_mode_name(
                           s.get("AgentMode"))}
                if isinstance(r, tuple) and r and r[0] == "error":
                    row["error"] = str(r[1])[:800]
                    failed += 1
                else:
                    row.update(r)
                    exported += 1
                    manifest.append(row)
            write_json(out_dir / "清单.json", {
                "account": str(account), "total": len(manifest),
                "exported": exported, "failed": failed, "scripts": manifest})
            result = {"export_dir": str(out_dir), "total": len(manifest),
                      "exported": exported, "failed": failed}
            return result
        finally:
            elapsed_s = round(_time.perf_counter() - start, 1)
            summary = (f"批量导出完成：成功 {exported}/{len(manifest)}"
                       if result else "批量导出失败")
            if failed:
                summary += f"，{failed} 个失败"
            progress.report_done(
                summary, elapsed_s=elapsed_s, total=len(manifest),
                exported=exported, failed=failed,
                export_dir=(result or {}).get("export_dir", ""))
            progress.set_source("")

    async def post_batch_export(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        group = str(data.get("group") or "").strip()
        try:
            concurrency = int(data.get("concurrency") or 0)
        except (TypeError, ValueError):
            concurrency = 0
        try:
            result = await run_in_threadpool(_batch_export, group,
                                             concurrency)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def get_export_dirs(request: Request) -> Response:
        # 目录结构（prompt 2026-09-07）：result/{日期}/{时间_账号_批量导出剧本}
        from .result import EXPORT_DIR_SUFFIX, RESULT_DIR
        dirs = []
        if RESULT_DIR.is_dir():
            found = []
            for d in RESULT_DIR.iterdir():
                if not d.is_dir():
                    continue
                if d.name.endswith(EXPORT_DIR_SUFFIX):
                    found.append(d)
                elif d.name.isdigit():
                    found.extend(x for x in d.iterdir()
                                 if x.is_dir()
                                 and x.name.endswith(EXPORT_DIR_SUFFIX))
            for d in sorted(found, reverse=True):
                count = sum(1 for p in d.iterdir()
                            if p.is_file() and p.suffix == ".json"
                            and p.name != "清单.json")
                dirs.append({"name": d.name, "count": count})
        return JSONResponse({"dirs": dirs})

    SEARCH_HEADERS = ["项目组", "剧本ID", "剧本名称", "剧本类型", "关键字",
                      "是否出现", "出现次数"]

    def _search(dir_name: str, keyword: str, with_count: bool) -> dict:
        import time as _time
        from .result import find_result_dir
        progress.set_source("search")
        start = _time.perf_counter()
        rows: list = []      # try 外初始化：finally 引用（2026-09-08 修复
        hits = 0             # 提前 return 时 UnboundLocalError 吞掉真错误）
        try:
            target = find_result_dir(dir_name)
            if not target:
                return {"error": "导出目录不存在，先执行第 1 步批量导出"}
            import json as _json
            mf = target / "清单.json"
            manifest = {}
            if mf.is_file():
                manifest = {row.get("export_file", ""): row for row in
                            _json.loads(mf.read_text(encoding="utf-8"))
                            .get("scripts", [])}
            progress.report_stage(f"搜索导出内容关键字 {keyword!r}",
                                  keyword=keyword, dir=dir_name)
            rows: list = []
            for p in sorted(target.iterdir()):
                if not p.is_file() or p.suffix != ".json" \
                        or p.name == "清单.json":
                    continue
                meta = manifest.get(p.name) or {}
                found, count = False, 0
                try:
                    with p.open("r", encoding="utf-8",
                                errors="replace") as f:
                        for line in f:
                            if keyword in line:
                                found = True
                                count += line.count(keyword)
                except OSError:
                    pass
                rows.append([meta.get("project_group", ""),
                             meta.get("script_id", ""),
                             meta.get("script_name", ""),
                             meta.get("agent_mode_name", ""),
                             keyword, "是" if found else "否",
                             count if with_count else ""])
            hits = sum(1 for r in rows if r[4] == "是")
            return {"headers": SEARCH_HEADERS, "rows": rows,
                    "total": len(rows), "hits": hits}
        finally:
            elapsed_s = round(_time.perf_counter() - start, 1)
            progress.report_done(
                f"搜索完成：命中 {hits}/{len(rows)} 个剧本",
                elapsed_s=elapsed_s, total=len(rows), hits=hits)
            progress.set_source("")

    async def post_search(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        keyword = str(data.get("keyword") or "").strip()
        if not keyword:
            return JSONResponse({"error": "缺少 keyword"}, status_code=400)
        dir_name = str(data.get("dir") or "").strip()
        with_count = bool(data.get("with_count"))
        try:
            result = await run_in_threadpool(
                _search, dir_name, keyword, with_count)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    # ---------------- 批量下载搜索分析Agent（prompt 2026-09-06） ----------------

    async def post_download_agents(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        agent_type = str(data.get("agent_type") or "").strip()
        try:
            concurrency = int(data.get("concurrency") or 0)
        except (TypeError, ValueError):
            concurrency = 0
        try:
            result = await run_in_threadpool(
                batch_download_agents, agent_type, concurrency)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def get_agents_dirs(request: Request) -> Response:
        # 目录结构（prompt 2026-09-07）：result/{日期}/{时间_账号_批量下载分析Agent}
        from .result import AGENTS_DIR_SUFFIX, RESULT_DIR
        dirs = []
        if RESULT_DIR.is_dir():
            found = []
            for d in RESULT_DIR.iterdir():
                if not d.is_dir():
                    continue
                if d.name.endswith(AGENTS_DIR_SUFFIX):
                    found.append(d)
                elif d.name.isdigit():
                    found.extend(x for x in d.iterdir()
                                 if x.is_dir()
                                 and x.name.endswith(AGENTS_DIR_SUFFIX))
            for d in sorted(found, reverse=True):
                count = sum(1 for p in d.iterdir()
                            if p.is_file() and p.suffix == ".md")
                dirs.append({"name": d.name, "count": count})
        return JSONResponse({"dirs": dirs})

    async def post_search_agents(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        keyword = str(data.get("keyword") or "").strip()
        if not keyword:
            return JSONResponse({"error": "缺少 keyword"}, status_code=400)
        dir_name = str(data.get("dir") or "").strip()
        with_count = bool(data.get("with_count"))
        try:
            result = await run_in_threadpool(
                search_downloaded_agents, dir_name, keyword, with_count)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    # ---------------- 批量删除剧本（prompt 2026-09-07） ----------------

    async def post_list_deletable(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            data = {}
        group = str((data or {}).get("group") or "").strip()
        try:
            result = await run_in_threadpool(list_deletable_scripts, group)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def post_delete_scripts(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        ids = [str(x) for x in data.get("script_ids") or [] if str(x)]
        if not ids:
            return JSONResponse({"error": "未选择要删除的剧本"}, status_code=400)
        try:
            result = await run_in_threadpool(batch_delete_scripts, ids)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def post_group_scripts(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        group = str(data.get("group") or "").strip()
        try:
            result = await run_in_threadpool(list_group_scripts, group)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:800]}, status_code=500)
        return JSONResponse(result)

    async def post_query_variables(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        group = str(data.get("group") or "").strip()
        script_id = str(data.get("script_id") or "").strip()
        try:
            concurrency = int(data.get("concurrency") or 0)
        except (TypeError, ValueError):
            concurrency = 0
        try:
            result = await run_in_threadpool(
                query_script_variables, group, script_id, concurrency)
        except Exception as e:  # noqa: BLE001
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def post_export_xlsx(request: Request) -> Response:
        try:
            data = await request.json()
        except ValueError:
            return JSONResponse({"error": "请求体非 JSON"}, status_code=400)
        headers = [str(h) for h in data.get("headers") or []]
        rows = [[("" if c is None else str(c)) for c in r]
                for r in (data.get("rows") or [])]
        if not headers:
            return JSONResponse({"error": "缺少表头"}, status_code=400)
        filename = str(data.get("filename") or "导出")
        # 2026-10-11 prompt：下载文件名前缀加当前火山引擎账号
        # （如 2105888584_批量查询剧本变量_20261011_103000.xlsx；账号取
        # 不到时不加前缀不影响导出）；最终文件名经 x-download-filename
        # 响应头回传前端（a.download 以服务端命名为准）
        try:
            account = str(_get_quick_client().get_current_account())
        except Exception:  # noqa: BLE001 - 未登录/账号获取失败不影响导出
            account = ""
        if account:
            filename = f"{account}_{filename}"
        try:
            content = make_xlsx(headers, rows)
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": str(e)[:800]}, status_code=500)
        from urllib.parse import quote
        from starlette.responses import Response as _Resp
        return _Resp(
            content,
            media_type="application/vnd.openxmlformats-officedocument"
                       ".spreadsheetml.sheet",
            headers={
                "content-disposition":
                    f"attachment; filename*=utf-8''{quote(filename)}.xlsx",
                "x-download-filename": quote(filename),
                "access-control-expose-headers": "x-download-filename",
            })

    return [
        Route("/api/groups", get_groups, methods=["GET"]),
        Route("/api/quick/query_scripts", post_query_scripts,
              methods=["POST"]),
        Route("/api/quick/query_unpublished", post_query_unpublished,
              methods=["POST"]),
        Route("/api/quick/batch_export", post_batch_export, methods=["POST"]),
        Route("/api/quick/export_dirs", get_export_dirs, methods=["GET"]),
        Route("/api/quick/search", post_search, methods=["POST"]),
        Route("/api/quick/download_agents", post_download_agents,
              methods=["POST"]),
        Route("/api/quick/agents_dirs", get_agents_dirs, methods=["GET"]),
        Route("/api/quick/search_agents", post_search_agents,
              methods=["POST"]),
        Route("/api/quick/deletable_scripts", post_list_deletable,
              methods=["POST"]),
        Route("/api/quick/delete_scripts", post_delete_scripts,
              methods=["POST"]),
        Route("/api/quick/group_scripts", post_group_scripts,
              methods=["POST"]),
        Route("/api/quick/query_variables", post_query_variables,
              methods=["POST"]),
        Route("/api/quick/query_unmasked_vars", post_query_unmasked_vars,
              methods=["POST"]),
        Route("/api/quick/batch_mask", post_batch_mask, methods=["POST"]),
        Route("/api/quick/publish_confirm", post_publish_confirm,
              methods=["POST"]),
        Route("/api/quick/batch_publish", post_batch_publish,
              methods=["POST"]),
        Route("/api/quick/export_xlsx", post_export_xlsx, methods=["POST"]),
    ]


def build_app(sse_starlette_app, host: str) -> Starlette:
    """主应用：Web 页面路由 + 静态资源（pics/ 架构图）+ MCP SSE（同端口）。"""
    from starlette.staticfiles import StaticFiles
    pics_dir = MD_DIR / "pics"   # 图片放 md/pics/（随 md 内容一起管理）
    if pics_dir.is_dir():
        return Starlette(routes=[
            *build_web_routes(),
            Mount("/pics", app=StaticFiles(directory=str(pics_dir))),
            Mount("/", app=sse_starlette_app),
        ])
    return Starlette(routes=[
        *build_web_routes(),
        Mount("/", app=sse_starlette_app),
    ])
