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
from starlette.routing import Mount, Route

from . import global_config

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
  <button data-tab="tools" class="active">MCP接口</button>
  <button data-tab="config">配置参数</button>
  <button data-tab="md-使用说明">使用说明</button>
  <button data-tab="md-适用场景">适用场景</button>
  <button data-tab="md-提示词示例">提示词示例</button>
</nav>

<!-- ============ MCP接口 ============ -->
<div class="tab active" id="tab-tools">
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
    const res = renderMarkdown(d.content || '');
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
function renderMarkdown(text) {
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
      const id = 'md-h-' + (++hSeq);
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
      switchTab('tools');
    }
  } catch (e) {
    switchTab('tools');
  }
})();
loadConfig();
loadTools();
</script>
</body>
</html>"""


def build_web_routes() -> list[Route]:
    """Web 页面相关路由（挂到主应用）。"""

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
    ]


def build_app(sse_starlette_app, host: str) -> Starlette:
    """主应用：Web 页面路由 + MCP SSE 应用（同端口）。"""
    return Starlette(routes=[
        *build_web_routes(),
        Mount("/", app=sse_starlette_app),
    ])
