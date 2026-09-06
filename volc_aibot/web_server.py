# -*- coding: utf-8 -*-
"""Web 页面 + MCP SSE 同端口服务（prompt 要求）。

页面结构（标签页，宽度 100%）：
- MCP 接口：工具下拉框（名称+中文描述）选择后展示参数等详情；
- 配置参数：监听端口（重启提示/双服务说明/SSE URL）、修改操作允许账号；
- 使用说明 / 适用场景 / 提示词示例：读取项目 md/ 目录的 md 文件渲染，
  左侧标题目录 + 锚点跳转（布局参考 chrome_capture_operate 的 index.html）。

后端路由：
    GET  /               页面（HTML）
    GET  /api/config     读取全局配置
    POST /api/config     保存配置（端口 / 允许账号）
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


PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>volc-aibot 控制台</title>
<style>
 * { box-sizing: border-box; }
 body { font-family: "Microsoft YaHei", sans-serif; margin: 0; color: #1f2937;
        background: #f5f6f8; width: 100%; }
 header { background: #fff; border-bottom: 1px solid #e5e7eb; padding: 14px 20px;
          display: flex; align-items: center; gap: 10px; }
 header .logo { font-size: 17px; font-weight: 700; color: #1f3a5f; }
 header .status { font-size: 12px; color: #6b7280; }
 nav { background: #fff; border-bottom: 1px solid #ddd; padding: 0 12px;
       display: flex; }
 nav button { border: 0; background: none; padding: 12px 18px; cursor: pointer;
              font-size: 14px; border-bottom: 2px solid transparent; color: #4b5563; }
 nav button.active { border-bottom-color: #1f3a5f; color: #1f3a5f; font-weight: 600; }
 .tab { display: none; padding: 18px 20px; width: 100%; }
 .tab.active { display: block; }
 .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px;
         padding: 20px 24px; margin-bottom: 16px; }
 h1 { font-size: 18px; margin: 0 0 14px; }
 h2 { font-size: 16px; margin: 18px 0 8px; }
 label { display: block; font-weight: 600; margin: 12px 0 6px; }
 input, select { padding: 8px 10px; border: 1px solid #d1d5db;
                 border-radius: 6px; font-size: 14px; }
 input[type=number], input[type=text] { width: 320px; }
 select { width: 100%; max-width: 900px; }
 .hint { color: #6b7280; font-size: 12.5px; margin-top: 4px; line-height: 1.7; }
 .url, code { font-family: Consolas, monospace; background: #f3f4f6;
        padding: 2px 6px; border-radius: 4px; word-break: break-all; }
 .url-row { display: flex; align-items: center; gap: 8px; margin: 4px 0; }
 .url-row .url { flex: 1; min-width: 0; }
 button.copy { padding: 5px 14px; border: 1px solid #2563eb; border-radius: 6px;
        background: #fff; color: #2563eb; font-size: 13px; cursor: pointer;
        white-space: nowrap; }
 button.copy:hover { background: #eff6ff; }
 .warn { color: #b45309; }
 .alert { display: block; background: #fffbeb; border: 1px solid #fcd34d;
          color: #92400e; padding: 12px 20px; font-size: 13.5px;
          line-height: 1.8; }
 .alert a { color: #1d4ed8; font-weight: 600; }
 button.primary { margin-top: 14px; padding: 9px 22px; border: 0;
          border-radius: 6px; background: #2563eb; color: #fff; font-size: 14px;
          cursor: pointer; }
 button.primary:hover { background: #1d4ed8; }
 .ok { color: #059669; margin-left: 12px; }
 table { border-collapse: collapse; margin-top: 8px; }
 th, td { border: 1px solid #e5e7eb; font-size: 13px; padding: 6px 10px;
          text-align: left; }
 th { background: #f9fafb; }
 .req { color: #b91c1c; font-weight: 600; }
 .tdesc { font-size: 13px; color: #374151; margin: 8px 0; white-space: pre-wrap; }
 /* md 内容页：左目录 + 正文 */
 .usage-split { display: flex; gap: 18px; width: 100%; }
 .usage-toc { display: block; width: 230px; flex: none; position: sticky;
              top: 10px; max-height: calc(100vh - 30px); overflow-y: auto;
              background: #fff; border: 1px solid #e5e7eb; border-radius: 8px;
              padding: 8px 4px; align-self: flex-start; }
 .usage-toc .toc-title { font-size: 13px; font-weight: 700; color: #1f3a5f;
              padding: 2px 8px 6px; border-bottom: 1px solid #eee; margin-bottom: 4px; }
 .usage-toc a { display: block; padding: 4px 8px; margin: 1px 0;
              border-radius: 3px; font-size: 13px; color: #374151;
              cursor: pointer; text-decoration: none; }
 .usage-toc a:hover { background: #f0f6ff; color: #1f6fd6; }
 .usage-toc a.active { background: #dceaff; color: #1f3a5f; font-weight: 600; }
 .usage-toc a.lv2 { padding-left: 20px; }
 .usage-toc a.lv3 { padding-left: 32px; }
 .usage-toc a.lv4 { padding-left: 44px; }
 .usage-body { flex: 1; min-width: 0; background: #fff;
              border: 1px solid #e5e7eb; border-radius: 8px; padding: 22px 28px; }
 .usage-body h1 { font-size: 20px; border-bottom: 2px solid #e5e7eb;
                  padding-bottom: 8px; }
 .usage-body h2 { font-size: 17px; margin-top: 22px; }
 .usage-body h3 { font-size: 15px; margin-top: 16px; }
 .usage-body pre { background: #f6f8fa; border: 1px solid #e5e7eb;
              border-radius: 6px; padding: 10px 12px; overflow: auto; }
 .usage-body pre code { background: none; padding: 0; }
 .usage-body blockquote { border-left: 4px solid #93c5fd; background: #eff6ff;
              margin: 8px 0; padding: 8px 12px; color: #1e40af; }
 .usage-body table { display: block; overflow-x: auto; }
 /* 快捷工具页 */
 .quick-body { flex: 1; min-width: 0; }
 .quick-body table { display: block; overflow-x: auto; max-height: 60vh;
                     overflow-y: auto; }
 .quick-body .table-ops { margin-top: 10px; display: flex; gap: 8px; }
 /* 执行状态面板（页面上方共享，可折叠；WebSocket 实时） */
 .ws-dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%;
           margin-left: 8px; vertical-align: middle; }
 .ws-dot.ok { background: #16a34a; animation: pulse 1.6s infinite; }
 .ws-dot.off { background: #d1d5db; }
 @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
 .progress-toggle { cursor: pointer; user-select: none; }
 .progress-toggle:hover { color: #1f6fd6; }
 /* 展开按钮（prompt：展开功能需要明显提示，小箭头不够醒目） */
 .progress-toggle .expand-btn { display: inline-block; padding: 2px 10px;
           margin-left: 8px; border: 1px solid #2563eb; border-radius: 6px;
           background: #fff; color: #2563eb; font-size: 12px;
           font-weight: 600; }
 .progress-toggle:hover .expand-btn { background: #eff6ff; }
 .progress-toggle .arrow { display: inline-block; margin-right: 6px;
           transition: transform .2s; }
 .progress-toggle.expanded .arrow { transform: rotate(90deg); }
 .progress-toggle .head-tool { color: #4b5563; font-weight: 400;
           font-size: 12px; margin-left: 6px; }
 .progress-head { font-size: 13px; font-weight: 700; color: #1f3a5f; }
 .progress-bar { height: 18px; background: #eef2f7; border-radius: 9px;
                 overflow: hidden; margin-top: 8px; }
 .progress-fill { height: 100%; width: 0; background: #1f6fd6; color: #fff;
                  text-align: center; font-size: 12px; line-height: 18px;
                  transition: width .3s; white-space: nowrap; }
 .progress-fill.full { background: #16a34a; }
 .progress-current { font-weight: 600; color: #1f3a5f; margin: 8px 0;
                     padding: 8px 10px; background: #f0f6ff;
                     border: 1px solid #dceaff; border-radius: 6px; }
 .progress-current.done { background: #ecfdf5; border-color: #a7f3d0;
                          color: #047857; }
 .progress-current .sub { display: block; font-weight: 400; color: #374151;
                          font-size: 12px; margin-top: 2px; }
 .progress-list { margin-top: 6px; max-height: 160px; overflow-y: auto;
                  font-size: 12px; color: #4b5563; }
 .progress-list .row { padding: 2px 6px; border-bottom: 1px dashed #f0f0f0;
                       display: flex; gap: 8px; }
 .progress-list .row .t { color: #9ca3af; flex: none; }
 .progress-list .row .ms { color: #16a34a; flex: none; }
 @media (max-width: 900px) { .usage-split { display: block; } .usage-toc { display: none; } }
</style>
</head>
<body>

<header>
  <span class="logo">火山引擎智能外呼工具</span>
  <span class="status" id="headStatus">加载中…</span>
</header>

<div id="account-alert" class="alert" style="display:none">
  ⚠ <b>尚未配置「修改操作允许执行的账号」</b>——修改类操作（剧本变量修改、
  变量赋值、发布测试版本）会被拒绝并提示配置。请在下方「<b>配置参数</b>」
  标签页填写并保存（建议使用测试环境账号）。
  <a href="#" onclick="switchTab('config');return false;">点此前往配置 →</a>
</div>

<nav>
  <button data-tab="quick" class="active">快捷工具</button>
  <button data-tab="tools">MCP接口</button>
  <button data-tab="config">配置参数</button>
  <button data-tab="md-使用说明">使用说明</button>
  <button data-tab="md-适用场景">适用场景</button>
  <button data-tab="md-提示词示例">提示词示例</button>
</nav>

<!-- ============ 快捷工具 ============ -->
<div class="tab active" id="tab-quick">
  <div class="usage-split">
    <nav class="usage-toc" id="quick-list">
      <div class="toc-title">快捷工具</div>
      <a class="active" data-tool="query" onclick="quickTool('query')">批量查询剧本信息</a>
      <a data-tool="search" onclick="quickTool('search')">批量搜索剧本内容</a>
      <a data-tool="agents" onclick="quickTool('agents')">批量下载搜索分析Agent</a>
    </nav>
    <div class="quick-body">

      <div class="card" id="progress-card">
        <div class="progress-head">
          <span class="progress-toggle" id="progress-toggle"
                onclick="toggleProgress()"><span class="arrow">▸</span>执行状态
            <span class="expand-btn" id="progress-expand-btn">展开详情 ▾</span>
            <span class="head-tool">当前工具：<b
                id="progress-tool">批量查询剧本信息</b></span></span>
          <span class="ws-dot off" data-ws-dot></span>
        </div>
        <div class="progress-bar"><div id="pb-cur" class="progress-fill"></div></div>
        <div id="pc-cur" class="progress-current">（空闲）</div>
        <div id="pl-cur" class="progress-list" style="display:none"></div>
      </div>

      <div class="card" id="quick-panel-query">
        <h1>批量查询剧本信息</h1>
        <div class="hint">按项目组批量查询其下全部剧本的基本信息，
          输出字段与「查询剧本基本信息」一致（剧本类型/对话控制/LLM模型/
          ASR设置/分析Agents/发布状态等）。剧本较多时耗时相应增加。</div>
        <label>项目组范围</label>
        <select id="q-group" style="max-width:360px;width:360px">
          <option value="">（全部项目组）</option>
        </select>
        <label style="margin-top:10px">并发数（获得剧本清单后并行查询剧本信息；默认 5，实测提速约 3.7 倍且无错误，过大有服务端频控风险）</label>
        <select id="q-concurrency" style="max-width:160px;width:160px">
          <option value="1">1（串行）</option>
          <option value="3">3</option>
          <option value="5" selected>5（默认）</option>
          <option value="8">8</option>
          <option value="10">10</option>
        </select>
        <div style="margin-top:12px">
          <button class="primary" id="q-btn" onclick="quickQuery()">查询</button>
          <span id="q-msg" class="ok"></span>
        </div>
        <div id="q-result"></div>
      </div>

      <div class="card" id="quick-panel-search" style="display:none">
        <h1>批量搜索剧本内容</h1>

        <h2>第 1 步：批量导出剧本（供搜索）</h2>
        <div class="hint">在导出剧本目录中按行搜索关键字。如无导出目录或内容
          已过期，点击「重新导出一次」生成新的导出目录。</div>
        <label>导出范围</label>
        <select id="s-group" style="max-width:360px;width:360px">
          <option value="">（全部项目组）</option>
        </select>
        <label style="margin-top:10px">并发数（获得剧本清单后并行导出；默认 5，实测提速约 3.7 倍且无错误，过大有服务端频控风险）</label>
        <select id="s-concurrency" style="max-width:160px;width:160px">
          <option value="1">1（串行）</option>
          <option value="3">3</option>
          <option value="5" selected>5（默认）</option>
          <option value="8">8</option>
          <option value="10">10</option>
        </select>
        <div style="margin-top:12px">
          <button class="primary" id="s-exp-btn" onclick="quickExport()">重新导出一次</button>
          <span id="s-exp-msg" class="ok"></span>
        </div>

        <h2>第 2 步：选择导出目录并搜索</h2>
        <label>导出剧本目录（result/ 下以 _批量导出剧本 结尾）</label>
        <select id="s-dir" style="max-width:600px;width:600px"></select>
        <button class="copy" onclick="loadExportDirs()">刷新目录</button>
        <label>关键字</label>
        <input id="s-keyword" type="text" style="width:320px"
               placeholder="要搜索的关键字">
        <label style="font-weight:400;margin-top:12px">
          <input type="checkbox" id="s-count" style="width:auto"> 获取出现次数（默认否）
        </label>
        <div style="margin-top:12px">
          <button class="primary" id="s-btn" onclick="quickSearch()">搜索</button>
          <span id="s-msg" class="ok"></span>
        </div>
        <div id="s-result"></div>
      </div>

      <div class="card" id="quick-panel-agents" style="display:none">
        <h1>批量下载搜索分析Agent</h1>

        <h2>第 1 步：批量下载分析Agent系统提示词</h2>
        <div class="hint">获取当前账号全部分析Agent的系统提示词，保存到
          result/ 目录（以「_批量下载分析Agent」结尾）。</div>
        <label>分析Agent类型</label>
        <select id="a-type" style="max-width:240px;width:240px">
          <option value="">（全部类型）</option>
          <option value="外呼-通话总结">外呼-通话总结</option>
          <option value="外呼-信息抽取">外呼-信息抽取</option>
          <option value="外呼-线索定级">外呼-线索定级</option>
        </select>
        <label style="margin-top:10px">并发数（默认 5，实测提速数倍且无错误，过大有服务端频控风险）</label>
        <select id="a-concurrency" style="max-width:160px;width:160px">
          <option value="1">1（串行）</option>
          <option value="3">3</option>
          <option value="5" selected>5（默认）</option>
          <option value="8">8</option>
          <option value="10">10</option>
        </select>
        <div style="margin-top:12px">
          <button class="primary" id="a-dl-btn" onclick="downloadAgents()">重新获取一次</button>
          <span id="a-dl-msg" class="ok"></span>
        </div>

        <h2>第 2 步：选择下载目录并搜索</h2>
        <label>下载分析Agent目录（result/ 下以 _批量下载分析Agent 结尾）</label>
        <select id="a-dir" style="max-width:600px;width:600px"></select>
        <button class="copy" onclick="loadAgentsDirs()">刷新目录</button>
        <label>关键字</label>
        <input id="a-keyword" type="text" style="width:320px"
               placeholder="要搜索的关键字">
        <label style="font-weight:400;margin-top:12px">
          <input type="checkbox" id="a-count" style="width:auto"> 获取出现次数（默认否）
        </label>
        <div style="margin-top:12px">
          <button class="primary" id="a-btn" onclick="searchAgents()">搜索</button>
          <span id="a-msg" class="ok"></span>
        </div>
        <div id="a-result"></div>
      </div>

    </div>
  </div>
</div>

<!-- ============ MCP接口 ============ -->
<div class="tab" id="tab-tools">
  <div class="card">
    <h1>MCP 接口</h1>
    <div class="hint">当前服务支持的全部 MCP 工具。选择工具后查看描述与参数详情
      （带 <span class="req">*</span> 为必填参数）。</div>
    <label>选择工具</label>
    <select id="toolSelect"><option>加载中…</option></select>
    <div id="toolDetail"></div>
  </div>
</div>

<!-- ============ 配置参数 ============ -->
<div class="tab" id="tab-config">
  <div class="card">
    <h1>配置参数</h1>

    <label>监听端口</label>
    <input id="port" type="number" min="1024" max="65535">
    <div class="hint">
      本端口同时提供 <b>Web 页面</b>（本页）与 <b>MCP SSE 服务</b>。
      修改后需<b>重启服务（托盘右键退出后重新 start.bat）</b>生效。
    </div>

    <h2>MCP SSE 地址与安装命令</h2>
    <div class="hint">
      <div class="section-title" style="margin-top:4px">当前可使用的 MCP SSE URL</div>
      <div class="url-row">
        <code class="url" id="sse-endpoint"></code>
        <button class="copy" id="btn-copy-endpoint" title="复制 SSE 端点 URL">复制</button>
      </div>
      <div class="section-title">安装 MCP 到 Claude 全局配置的命令</div>
      <div class="url-row">
        <code class="url" id="sse-url"></code>
        <button class="copy" id="btn-copy-sse" title="复制安装命令（--scope user 写入用户全局配置，所有项目可用）">复制</button>
        <span class="ok" id="copy-msg"></span>
      </div>
      在命令行粘贴执行上面的安装命令后，即可在任意 Claude Code 会话中使用本工具。
    </div>

    <label>修改操作允许执行的账号</label>
    <input id="account" type="text" placeholder="填写允许的账号ID（数字）">
    <div class="hint">
      只允许配置一个账号。所有<b>修改类操作</b>（发布剧本测试版本、剧本变量修改、
      测试版本全局变量赋值）执行前会校验当前 Chrome 登录账号，不一致则拒绝执行；
      查询类操作不受限制。<br>
      <b>建议配置测试环境的账号</b>：在测试环境完成剧本修改后，再人工导入
      测试环境使用的账号，以保证生产环境账号数据不被 AI 误修改。<br>
      当前登录账号可用脚本查看：<code>scripts\\get_current_user.py</code>
      （加 <code>--save-allowed</code> 可直接保存为允许账号）。
    </div>

    <button class="primary" onclick="save()">保存</button>
    <span id="msg" class="ok"></span>

    <h2>使用提醒</h2>
    <div class="hint">
      <ul>
        <li>⚠ <b>文本对话测试占用生产实际外呼资源</b>：请控制并发
            （不要同时跑大量会话）、避免业务高峰，建议在晚上测试。</li>
        <li>系统托盘双击可打开本页；右键菜单可退出服务。</li>
        <li>运行前提：chrome_capture_operate 服务已启动
            （默认 127.0.0.1:33445）且日常 Chrome 已登录火山引擎控制台；
            若提示“Cookie 服务不可达 / 获取 Cookie 失败”，请先检查
            chrome_capture_operate 是否正在正常运行。</li>
        <li>配置文件位置：<code class="url" id="cfg-path"></code></li>
        <li>MCP 工具含 usage_guide 说明工具，AI 不确定用法时会先调用它；
            也可在「MCP接口」标签页查看全部接口。</li>
      </ul>
    </div>
  </div>
</div>

<!-- ============ md 内容页（使用说明/适用场景/提示词示例） ============ -->
<div class="tab" id="tab-md-使用说明">
  <div class="usage-split">
    <nav class="usage-toc" id="toc-使用说明"></nav>
    <div class="usage-body" id="body-使用说明">加载中…</div>
  </div>
</div>
<div class="tab" id="tab-md-适用场景">
  <div class="usage-split">
    <nav class="usage-toc" id="toc-适用场景"></nav>
    <div class="usage-body" id="body-适用场景">加载中…</div>
  </div>
</div>
<div class="tab" id="tab-md-提示词示例">
  <div class="usage-split">
    <nav class="usage-toc" id="toc-提示词示例"></nav>
    <div class="usage-body" id="body-提示词示例">加载中…</div>
  </div>
</div>

<script>
const esc = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

/* ---------- 标签页切换 ---------- */
function switchTab(key) {
  document.querySelectorAll('nav button[data-tab]').forEach(b =>
    b.classList.toggle('active', b.dataset.tab === key));
  document.querySelectorAll('.tab').forEach(t =>
    t.classList.toggle('active', t.id === 'tab-' + key));
  const name = key.replace(/^md-/, '');
  if (key.startsWith('md-')) loadMd(name);
  if (key === 'tools') loadTools();
  if (key === 'config') loadConfig();
  window.scrollTo(0, 0);
}
document.querySelectorAll('nav button[data-tab]').forEach(btn => {
  btn.onclick = () => switchTab(btn.dataset.tab);
});

/* ---------- 配置参数 ---------- */
async function loadConfig() {
  const r = await fetch('/api/config');
  const c = await r.json();
  document.getElementById('port').value = c.server_port;
  document.getElementById('account').value = c.allowed_account;
  document.getElementById('headStatus').textContent =
      '端口 ' + c.server_port + ' · ' + (c.allowed_account ?
      '允许账号 ' + c.allowed_account : '未配置允许账号');
  bindCopyButtons(c.server_port);
}

/* ---------- 复制按钮（SSE URL / Claude 全局安装命令） ---------- */
function bindCopyButtons(port) {
  const endpoint = 'http://127.0.0.1:' + port + '/sse';
  const cmdGlobal = 'claude mcp add --scope user --transport sse volc-aibot ' + endpoint;
  document.getElementById('sse-endpoint').textContent = endpoint;
  document.getElementById('sse-url').textContent = cmdGlobal;
  bindCopy('btn-copy-endpoint', endpoint, null);
  bindCopy('btn-copy-sse', cmdGlobal, 'copy-msg');
}

function bindCopy(btnId, text, msgId) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.onclick = async () => {
    let ok = false;
    try {
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch (e) {
      // 降级：非安全上下文(非localhost页面)时用 execCommand
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        ok = document.execCommand('copy');
        document.body.removeChild(ta);
      } catch (e2) { ok = false; }
    }
    const msg = msgId ? document.getElementById(msgId) : null;
    if (msg) {
      msg.textContent = ok ? '已复制 ✓' : '复制失败，请手动选择文本复制';
      setTimeout(() => msg.textContent = '', 2000);
    } else if (!ok) {
      alert('复制失败，请手动选择文本复制');
    }
    if (!ok && btn) {
      // 高亮便于手动复制
      const code = btn.parentElement.querySelector('.url');
      if (code) { code.style.background = '#fef3c7'; }
    }
  };
}
async function save() {
  const port = parseInt(document.getElementById('port').value, 10);
  const account = document.getElementById('account').value.trim();
  if (!(port >= 1024 && port <= 65535)) { alert('端口需为 1024~65535'); return; }
  if (account && !/^\\d+$/.test(account)) {
      alert('账号应为数字（账号ID）'); return;
  }
  const r = await fetch('/api/config', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({server_port: port, allowed_account: account})
  });
  const c = await r.json();
  if (c.error) { alert(c.error); return; }
  document.getElementById('msg').textContent = '已保存';
  setTimeout(() => location.reload(), 900);
}

/* ---------- MCP接口 ---------- */
let TOOLS = [];
async function loadTools() {
  try {
    const r = await fetch('/api/tools');
    const d = await r.json();
    TOOLS = d.tools || [];
    const sel = document.getElementById('toolSelect');
    if (!TOOLS.length) {
      sel.innerHTML = '<option>（暂无工具）</option>';
      document.getElementById('toolDetail').innerHTML =
          '<div class="hint">' + (d.error || '工具清单为空') + '</div>';
      return;
    }
    // 下拉框：名称 + 中文描述摘要（首行）
    sel.innerHTML = TOOLS.map(t => {
      const brief = (t.description || '').split('\\n')[0].slice(0, 40);
      return '<option value="' + esc(t.name) + '">' + esc(t.name) +
             ' — ' + esc(brief) + '</option>';
    }).join('');
    sel.onchange = () => renderTool(sel.value);
    renderTool(sel.value);
  } catch (e) {
    document.getElementById('toolDetail').innerHTML =
        '<div class="hint">工具清单加载失败: ' + esc(e) + '</div>';
  }
}
function renderTool(name) {
  const t = TOOLS.find(x => x.name === name);
  const box = document.getElementById('toolDetail');
  if (!t) { box.innerHTML = ''; return; }
  const rows = (t.params || []).map(p =>
      '<tr><td>' + esc(p.name) + (p.required ? ' <span class="req">*</span>' : '') +
      '</td><td>' + esc(p.type || '-') + '</td><td>' + esc(p.description || '') +
      '</td></tr>').join('');
  const ptable = rows ?
      '<table><tr><th>参数</th><th>类型</th><th>说明</th></tr>' + rows + '</table>'
      : '<div class="hint">无参数</div>';
  box.innerHTML = '<h2>' + esc(t.name) + '</h2>' +
      '<div class="tdesc">' + esc(t.description) + '</div>' + ptable;
}

/* ---------- md 内容页（左目录 + 正文渲染） ---------- */
let mdHeads = {}, mdScrollTimer = null;
async function loadMd(name) {
  const body = document.getElementById('body-' + name);
  const toc = document.getElementById('toc-' + name);
  try {
    const r = await fetch('/api/md?name=' + encodeURIComponent(name));
    const d = await r.json();
    if (d.error) {
      body.innerHTML = '<div class="hint">内容加载失败: ' + esc(d.error) + '</div>';
      toc.innerHTML = ''; return;
    }
    const res = renderMarkdown(d.content || '', 'md-' + name);
    body.innerHTML = res.html;
    toc.innerHTML = '<div class="toc-title">目录</div>' + res.toc.map(h =>
        '<a data-anchor="' + h.id + '" class="lv' + h.level + '" title="' +
        esc(h.text) + '">' + esc(h.text) + '</a>').join('');
    toc.querySelectorAll('a[data-anchor]').forEach(a => {
      a.onclick = () => {
        const el = document.getElementById(a.dataset.anchor);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      };
    });
    mdHeads[name] = res.toc.map(h => ({ id: h.id, top: 0 }));
    syncTops(name);
  } catch (e) {
    body.innerHTML = '<div class="hint">内容加载失败: ' + esc(e) + '</div>';
  }
}
function syncTops(name) {
  (mdHeads[name] || []).forEach(h => {
    const el = document.getElementById(h.id);
    h.top = el ? el.getBoundingClientRect().top + window.scrollY : Infinity;
  });
}
window.addEventListener('scroll', () => {
  const activeTab = document.querySelector('.tab.active');
  if (!activeTab || !activeTab.id.startsWith('tab-md-')) return;
  const name = activeTab.id.replace('tab-md-', '');
  const heads = mdHeads[name] || [];
  if (!heads.length) return;
  if (mdScrollTimer) return;
  mdScrollTimer = setTimeout(() => {
    mdScrollTimer = null;
    syncTops(name);
    const y = window.scrollY + 30;
    let cur = heads[0] ? heads[0].id : null;
    for (const h of heads) if (h.top <= y) cur = h.id;
    document.querySelectorAll('#toc-' + CSS.escape(name) +
        ' a[data-anchor]').forEach(a =>
        a.classList.toggle('active', a.dataset.anchor === cur));
  }, 150);
}, { passive: true });

/* ---------- 轻量 markdown -> HTML（参考 chrome_capture_operate 本地实现） ---------- */
function renderMarkdown(text, idPrefix) {
  // idPrefix：页面级 id 前缀（如 md-使用说明），保证各 md 页标题 id 唯一
  // ——否则三页同时渲染时 getElementById('md-h-1') 恒命中第一页的隐藏元素，
  // 左侧目录跳转失效（2026-09-06 实测适用场景/提示词示例跳转无效的根因）
  const lines = String(text).split('\\n');
  let html = [], inCode = false, listType = null, tableBuf = [],
      toc = [], hSeq = 0;
  const closeList = () => { if (listType) { html.push('</' + listType + '>'); listType = null; } };
  const flushTable = () => {
    if (!tableBuf.length) return;
    const rows = tableBuf.map(l => l.replace(/^\\|/, '').replace(/\\|\\s*$/, '')
        .split('|').map(c => c.trim()));
    const isSep = i => /^:?-{2,}:?$/.test((rows[i] || [''])[0].replace(/\\s/g, ''));
    let out = '<table>';
    rows.forEach((cells, i) => {
      if (isSep(i)) return;
      const tag = (i === 0 && rows.length > 1 && isSep(1)) ? 'th' : 'td';
      out += '<tr>' + cells.map(c => '<' + tag + '>' + inlineMd(c) + '</' + tag + '>').join('') + '</tr>';
    });
    out += '</table>';
    html.push(out);
    tableBuf = [];
  };
  const inlineMd = s => esc(s)
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\\*\\*([^*]+)\\*\\*/g, '<b>$1</b>');
  for (const line of lines) {
    const t = line.trim();
    if (t.startsWith('```')) {
      if (inCode) { html.push('</code></pre>'); inCode = false; }
      else { closeList(); flushTable(); html.push('<pre><code>'); inCode = true; }
      continue;
    }
    if (inCode) { html.push(esc(line)); continue; }
    if (!t) { closeList(); flushTable(); continue; }
    if (t.startsWith('|')) { closeList(); tableBuf.push(t); continue; }
    flushTable();
    let m;
    if ((m = t.match(/^(#{1,6})\\s+(.*)$/))) {
      closeList();
      const lv = m[1].length;
      const id = (idPrefix || 'md') + '-h-' + (++hSeq);
      html.push('<h' + lv + ' id="' + id + '">' + inlineMd(m[2]) + '</h' + lv + '>');
      if (lv <= 4) toc.push({ level: lv, text: m[2].replace(/[*`]/g, ''), id });
    } else if ((m = t.match(/^[-*]\\s+(.*)$/))) {
      if (listType !== 'ul') { closeList(); html.push('<ul>'); listType = 'ul'; }
      html.push('<li>' + inlineMd(m[1]) + '</li>');
    } else if ((m = t.match(/^\\d+\\.\\s+(.*)$/))) {
      if (listType !== 'ol') { closeList(); html.push('<ol>'); listType = 'ol'; }
      html.push('<li>' + inlineMd(m[1]) + '</li>');
    } else if ((m = t.match(/^>\\s?(.*)$/))) {
      closeList();
      html.push('<blockquote>' + inlineMd(m[1]) + '</blockquote>');
    } else {
      closeList();
      html.push('<p>' + inlineMd(t) + '</p>');
    }
  }
  if (inCode) html.push('</code></pre>');
  closeList();
  flushTable();
  return { html: html.join('\\n'), toc };
}

/* ---------- 执行状态（WebSocket 实时推送，断线 2s 自动重连） ----------
   每个快捷工具独立面板展示：事件带 source（query=批量查询，
   export=批量导出，search=搜索）分流；无 source 的事件（普通
   MCP 工具请求等）不进入快捷工具面板。
   完成提醒：done 事件清空计时并显示 ✓ 完成提示；用户不在本页
   （标签页未聚焦）时闪烁浏览器标签页标题。 */
let ws = null, wsRetry = null, wsGen = 0, titleTimer = null;
/* 执行状态：数据按 source 分源保存，展示面板在页面上方共享一条，
   渲染当前工具对应的数据（prompt 2026-09-06 终版）：
   - 不展开：仅百分比进度条（总数/已完成）；
   - 展开：发起的请求 + 剧本ID/剧本名称等参数 + 最近事件。 */
const PANELS = {
  query: { label: '批量查询剧本信息',
           inFlight: new Map(), stage: null, stageTs: 0, done: null,
           maxIdx: 0, total: 0, history: [] },
  export: { label: '批量搜索剧本内容',
            inFlight: new Map(), stage: null, stageTs: 0, done: null,
            maxIdx: 0, total: 0, history: [] },
  agents: { label: '批量下载搜索分析Agent',
            inFlight: new Map(), stage: null, stageTs: 0, done: null,
            maxIdx: 0, total: 0, history: [] }
};
let curTool = 'query';          // 当前展示的工具数据源
let progExpanded = false;       // 折叠状态（默认收起：仅进度条）
function wsUrl() {
  return (location.protocol === 'https:' ? 'wss://' : 'ws://')
      + location.host + '/ws';
}
function setWsDot(cls) {
  document.querySelectorAll('[data-ws-dot]').forEach(d =>
    d.className = 'ws-dot ' + cls);
}
function connectProgress() {
  clearTimeout(wsRetry);
  try { ws = new WebSocket(wsUrl()); } catch (e) { scheduleWs(); return; }
  const gen = ++wsGen;
  ws.onopen = () => setWsDot('ok');
  ws.onclose = () => {
    setWsDot('off');
    if (gen === wsGen) scheduleWs();
  };
  ws.onmessage = (ev) => {
    try { renderProgressEvent(JSON.parse(ev.data)); } catch (e) {}
  };
}
function scheduleWs() {
  clearTimeout(wsRetry);
  wsRetry = setTimeout(connectProgress, 2000);
}
function panelFor(source) {
  if (source === 'query') return PANELS.query;
  if (source === 'export' || source === 'search') return PANELS.export;
  if (source === 'agents') return PANELS.agents;
  return null;   // 无来源事件不进入快捷工具面板
}
function renderProgressEvent(d) {
  const p = panelFor(d.source);
  if (!p) return;
  const key = (d.what || '') + '|' + (d.path || '');
  if (d.event === 'request_start') {
    p.done = null;   // 新操作开始（覆盖完成态）
    p.inFlight.set(key, { label: d.what || (d.method + ' ' + d.path), ts: d.ts });
  } else if (d.event === 'request_done') {
    p.inFlight.delete(key);
    pushHistory(p, d.what || (d.method + ' ' + d.path), d.elapsed_ms, d.bytes);
  } else if (d.event === 'stage') {
    p.done = null;
    p.stage = d; p.stageTs = d.ts;
    if (d.index === 1) p.maxIdx = 0;   // 新一轮批量开始，进度归零
    if (d.total) { p.total = d.total; p.maxIdx = Math.max(p.maxIdx, d.index || 0); }
    pushHistory(p, stageText(d), null, null);
  } else if (d.event === 'done') {
    // 完成：清空进行中状态（计时停止），显示完成提示与总耗时
    p.inFlight.clear();
    p.stage = null;
    p.done = { text: d.message, elapsed: d.elapsed_s };
    if (p.total) p.maxIdx = p.total;
    pushHistory(p, '✓ ' + (d.message || '完成'), null, null);
    flashTitle(d.message || '操作完成');
  }
  renderPanel();
}
function stageText(d) {
  let t = d.message || '';
  if (d.script_id) t += '（' + (d.script_name || d.script_id) + '）';
  if (d.index && d.total) t += ' ' + d.index + '/' + d.total;
  else if (d.group) t += ' @' + d.group;
  return t;
}
function pushHistory(p, text, ms, bytes) {
  const time = new Date().toTimeString().slice(0, 8);
  p.history.unshift({ time: time, text: text, ms: ms, bytes: bytes });
  p.history = p.history.slice(0, 30);
}
function fmtBytes(n) {
  if (n == null || n < 0) return '-';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / 1024 / 1024).toFixed(1) + ' MB';
}
function renderPanel() {
  // 共享面板（页面上方）渲染当前工具的数据源
  const p = PANELS[curTool];
  const head = document.getElementById('progress-tool');
  const cur = document.getElementById('pc-cur');
  const bar = document.getElementById('pb-cur');
  const list = document.getElementById('pl-cur');
  const toggle = document.getElementById('progress-toggle');
  const btn = document.getElementById('progress-expand-btn');
  if (!cur || !bar) return;
  if (head) head.textContent = p.label;
  if (toggle) toggle.classList.toggle('expanded', progExpanded);
  if (btn) btn.textContent = progExpanded ? '收起详情 ▴' : '展开详情 ▾';
  // 进度条：恒可见（不展开时的核心展示）
  const pct = p.total ? Math.min(100, Math.round(p.maxIdx / p.total * 100))
                      : (p.done ? 100 : 0);
  bar.style.width = pct + '%';
  bar.classList.toggle('full', pct >= 100);
  bar.textContent = p.total ? (p.done ? p.total + '/' + p.total
                                      : p.maxIdx + '/' + p.total + '（' + pct + '%）')
                            : (p.done ? '100%' : '');
  // 明细区：仅展开时显示（收起时只留进度条）
  cur.style.display = progExpanded ? '' : 'none';
  list.style.display = progExpanded ? '' : 'none';
  if (!progExpanded) return;
  const now = Date.now() / 1000;
  let html = '';
  if (p.inFlight.size) {
    for (const v of p.inFlight.values()) {
      const s = Math.max(0, now - v.ts);
      html += '正在执行：' + esc(v.label)
          + ' <span class="sub">已耗时 ' + s.toFixed(1) + ' s</span>';
    }
  }
  if (p.stage) {
    const s = Math.max(0, now - p.stageTs);
    html += (html ? '<br>' : '') + '当前进度：' + esc(stageText(p.stage))
        + ' <span class="sub">已进行 ' + s.toFixed(0) + ' s</span>';
  }
  if (p.done) {
    // 完成态：不计时（固定显示），避免完成后仍在累计
    html += (html ? '<br>' : '') + '✓ 已完成：' + esc(p.done.text)
        + (p.done.elapsed != null
           ? ' <span class="sub">总耗时 ' + p.done.elapsed + ' s</span>' : '');
  }
  cur.classList.toggle('done', !!p.done);
  cur.innerHTML = html || '（空闲）';
  list.innerHTML = p.history.slice(0, 12).map(r =>
      '<div class="row"><span class="t">' + r.time + '</span><span>'
      + esc(r.text) + '</span>'
      + (r.ms != null ? '<span class="ms">' + r.ms + ' ms'
          + (r.bytes >= 0 ? ' · ' + fmtBytes(r.bytes) : '') + '</span>' : '')
      + '</div>').join('');
}
function toggleProgress() {
  progExpanded = !progExpanded;
  renderPanel();
}
/* 完成提醒：标签页未聚焦时闪烁标题（用户回到本页即停止，最长 30s） */
function flashTitle(text) {
  if (document.hasFocus()) return;
  const orig = document.title;
  clearInterval(titleTimer);
  let on = false, n = 0;
  const stop = () => {
    clearInterval(titleTimer);
    document.title = orig;
    window.removeEventListener('focus', stop);
  };
  window.addEventListener('focus', stop);
  titleTimer = setInterval(() => {
    on = !on; n++;
    document.title = on ? '✓ ' + text : orig;
    if (n > 60) stop();   // ~30s 上限
  }, 500);
}
// 每 0.5s 刷新计时显示
setInterval(renderPanel, 500);

/* ---------- 快捷工具 ---------- */
function quickTool(name) {
  document.querySelectorAll('#quick-list a').forEach(a =>
    a.classList.toggle('active', a.dataset.tool === name));
  for (const t of ['query', 'search', 'agents']) {
    document.getElementById('quick-panel-' + t).style.display =
        t === name ? '' : 'none';
  }
  // 上方共享面板切换为当前工具的数据源
  curTool = (name === 'search') ? 'export' : name;
  renderPanel();
}

async function loadGroups() {
  try {
    const r = await fetch('/api/groups');
    const d = await r.json();
    if (d.error) return;
    for (const sel of ['q-group', 's-group']) {
      const el = document.getElementById(sel);
      const cur = el.value;
      el.innerHTML = '<option value="">（全部项目组）</option>' +
          d.groups.map(g =>
            '<option value="' + esc(g.name) + '">' + esc(g.name) + '</option>'
          ).join('');
      if (cur) el.value = cur;
    }
  } catch (e) { /* 静默：切到快捷工具页时再试 */ }
}

function busy(btnId, msgId, text) {
  const btn = document.getElementById(btnId);
  if (btn) {
    if (!btn.dataset.label) btn.dataset.label = btn.textContent;
    btn.disabled = true; btn.textContent = text || '处理中…';
  }
  const msg = document.getElementById(msgId);
  if (msg) msg.textContent = '';
}
function idle(btnId, msgId, ok, err) {
  const btn = document.getElementById(btnId);
  if (btn) { btn.disabled = false; btn.textContent = btn.dataset.label || '执行'; }
  const msg = document.getElementById(msgId);
  if (msg) { msg.textContent = err || (ok || ''); setTimeout(() => msg.textContent = '', 6000); }
}

async function quickQuery() {
  busy('q-btn', 'q-msg', '查询中…（剧本较多时需等待）');
  try {
    const group = document.getElementById('q-group').value;
    const concurrency = document.getElementById('q-concurrency').value;
    const r = await fetch('/api/quick/query_scripts', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({group: group, concurrency: concurrency})
    });
    const d = await r.json();
    if (d.error) { idle('q-btn', 'q-msg', '', '失败: ' + d.error); return; }
    renderQuickTable('q-result', d.headers, d.rows, '批量查询剧本信息');
    idle('q-btn', 'q-msg', '共 ' + d.total + ' 个剧本' +
        (d.errors ? '，' + d.errors + ' 个失败' : ''));
  } catch (e) { idle('q-btn', 'q-msg', '', '请求失败: ' + e); }
}

async function quickExport() {
  if (!confirm('将按所选范围重新导出一次剧本（导出到 result/ 新目录），确认执行？'))
    return;
  busy('s-exp-btn', 's-exp-msg', '导出中…');
  try {
    const group = document.getElementById('s-group').value;
    const concurrency = document.getElementById('s-concurrency').value;
    const r = await fetch('/api/quick/batch_export', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({group: group, concurrency: concurrency})
    });
    const d = await r.json();
    if (d.error) { idle('s-exp-btn', 's-exp-msg', '', '失败: ' + d.error); return; }
    idle('s-exp-btn', 's-exp-msg',
      '已导出 ' + d.exported + '/' + d.total + '，目录: ' + d.export_dir);
    await loadExportDirs(true);
  } catch (e) { idle('s-exp-btn', 's-exp-msg', '', '请求失败: ' + e); }
}

async function loadExportDirs(selectNew) {
  try {
    const r = await fetch('/api/quick/export_dirs');
    const d = await r.json();
    const el = document.getElementById('s-dir');
    el.innerHTML = (d.dirs || []).map(x =>
      '<option value="' + esc(x.name) + '">' + esc(x.name) +
      '（' + x.count + ' 个剧本）</option>').join('') ||
      '<option value="">（无导出目录，先执行第 1 步）</option>';
    if (selectNew && d.dirs && d.dirs.length) el.selectedIndex = 0;
  } catch (e) { /* 忽略 */ }
}

async function quickSearch() {
  const kw = document.getElementById('s-keyword').value.trim();
  if (!kw) { alert('请输入要搜索的关键字'); return; }
  busy('s-btn', 's-msg', '搜索中…');
  try {
    const r = await fetch('/api/quick/search', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        dir: document.getElementById('s-dir').value,
        keyword: kw,
        with_count: document.getElementById('s-count').checked
      })
    });
    const d = await r.json();
    if (d.error) { idle('s-btn', 's-msg', '', '失败: ' + d.error); return; }
    renderQuickTable('s-result', d.headers, d.rows, '批量搜索剧本内容');
    idle('s-btn', 's-msg', '共 ' + d.total + ' 个导出剧本，命中 ' + d.hits + ' 个');
  } catch (e) { idle('s-btn', 's-msg', '', '请求失败: ' + e); }
}

/* ---------- 批量下载搜索分析Agent ---------- */
async function downloadAgents() {
  if (!confirm('将重新获取一次所选范围分析Agent的系统提示词（下载到 result/ 新目录），确认执行？'))
    return;
  busy('a-dl-btn', 'a-dl-msg', '获取中…');
  try {
    const r = await fetch('/api/quick/download_agents', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        agent_type: document.getElementById('a-type').value,
        concurrency: document.getElementById('a-concurrency').value
      })
    });
    const d = await r.json();
    if (d.error) { idle('a-dl-btn', 'a-dl-msg', '', '失败: ' + d.error); return; }
    idle('a-dl-btn', 'a-dl-msg',
        '已下载 ' + d.downloaded + '/' + d.total + '，目录: ' + d.download_dir);
    await loadAgentsDirs(true);
  } catch (e) { idle('a-dl-btn', 'a-dl-msg', '', '请求失败: ' + e); }
}

async function loadAgentsDirs(selectNew) {
  try {
    const r = await fetch('/api/quick/agents_dirs');
    const d = await r.json();
    const el = document.getElementById('a-dir');
    el.innerHTML = (d.dirs || []).map(x =>
        '<option value="' + esc(x.name) + '">' + esc(x.name) +
        '（' + x.count + ' 个分析Agent）</option>').join('') ||
        '<option value="">（无下载目录，先执行第 1 步）</option>';
    if (selectNew && d.dirs && d.dirs.length) el.selectedIndex = 0;
  } catch (e) { /* 忽略 */ }
}

async function searchAgents() {
  const kw = document.getElementById('a-keyword').value.trim();
  if (!kw) { alert('请输入要搜索的关键字'); return; }
  busy('a-btn', 'a-msg', '搜索中…');
  try {
    const r = await fetch('/api/quick/search_agents', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        dir: document.getElementById('a-dir').value,
        keyword: kw,
        with_count: document.getElementById('a-count').checked
      })
    });
    const d = await r.json();
    if (d.error) { idle('a-btn', 'a-msg', '', '失败: ' + d.error); return; }
    renderQuickTable('a-result', d.headers, d.rows, '批量搜索分析Agent提示词');
    idle('a-btn', 'a-msg', '共 ' + d.total + ' 个分析Agent，命中 ' + d.hits + ' 个');
  } catch (e) { idle('a-btn', 'a-msg', '', '请求失败: ' + e); }
}

function renderQuickTable(containerId, headers, rows, title) {
  const box = document.getElementById(containerId);
  if (!rows || !rows.length) {
    box.innerHTML = '<div class="hint" style="margin-top:12px">（无结果）</div>';
    return;
  }
  const tid = containerId + '-table';
  let html = '<div class="table-ops">' +
    '<button class="copy" onclick="copyQuickTable(\\'' + tid + '\\')">复制</button>' +
    '<button class="copy" onclick="exportQuickTable(\\'' + containerId + '\\', \\'' +
      esc(title) + '\\')">导出Excel</button>' +
    '<span class="hint" style="align-self:center">复制为 \\t 分隔文本；导出为 .xlsx 文件</span></div>';
  html += '<table id="' + tid + '"><thead><tr>' +
      headers.map(h => '<th>' + esc(h) + '</th>').join('') +
      '</tr></thead><tbody>' +
      rows.map(row => '<tr>' +
          row.map(c => '<td>' + esc(c) + '</td>').join('') + '</tr>').join('') +
      '</tbody></table>';
  box.innerHTML = html;
  box._quickData = {headers: headers, rows: rows};
}

function copyQuickTable(tid) {
  const table = document.getElementById(tid);
  const box = table.parentElement;
  const data = box._quickData;
  if (!data) return;
  const text = data.headers.join('\\t') + '\\n' +
      data.rows.map(r => r.join('\\t')).join('\\n');
  (async () => {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; }
    catch (e) {
      try {
        const ta = document.createElement('textarea');
        ta.value = text; document.body.appendChild(ta); ta.select();
        ok = document.execCommand('copy'); document.body.removeChild(ta);
      } catch (e2) { ok = false; }
    }
    if (!ok) alert('复制失败，请手动选择表格内容复制');
  })();
}

async function exportQuickTable(containerId, title) {
  const box = document.getElementById(containerId);
  const data = box._quickData;
  if (!data) return;
  // 文件名带当前时间（prompt 2026-09-06：YYYYMMDD_HHMMSS，多次导出不重名）
  const now = new Date();
  const ts = now.getFullYear() +
      String(now.getMonth() + 1).padStart(2, '0') +
      String(now.getDate()).padStart(2, '0') + '_' +
      String(now.getHours()).padStart(2, '0') +
      String(now.getMinutes()).padStart(2, '0') +
      String(now.getSeconds()).padStart(2, '0');
  const fname = title + '_' + ts;
  try {
    const r = await fetch('/api/quick/export_xlsx', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({filename: fname, headers: data.headers, rows: data.rows})
    });
    if (!r.ok) { alert('导出失败: HTTP ' + r.status); return; }
    const blob = await r.blob();
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = fname + '.xlsx';
    document.body.appendChild(a); a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
  } catch (e) { alert('导出失败: ' + e); }
}

/* ---------- 启动 ---------- */
(async () => {
  // 先取配置：账号未配置时显示告警横幅并自动切到「配置参数」标签
  try {
    const r = await fetch('/api/config');
    const c = await r.json();
    if (!c.allowed_account) {
      document.getElementById('account-alert').style.display = 'block';
      switchTab('config');
    } else {
      switchTab('quick');
    }
  } catch (e) {
    switchTab('quick');
  }
})();
loadConfig();
loadTools();
loadGroups();
loadExportDirs();
loadAgentsDirs();
connectProgress();
</script>
</body>
</html>"""


def _ws_enqueue(queue: asyncio.Queue, data: dict) -> None:
    """事件放入连接队列（满则丢——仅状态展示，允许丢旧）。"""
    try:
        queue.put_nowait(data)
    except asyncio.QueueFull:
        pass


def build_web_routes() -> list[Route]:
    """Web 页面相关路由（挂到主应用）。"""

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
        return HTMLResponse(PAGE_HTML)

    async def get_cfg(request: Request) -> Response:
        cfg = global_config.load_config()
        port = global_config.get_server_port()
        return JSONResponse({
            "server_port": port,
            "allowed_account": cfg.get("allowed_account", ""),
            "sse_url": f"http://127.0.0.1:{port}/sse",
            "config_path": global_config.config_path(),
        })

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
        if not cfg:
            return JSONResponse({"error": "无有效配置项"}, status_code=400)
        saved = global_config.save_config(cfg)
        return JSONResponse({
            "server_port": saved["server_port"],
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
        return JSONResponse({"name": name, "content": path.read_text(
            encoding="utf-8")})

    return [
        Route("/", page),
        Route("/api/config", get_cfg, methods=["GET"]),
        Route("/api/config", post_cfg, methods=["POST"]),
        Route("/api/tools", get_tools, methods=["GET"]),
        Route("/api/md", get_md, methods=["GET"]),
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
                row["error"] = str(r[1])[:200]
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
    from .result import AGENTS_DIR_SUFFIX, RESULT_DIR
    progress.set_source("agents")
    start = _time.perf_counter()
    rows_out: list = []
    hits = 0
    try:
        target = RESULT_DIR / dir_name if dir_name else None
        if not target or not target.is_dir() or \
                not target.name.endswith(AGENTS_DIR_SUFFIX):
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
            return JSONResponse({"error": str(e)[:200]}, status_code=500)
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
                        row["error"] = str(r[1])[:200]
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
        from .result import EXPORT_DIR_SUFFIX, RESULT_DIR
        dirs = []
        if RESULT_DIR.is_dir():
            for d in sorted((d for d in RESULT_DIR.iterdir()
                             if d.is_dir()
                             and d.name.endswith(EXPORT_DIR_SUFFIX)),
                            reverse=True):
                count = sum(1 for p in d.iterdir()
                            if p.is_file() and p.suffix == ".json"
                            and p.name != "清单.json")
                dirs.append({"name": d.name, "count": count})
        return JSONResponse({"dirs": dirs})

    SEARCH_HEADERS = ["项目组", "剧本ID", "剧本名称", "剧本类型", "关键字",
                      "是否出现", "出现次数"]

    def _search(dir_name: str, keyword: str, with_count: bool) -> dict:
        import time as _time
        from .result import RESULT_DIR
        progress.set_source("search")
        start = _time.perf_counter()
        try:
            target = RESULT_DIR / dir_name if dir_name else None
            if not target or not target.is_dir():
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
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
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
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
            _log_quick_error(e)
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
        return JSONResponse(result)

    async def get_agents_dirs(request: Request) -> Response:
        from .result import AGENTS_DIR_SUFFIX, RESULT_DIR
        dirs = []
        if RESULT_DIR.is_dir():
            for d in sorted((d for d in RESULT_DIR.iterdir()
                             if d.is_dir()
                             and d.name.endswith(AGENTS_DIR_SUFFIX)),
                            reverse=True):
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
            return JSONResponse({"error": str(e)[:300]}, status_code=500)
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
            return JSONResponse({"error": str(e)[:200]}, status_code=500)
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
        Route("/api/quick/export_xlsx", post_export_xlsx, methods=["POST"]),
    ]


def build_app(sse_starlette_app, host: str) -> Starlette:
    """主应用：Web 页面路由 + MCP SSE 应用（同端口）。"""
    return Starlette(routes=[
        *build_web_routes(),
        Mount("/", app=sse_starlette_app),
    ])
