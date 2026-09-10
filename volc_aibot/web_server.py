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
    GET  /api/tools      MCP 工具清单（名称/描述/参数）
    GET  /api/md?name=x  读取 md 目录下的 md 文件内容
"""

from __future__ import annotations

import asyncio
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
        from .autostart import is_autostart_enabled
        return JSONResponse({
            "server_port": port,
            "allow_mutation": global_config.get_allow_mutation(),
            "allowed_account": cfg.get("allowed_account", ""),
            "sse_url": f"http://127.0.0.1:{port}/sse",
            "config_path": global_config.config_path(),
            "autostart": is_autostart_enabled(),
        })

    async def post_autostart(request: Request) -> Response:
        """切换系统自启动（注册表 Run 键，prompt 2026-09-07，默认关闭）。"""
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
        if not cfg:
            return JSONResponse({"error": "无有效配置项"}, status_code=400)
        saved = global_config.save_config(cfg)
        return JSONResponse({
            "server_port": saved["server_port"],
            "allow_mutation": saved["allow_mutation"],
            "allowed_account": saved["allowed_account"],
            "restart_required": True,
            "message": "已保存（端口修改需重启服务生效）",
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
        Route("/api/tools", get_tools, methods=["GET"]),
        Route("/api/md", get_md, methods=["GET"]),
        Route("/api/account", get_account, methods=["POST"]),
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
    "测试版本发布-版本号", "测试版本发布-更新时间",
    "线上版本发布-版本号", "线上版本发布-更新时间",
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
        pv.get("version"), pv.get("update_time"),
        ov.get("version"), ov.get("update_time"),
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

    def _query_all(group: str, concurrency: int | None = None) -> dict:
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
            for g in selected:
                gname = g.get("group_name") or str(g.get("id"))
                progress.report_stage("查询项目组下的剧本", group=gname)
                scripts = client.list_group_all_scripts(g["id"])

                def query_one(s: dict):
                    # 传入清单项：省 query_script（agent/list 数据已在 s 中）
                    sid = s.get("AgentID") or ""
                    coords = {"project": s.get("ProjectID"),
                              "service": s.get("ServiceID"),
                              "group": s.get("GroupID"),
                              "agent_id": sid,
                              "agent_name": s.get("AgentName") or ""}
                    return _script_info_row(client.get_script_info(
                        sid, coords=coords, agent=s))

                def on_progress(s: dict, index: int, total: int) -> None:
                    # worker 线程：先标记来源，使 HTTP 请求事件同样归属
                    progress.set_source("query")
                    progress.report_stage(
                        f"查询剧本基本信息 {s.get('AgentID') or ''}",
                        group=gname,
                        script_id=s.get("AgentID") or "",
                        script_name=s.get("AgentName") or "",
                        index=index, total=total)

                # 并发查询（prompt 2026-09-06：获得剧本后并发查询剧本信息）
                results = run_parallel(scripts, query_one, concurrency=c,
                                       progress=on_progress)
                for s, r in zip(scripts, results):
                    sid = s.get("AgentID") or ""
                    if isinstance(r, tuple) and r and r[0] == "error":
                        errors += 1
                        rows.append([gname, sid, s.get("AgentName") or "",
                                     "（查询失败）"] + [""] * 24)
                    else:
                        rows.append(r)
            return {"headers": SCRIPT_INFO_HEADERS, "rows": rows,
                    "total": len(rows), "errors": errors}
        finally:
            elapsed_s = round(_time.perf_counter() - start, 1)
            # 完成事件：清空面板计时、显示完成提示（页面未聚焦时闪烁提醒）
            summary = f"批量查询完成：共 {len(rows)} 个剧本"
            if errors:
                summary += f"，{errors} 个失败"
            progress.report_done(summary, elapsed_s=elapsed_s,
                                 total=len(rows), errors=errors)
            progress.set_source("")

    async def post_query_scripts(request: Request) -> Response:
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
            result = await run_in_threadpool(_query_all, group, concurrency)
        except Exception as e:  # noqa: BLE001
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
            for g in selected:
                gname = g.get("group_name") or str(g.get("id"))
                progress.report_stage("查询项目组下的剧本", group=gname)
                scripts = client.list_group_all_scripts(g["id"])

                def export_one(s: dict):
                    sid = s.get("AgentID") or ""
                    filename, content = client.export_script(sid)
                    path = out_dir / safe_filename(
                        f"{safe_filename(gname)}_{safe_filename(filename)}")
                    path.write_bytes(content)
                    return {"export_file": path.name,
                            "size_bytes": len(content)}

                def on_progress(s: dict, index: int, total: int) -> None:
                    # worker 线程：先标记来源，使 HTTP 请求事件同样归属
                    progress.set_source("export")
                    progress.report_stage(
                        f"导出剧本 {s.get('AgentID') or ''}", group=gname,
                        script_id=s.get("AgentID") or "",
                        script_name=s.get("AgentName") or "",
                        index=index, total=total)

                # 并发导出（prompt 2026-09-06：获得剧本后并行执行导出）
                results = run_parallel(scripts, export_one, concurrency=c,
                                       progress=on_progress)
                for s, r in zip(scripts, results):
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
            })

    return [
        Route("/api/groups", get_groups, methods=["GET"]),
        Route("/api/quick/query_scripts", post_query_scripts,
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
