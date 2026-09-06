#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""火山引擎智能外呼 MCP 服务（SSE，默认端口 19000，支持配置）。

把 17 个独立脚本功能全部封装为 MCP 工具（共 26 个），供 AI 调用：
- 使用说明：usage_guide（运行前提、各工具用法、典型调用序列）
- 账号：get_current_user（获取当前登录账号；所有工具内置账号守卫）
- 项目组/剧本：query_project_groups、list_group_scripts、query_script、
  search_script、export_script、import_script、publish_preview
- 剧本变量：query_variables、add_variable、update_variable、
  delete_variable、set_preview_variables
- 文本对话测试（模式二，任意轮数）：
  start_dialog / say_to_robot / is_dialog_active / end_dialog / list_dialogs
- 剧本基本信息/Sub Agent（2026-09-06 新增）：
  query_script_info、get_sub_agents、get_sub_agent_info
- 分析Agents（CloudLadder 域，2026-09-06 新增）：
  query_analysis_agents、get_analysis_agent
- 批量导出与内容搜索（2026-09-06 新增）：
  batch_export_scripts、search_exported_scripts

兼容 mcp SDK 1.x（FastMCP）与 2.x（MCPServer）两个大版本。

同端口服务（prompt 新要求）：
- HTTP 配置页 http://127.0.0.1:{port}/（监听端口与允许账号，写全局配置
  ~/.volcengine_aibot_scripts/global.json）；
- MCP SSE 端点 http://127.0.0.1:{port}/sse；
- 系统托盘（双击打开配置页，右键退出）；pythonw 运行无窗口。

账号守卫（prompt 约定）：所有业务请求前校验当前登录账号（/console/api/v2/user
的 id）是否为全局配置允许的账号；同一 Cookie 未变化时免重复检查；
未配置账号时报错提示先到配置页设置。

运行前提：
1) install.bat 安装依赖（requests mcp uvicorn sse-starlette websockets）；
2) chrome_capture_operate 服务运行中（默认 http://127.0.0.1:33445），
   日常 Chrome 已登录火山引擎控制台（Cookie 经插件推送，实时查询获取）。

启动：
    python mcp_server.py                    # SSE，127.0.0.1:19000
    python mcp_server.py --port 19000       # 指定端口
    python mcp_server.py --transport stdio  # 回退 stdio 模式

注册到 Claude Code（SSE）：
    claude mcp add --transport sse volc-aibot http://127.0.0.1:19000/sse

注意：对话会话状态存本服务进程内存，重启服务后旧 session_id 失效
（重新 start_dialog 即可）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot import global_config                     # noqa: E402
from volc_aibot.client import (VolcAIBotClient,            # noqa: E402
                                  agent_mode_name)
from volc_aibot.config import VARIABLE_TYPES              # noqa: E402
from volc_aibot.logging_util import setup_logging         # noqa: E402
from volc_aibot.result import new_result_dir, safe_filename, \
    write_dialog_md, write_dialog_round_md, write_variables_md, \
    write_json                                          # noqa: E402
from volc_aibot.web_server import build_app, set_mcp_instance  # noqa: E402

# mcp SDK 1.x 与 2.x 兼容：
# - 1.x: from mcp.server.fastmcp import FastMCP（settings 配置 host/port）
# - 2.x: FastMCP 改名为 MCPServer（run(transport="sse", host=..., port=...)）
try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _McpServer
    MCP_SDK_V2 = True
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _McpServer
    MCP_SDK_V2 = False

mcp = _McpServer("volc-aibot")

_client: VolcAIBotClient | None = None


def _get_client() -> VolcAIBotClient:
    global _client
    if _client is None:
        _client = VolcAIBotClient()
    return _client


# ---------------------------------------------------------------- 工具异常详情透出

# 背景：mcp SDK 对工具函数抛出的非 ToolError 异常只回
# "Error executing tool <name>"（原始 message 留在服务端），客户端排障困难。
# 解法：装饰器把我们的业务异常（ApiError/NotConfigured/AccountNotAllowed/
# RuntimeError 等）转为 ToolError —— SDK 会把 ToolError 的完整 message
# 透出给客户端（含守卫拦截原因、接口报错详情等）。

try:  # mcp 2.x
    from mcp.server.mcpserver.exceptions import ToolError as _ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp.exceptions import ToolError as _ToolError  # type: ignore


def tool_with_detail(fn):
    """包装 MCP 工具函数：业务异常的完整信息透出给客户端。

    与 @mcp.tool() 叠加使用（本装饰器在内层）：
        @mcp.tool()
        @tool_with_detail
        def my_tool(...): ...
    """
    import functools
    import inspect as _inspect

    if _inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except _ToolError:
                raise
            except Exception as e:  # noqa: BLE001 - 统一转 ToolError 透出
                _log_tool_error(fn, e)
                raise _ToolError(f"{e}") from e
        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _ToolError:
            raise
        except Exception as e:  # noqa: BLE001 - 统一转 ToolError 透出
            _log_tool_error(fn, e)
            raise _ToolError(f"{e}") from e
    return wrapper


def _log_tool_error(fn, e: Exception) -> None:
    try:
        # 完整堆栈（prompt 2026-09-06：日志记录详细异常堆栈）
        _client.logger.exception("工具 %s 执行失败: %s", fn.__name__, e)
    except Exception:  # noqa: BLE001 - 日志失败不影响透出
        pass


# ---------------------------------------------------------------- 使用说明工具

_USAGE_GUIDE = {
"quickstart": (
"""## 快速开始

**运行前提**
1. chrome_capture_operate 服务运行中（默认 http://127.0.0.1:33445），
   日常 Chrome 已登录火山引擎智能外呼控制台（登录态 Cookie 实时查询，脚本不保存）；
2. 剧本ID 为 llm_ 开头的字符串（如 llm_xxx），不确定时先用
   search_script 搜索。

**工具调用约定**
- 涉及剧本的工具都用剧本ID（AgentID，llm_xxx）定位，内部自动解析数字坐标；
- 写操作（变量修改/赋值/发布/导入）执行后自动回查验证并写入项目 result/ 目录；
- 常见失败：Cookie 失效（Chrome 重新登录火山引擎控制台）；
  剧本未发布就 start_dialog（先 publish_preview）。

**最常用流程：与机器人对话（模式二，任意轮数）**
1. start_dialog(script_id) → 返回 session_id + 机器人开场白
2. say_to_robot(session_id, text) 循环多轮（每轮检查 session_completed，
   true 表示机器人已挂机，停止发言）
3. end_dialog(session_id) → 意向评级 + 对话摘要（结果写入 result 目录）

其他主题：usage_guide("scripts") / ("variables") / ("import_export")
/ ("publish") / ("agents_info") / ("faq")，或 "all" 查看全部。"""
),
"scripts": (
"""## 剧本查询/搜索/导出/导入

- query_project_groups()：全部项目组（含组ID）。注意“项目组”是权限分组
  （如 电销项目组_测试），不是业务项目。
- list_group_scripts(group_name)：项目组下的全部剧本（名称精确匹配项目组）。
- query_script(script_id)：按剧本ID查详情（名称/ServiceID/GroupID/版本状态）。
- search_script(keyword)：按名称或ID模糊搜索（不确定剧本ID时用）。
- export_script(script_id)：导出剧本 JSON 到 result 目录，返回文件路径。
  ⚠ 导出文件含服务端 checksum，导入必须使用原样文件，手工修改会被校验拒绝。
- import_script(file_path, group_name)：导入剧本到项目组 → 返回新剧本ID
  （new_agent_id，llm_xxx）。file_path 用 export_script 返回的路径；
  group_name 精确匹配（先 query_project_groups 查可用名称）。
  ⚠ 新剧本未发布（版本0），对话测试前必须 publish_preview。

复制剧本完整链路：
export_script → import_script(导出文件, 项目组名) → publish_preview(新剧本ID)
→ start_dialog(新剧本ID) ..."""
),
"variables": (
"""## 剧本变量（两套体系，勿混淆）

**1. 变量定义（增删改）：query_variables / add_variable / update_variable / delete_variable**
- 先 query_variables(script_id) 查现有变量（修改/删除需要变量 id 与全量列表）；
- add_variable(script_id, name, key, variable_type, is_required)：
  key 只支持英文与下划线（如 test_vvv）；variable_type 1=String 2=Integer
  3=Float 4=Boolean；注意该控制台接口无“变量描述”字段；
- update_variable(script_id, key, ...)：按调用名称定位，改 name/类型/必填
  （至少一项）；
- delete_variable(script_id, key)：按调用名称定位（服务端按变量数字ID删除）；
- ⚠ 修改变量定义后需 publish_preview 发布测试版本才能生效。

**2. 测试版本变量赋值：set_preview_variables**
- set_preview_variables(script_id, values)，values 如
  {"coupon_a_lock_term": 3}（值统一按字符串提交）；
- ⚠ 必填变量（is_required=true）赋值时值不能为空，传空值会被拒绝；
- 修改的是测试版本（Preview）变量取值，不影响线上；
- start_dialog 默认使用测试版本全局变量作为对话变量；overrides 为会话级变量，会覆盖同名变量（仅本段会话生效，不改测试版本全局变量），未指定时使用测试版本全局变量。

修改后验证话术链路：
query_variables → update_variable → publish_preview → start_dialog ...
或：set_preview_variables → start_dialog ..."""
),
"import_export": (
"""## 导入/导出剧本

- export_script(script_id)：导出剧本 JSON（写入 result/{时间_账号_导出剧本}/），
  返回 export_file 路径、文件名、大小；
- import_script(file_path, group_name)：
  - file_path：export_script 返回的 export_file（原样文件，含服务端 checksum，
    手工修改内容会被导入校验拒绝）；
  - group_name：目标项目组名称，精确匹配（先用 query_project_groups 查询）；
  - 返回 new_agent_id（新剧本ID）、new_script_name（原名+导入时间戳后缀）、
    new_service_id；
- ⚠ 新导入剧本未发布（版本0），对话测试前必须先 publish_preview。

完整链路：export_script → import_script → publish_preview(新剧本ID)
→ start_dialog(新剧本ID)。"""
),
"publish": (
"""## 发布剧本测试版本

publish_preview(script_id, description?)

- 提交发布并自动轮询到完成（默认最多 600 秒，无需人工等待干预）；
- 成功判据：release-launch 的 train_info.version 相比提交前 +1 且
  status=FINISHED（返回 before_version / new_version）；
- 发布的是服务端已保存的剧本草稿；本 MCP 的变量修改（add/update/delete_variable）
  直接提交到服务端，发布后生效；
- 哪些场景需要发布：
  - 新导入的剧本（版本0）→ 对话测试前必须发布；
  - 修改变量定义后 → 需发布才生效；
  - 测试版本变量赋值（set_preview_variables）→ 文本对话测试直接生效，
    实际外呼是否需发布未验证。

前置检查：start_dialog 会自动校验已发布，未发布会报错提示先发布。"""
),
"dialog": (
"""## 文本对话测试（模式二：任意轮数，AI 扮演客户）

**⚠ 资源提示**：对话测试（talk）占用生产实际外呼资源——请控制并发
（不要同时发起大量会话，逐会话逐轮即可），避免业务高峰，建议晚上测试。

**前置**：剧本已发布测试版本（start_dialog 自动校验，未发布报错）。

调用序列：
1. start_dialog(script_id, overrides?) →
   新建会话 + 机器人开场白；返回 session_id、robot_texts、variables
   （overrides 为会话级变量，覆盖剧本测试版本全局变量的同名变量，
   仅本段会话生效；未指定时使用剧本测试版本全局变量）
2. say_to_robot(session_id, text) → 客户说一句，返回机器人回复 robot_texts、
   当前节点 node、session_completed；
   - session_completed=true：机器人已挂机，勿再发言，直接 end_dialog；
   - 对话轮数上限由剧本配置（如 50 轮），超限行为未验证；
3. is_dialog_active(session_id)：判断会话是否仍在进行；
4. end_dialog(session_id) → 结束测试：对话分析（LeadsGrading 意向评级 +
   DialogSummary 摘要），transcript 与分析结果写入 result 目录；
   ⚠ 分析接口可能返回空结果（available=false，服务端疑点），
   此时对话全文已保存供人工评估；
5. list_dialogs()：查看服务内全部会话状态。

注意：会话状态存 MCP 服务进程内存，服务重启后旧 session_id 失效，
重新 start_dialog 即可。机器人挂机那轮的结束语不计入分析（与页面一致）。"""
),
"faq": (
"""## 常见问题

- Cookie 服务不可达 / 获取 Cookie 失败：chrome_capture_operate 未启动，
  或插件未推送（确认插件已装、推送范围允许 volcengine.com、
  Chrome 已登录火山引擎控制台）；
- 接口 HTTP 401/403：Chrome 里重新登录火山引擎控制台（服务会自动重取
  Cookie 重试一次）；
- “剧本ID xxx 无精确匹配”：剧本ID 拼写错误，用 search_script 搜索；
- start_dialog 报“测试版本未发布”：先 publish_preview(script_id)；
- “会话不存在”：MCP 服务重启过（会话存内存），重新 start_dialog；
- end_dialog 结果 available=false：对话分析接口返回空（2026-09-03 抓包同
  现象，服务端疑点），对话全文已保存至 result 目录；
- import_script 被拒：必须用 export_script 导出的原样文件（含 checksum）；
- “变量 xxx 不存在”：调用名称（key）拼写问题，先 query_variables。"""
),
"agents_info": (
"""## 剧本基本信息 / Sub Agent / 分析Agents

**剧本基本信息（query_script_info）**
- query_script_info(script_id)：一次返回 prompt 所需全字段——项目组/
  剧本名称/剧本类型（1=纯PE型 2=Multi Agents 3=对话流程编排）/
  最大对话轮次/最大模型出错次数/挂机关键词（;分隔）/LLM 模型/
  ASR 引用热词表/ASR 上传上下文（开启/未开启）/挂载的分析Agent
  （信息抽取/线索定级/通话总结 各 ID/名称/状态/更新时间）/
  测试版本与线上版本发布（版本号/状态/更新时间）；
- 结果写入 result/{时间_账号_查询剧本基本信息}/。

**Sub Agent（Multi Agents 剧本）**
- get_sub_agents(script_id)：Sub Agent 清单（ID/名称/顺序）；
- get_sub_agent_info(script_id, sub_agent_id)：单个 Sub Agent 的
  LLM 模型与提示词配置（提示词全文落盘 result 目录，返回摘要+文件路径，
  提示词可达 60KB，不要整段读入对话）。

**分析Agents（独立于剧本，CloudLadder 域）**
- query_analysis_agents(type?)：分析Agent 列表（名称/ID/状态 已发布/
  未发布/更新时间）；type 为 通话总结/信息抽取/线索定级，可省略查全部；
- get_analysis_agent(agent_id)：详情——系统提示词/用户提示词/状态/
  更新时间/模型参数（提示词全文落盘 result 目录，返回摘要+文件路径）。

**批量导出与关键字搜索**
- batch_export_scripts(group_names?)：按项目组（多个或全部）批量导出
  剧本，目录名含当前火山账号（区分环境）且以「_批量导出剧本」结尾，
  内含 清单.json（项目组/剧本ID/剧本名称，亦记录账号）；
- search_exported_scripts(export_dir?, keyword, with_count?)：在导出
  目录中按行搜索关键字，返回 项目组/剧本ID/剧本名称/是否出现/出现次数
  （with_count=false 时次数为空）。export_dir 省略时取最新导出目录。
  典型用法：先 batch_export_scripts 导出，再 search_exported_scripts
  检查全部剧本内容是否含某关键字。"""
),
}


@mcp.tool()
@tool_with_detail
def usage_guide(topic: str = "all") -> dict:
    """使用说明：本 MCP 服务的运行前提、工具用法与典型调用序列（不发起任何网络请求）。

    - topic 可选值：
      all（全部）/ quickstart（快速开始）/ scripts（剧本查询/导出/导入）
      / variables（变量增删改与赋值）/ import_export（导入导出）
      / publish（发布测试版本）/ dialog（文本对话测试）/ faq（常见问题）
    不确定怎么用时，先调用本工具。
    """
    topic = (topic or "all").strip().lower()
    valid = set(_USAGE_GUIDE) | {"all"}
    if topic not in valid:
        return {
            "error": f"未知 topic: {topic}",
            "available_topics": sorted(valid),
        }
    if topic == "all":
        content = "\n\n".join(
            f"# {name}\n{text}" for name, text in _USAGE_GUIDE.items())
    else:
        content = _USAGE_GUIDE[topic]
    return {"topic": topic, "content": content}


# ---------------------------------------------------------------- 对话状态（模式二：任意轮数）

class DialogState:
    """一段文本测试对话的内存状态。"""

    def __init__(self, script_id: str, coords: dict, context_str: str,
                 variables: dict):
        self.session_id = str(uuid.uuid4())
        self.script_id = script_id
        self.coords = coords
        self.context_str = context_str
        self.variables = variables
        self.round_index = 0          # 已完成的 talk 轮次（0 = 仅开场白）
        self.items: list[dict] = []   # {"Speaker":1|2, "Content":..., "hangup":bool}
        self.completed = False        # 机器人是否已挂机（session_completed）
        self.ended = False            # 是否已跑过结束分析
        self.analysis: dict | None = None
        self.last_node = ""
        self.result_dir: Path | None = None   # start_dialog 时创建
        self.lock = threading.Lock()

    def write_round_md(self) -> Path:
        """每轮对话结束后单独生成一个 md（round_XXX.md）。

        结构（prompt 约定）：头部 + “# 变量”段（名称\t值）+ “# 对话内容”段
        （截至本轮，每行 机器人:xxx / 客户:xxx）。进程中断也已保留已完成轮次。
        返回该轮 md 路径；同时更新汇总 transcript.md。
        """
        assert self.result_dir is not None
        rounds_dir = self.result_dir / "rounds"
        index = self.round_index + 1   # 轮次从 1 计（开场白为第 1 轮）
        header = [
            f"# 文本对话测试 第 {index} 轮",
            f"- 剧本: {self.script_id}（{self.coords.get('agent_name')}）",
            f"- 会话ID: {self.session_id}",
        ]
        var_pairs = sorted((self.variables or {}).items())
        write_dialog_round_md(rounds_dir / f"round_{index:03d}.md",
                              header, var_pairs, self.items)
        # 汇总文件（兼容旧引用）
        return write_dialog_md(rounds_dir / "transcript.md",
                               [f"# 文本对话测试（已对话 {index} 轮）",
                                f"- 剧本: {self.script_id}"
                                f"（{self.coords.get('agent_name')}）",
                                f"- 会话ID: {self.session_id}"],
                               self.items)


_dialogs: dict[str, DialogState] = {}
_dialogs_lock = threading.Lock()


def _get_dialog(session_id: str) -> DialogState:
    with _dialogs_lock:
        st = _dialogs.get(session_id)
    if st is None:
        raise RuntimeError(f"会话不存在: {session_id}（可用 list_dialogs 查看现有会话；"
                           "若服务重启过，请重新 start_dialog）")
    return st


# ---------------------------------------------------------------- 项目组 / 剧本

@mcp.tool()
@tool_with_detail
def query_project_groups() -> dict:
    """查询火山引擎智能外呼的全部项目组（含组ID与父子层级）。

    返回 groups: [{id, group_name, parent_group_id}]（树形结构用 parent_group_id 组装，
    根为 0）。后续 import_script 需要项目组名称。
    """
    groups = _get_client().query_project_groups()
    return {
        "total": len(groups),
        "groups": [{"id": g.get("id"), "group_name": g.get("group_name"),
                    "parent_group_id": g.get("parent_group_id")}
                   for g in groups],
    }


@mcp.tool()
@tool_with_detail
def list_group_scripts(group_name: str) -> dict:
    """查询指定项目组下的全部剧本。

    - group_name: 项目组名称（精确匹配，如 脚本测试项目组；可先用
      query_project_groups 查询可用名称）
    返回 scripts: [{script_id(AgentID), agent_name, service, preview_version,
    online_version}, ...]。结果同时写入 result 目录。
    """
    client = _get_client()
    group = client.find_group(group_name)
    group_id = group["id"]
    collected: list[dict] = []
    page = 1
    while True:
        agents, total = client.list_agents(group_id=group_id, page=page,
                                           page_size=100)
        if not agents:
            break
        collected.extend(agents)
        if len(collected) >= total:
            break
        page += 1
    out_dir = new_result_dir("查询项目组下的剧本")
    write_json(out_dir / "scripts.json", {
        "group_name": group_name, "group_id": group_id,
        "total": len(collected), "scripts": collected})
    return {
        "group_name": group_name,
        "group_id": group_id,
        "total": len(collected),
        "scripts": [{"script_id": a.get("AgentID"),
                     "agent_name": a.get("AgentName", ""),
                     "service": a.get("ServiceID"),
                     "preview_version": a.get("PreviewVersion"),
                     "online_version": a.get("OnlineVersion")}
                    for a in collected],
        "result_file": str(out_dir / "scripts.json"),
    }


@mcp.tool()
@tool_with_detail
def query_script(script_id: str) -> dict:
    """根据剧本ID查询剧本详情。

    - script_id: 剧本ID（llm_ 开头，如 llm_xxx，即控制台剧本列表的 AgentID）
    返回剧本详情：名称、ServiceID（数字剧本标识）、GroupID、ProjectID、
    测试版本/线上版本号与状态等。
    """
    return _get_client().query_script(script_id)


@mcp.tool()
@tool_with_detail
def search_script(keyword: str) -> dict:
    """按关键字搜索剧本（模糊匹配剧本名称与剧本ID，自动翻页）。

    - keyword: 剧本名称片段或剧本ID
    返回 scripts: [{script_id(AgentID), agent_name, service, group, ...}]，
    不确定剧本ID时可先用本工具。
    """
    agents = _get_client().search_scripts(keyword)
    return {
        "keyword": keyword,
        "total": len(agents),
        "scripts": [{"script_id": a.get("AgentID"),
                     "agent_name": a.get("AgentName", ""),
                     "service": a.get("ServiceID"),
                     "group": a.get("GroupID"),
                     "preview_version": a.get("PreviewVersion"),
                     "online_version": a.get("OnlineVersion")}
                    for a in agents],
    }


@mcp.tool()
@tool_with_detail
def export_script(script_id: str) -> dict:
    """导出剧本为 JSON 文件（写入 result 目录）。

    - script_id: 剧本ID（llm_xxx）
    返回导出文件路径、文件名、大小。注意导出文件含服务端 checksum，
    导入时须使用原样文件，勿手工修改内容。
    """
    client = _get_client()
    coords = client.resolve_script(script_id)
    filename, content = client.export_script(script_id)
    out_dir = new_result_dir("导出剧本")
    path = out_dir / safe_filename(filename)
    path.write_bytes(content)
    return {
        "script_id": script_id,
        "agent_name": coords.get("agent_name"),
        "export_file": str(path),
        "filename": filename,
        "size_bytes": len(content),
        "hint": "导入请使用该原样文件（import_script）",
    }


@mcp.tool()
@tool_with_detail
def import_script(file_path: str, group_name: str) -> dict:
    """导入剧本文件到指定项目组，生成新剧本（须为 export_script 导出的原样文件）。

    - file_path: 导出的剧本 JSON 文件路径（export_script 返回的 export_file）
    - group_name: 目标项目组名称（精确匹配，如 电销项目组_测试；
      可先用 query_project_groups 查询可用名称）
    返回新剧本信息：new_agent_id（新剧本ID，llm_xxx）、new_script_name、
    new_service_id 等。新剧本未发布（版本0），对话测试前需 publish_preview。
    """
    client = _get_client()
    result = client.import_script(file_path, group_name=group_name)
    out_dir = new_result_dir("导入剧本")
    write_json(out_dir / "import.json", result)
    return {
        "new_agent_id": result.get("new_agent_id"),
        "new_script_name": result.get("new_script_name"),
        "new_service_id": result.get("new_service_id"),
        "group_name": result.get("group_name"),
        "group_id": result.get("group_id"),
        "result_file": str(out_dir / "import.json"),
        "hint": "新剧本未发布，对话测试前请先 publish_preview",
    }


@mcp.tool()
@tool_with_detail
def publish_preview(script_id: str, description: str = "") -> dict:
    """发布剧本测试版本并轮询至完成（变量修改后需发布才生效；对话测试前置）。

    - script_id: 剧本ID（llm_xxx）
    - description: 发布描述（缺省自动生成）
    成功判据：release-launch 的 train_info.version 相比提交前 +1 且
    status=FINISHED。返回新旧版本号与状态。
    """
    client = _get_client()
    result = client.publish_preview(script_id, description or "发布测试版本")
    out_dir = new_result_dir("发布剧本测试版本")
    write_json(out_dir / "publish.json", result)
    return {
        "script_id": script_id,
        "before_version": result["before_version"],
        "new_version": result["new_version"],
        "status": result["status"],
        "update_time": result.get("update_time"),
        "result_file": str(out_dir / "publish.json"),
    }


# ---------------------------------------------------------------- 剧本变量

@mcp.tool()
@tool_with_detail
def query_variables(script_id: str) -> dict:
    """查询剧本变量定义列表（名称/调用名称/类型/是否必填）。

    - script_id: 剧本ID（llm_xxx）
    返回 variables: [{id, name(名称), key(调用名称), IsRequired, VariableType}]，
    VariableType: 1=String 2=Integer 3=Float 4=Boolean。
    修改变量前必须先调用本工具（需要变量 id 与全量列表）。
    """
    variables = _get_client().query_variables(script_id)
    return {"script_id": script_id, "total": len(variables),
            "variables": variables}


def _do_modify(script_id: str, add=None, update=None, delete_ids=None) -> dict:
    client = _get_client()
    result = client.modify_variables(script_id, add=add, update=update,
                                     delete_ids=delete_ids)
    out_dir = new_result_dir("剧本变量修改")
    columns = [("name", "名称"), ("key", "调用名称"), ("IsRequired", "是否必填"),
               ("VariableType", "变量类型数值"), ("VariableTypeDesc", "变量类型")]

    def _desc(items):
        out = []
        for v in items or []:
            item = dict(v)
            t = item.get("VariableType")
            item["VariableTypeDesc"] = VARIABLE_TYPES.get(t, str(t or ""))
            out.append(item)
        return out

    write_variables_md(out_dir / "before.md", "修改前变量",
                       _desc(result["before"]), columns)
    write_variables_md(out_dir / "after.md", "修改后变量",
                       _desc(result["after"]), columns)
    write_json(out_dir / "before.json", {
        "script_id": script_id, "variables": result["before"]})
    write_json(out_dir / "after.json", {
        "script_id": script_id, "variables": result["after"]})
    return {
        "before_file": str(out_dir / "before.md"),
        "after_file": str(out_dir / "after.md"),
        "before_total": len(result["before"]),
        "after_total": len(result["after"]),
        "hint": "变量修改需发布测试版本（publish_preview）才能生效",
    }


@mcp.tool()
@tool_with_detail
def add_variable(script_id: str, name: str, key: str,
                 variable_type: int, is_required: bool = False) -> dict:
    """新增剧本变量。

    - script_id: 剧本ID（llm_xxx）
    - name: 变量名称（如 测试变量）
    - key: 调用名称，只支持英文与下划线（如 test_vvv）
    - variable_type: 变量类型 1=String 2=Integer 3=Float 4=Boolean
    - is_required: 是否必填（默认否）
    注意：控制台该接口无“变量描述”字段（抓包验证）。
    """
    if variable_type not in VARIABLE_TYPES:
        raise RuntimeError(f"variable_type 必须是 1/2/3/4（{VARIABLE_TYPES}）")
    return _do_modify(script_id, add=[{
        "name": name, "key": key,
        "IsRequired": bool(is_required), "VariableType": variable_type}])


@mcp.tool()
@tool_with_detail
def update_variable(script_id: str, key: str, name: str | None = None,
                    variable_type: int | None = None,
                    is_required: bool | None = None) -> dict:
    """修改已存在的剧本变量（按调用名称定位，至少改一项）。

    - script_id: 剧本ID（llm_xxx）
    - key: 变量调用名称（必须已存在，先 query_variables 确认）
    - name / variable_type / is_required: 需要修改的字段（可选，至少一个）
    """
    client = _get_client()
    variables = client.query_variables(script_id)
    target = next((v for v in variables if v.get("key") == key), None)
    if target is None:
        raise RuntimeError(f"变量 {key} 不存在（先 query_variables 确认调用名称）")
    item = {"id": target["id"], "name": target.get("name"), "key": key,
            "IsRequired": target.get("IsRequired"),
            "VariableType": target.get("VariableType")}
    if name is not None:
        item["name"] = name
    if variable_type is not None:
        item["VariableType"] = variable_type
    if is_required is not None:
        item["IsRequired"] = is_required
    return _do_modify(script_id, update=[item])


@mcp.tool()
@tool_with_detail
def delete_variable(script_id: str, key: str) -> dict:
    """删除剧本变量（按调用名称定位；删除用服务端变量数字ID）。

    - script_id: 剧本ID（llm_xxx）
    - key: 变量调用名称（必须已存在）
    """
    client = _get_client()
    variables = client.query_variables(script_id)
    target = next((v for v in variables if v.get("key") == key), None)
    if target is None:
        raise RuntimeError(f"变量 {key} 不存在（先 query_variables 确认调用名称）")
    return _do_modify(script_id, delete_ids=[target["id"]])


@mcp.tool()
@tool_with_detail
def set_preview_variables(script_id: str, values: dict) -> dict:
    """测试版本全局变量赋值（修改变量取值，全量提交仅改目标项）。

    - script_id: 剧本ID（llm_xxx）
    - values: {调用名称: 新值}，值统一按字符串提交（如 {"coupon_a_lock_term": 3}）
    ⚠ 必填变量（is_required=true）赋值时值不能为空——传空值会被拒绝。
    修改前/后值写入 result 目录（before.md/after.md，\t 分隔，
    含变量类型数值与字符串形式）。
    文本对话测试（start_dialog）默认使用这些值。
    """
    result = _get_client().set_preview_variables(script_id, values)
    out_dir = new_result_dir("测试版本全局变量赋值")
    columns = [("name", "名称"), ("key", "调用名称"), ("value", "值"),
               ("is_required", "是否必填"), ("variable_type", "变量类型数值"),
               ("variable_type_desc", "变量类型")]

    def _desc(items):
        out = []
        for v in items or []:
            item = dict(v)
            item["is_required"] = "是" if item.get("is_required") else "否"
            t = item.get("variable_type")
            item["variable_type_desc"] = VARIABLE_TYPES.get(t, str(t or ""))
            out.append(item)
        return out

    write_variables_md(out_dir / "before.md", "修改前测试版本变量",
                       _desc(result["before"]), columns)
    write_variables_md(out_dir / "after.md", "修改后测试版本变量",
                       _desc(result["after"]), columns)
    write_json(out_dir / "before.json", {
        "script_id": script_id, "preview_variables": result["before"]})
    write_json(out_dir / "after.json", {
        "script_id": script_id, "preview_variables": result["after"]})
    return {
        "values": result["values"],
        "verify_failed": result["verify_failed"],
        "before_file": str(out_dir / "before.md"),
        "after_file": str(out_dir / "after.md"),
    }


# ---------------------------------------------------------------- 文本对话测试（模式二）

@mcp.tool()
@tool_with_detail
def start_dialog(script_id: str, overrides: dict | None = None) -> dict:
    """开始一段文本测试对话（新会话，返回机器人开场白）。前置：剧本已发布测试版本。

    ⚠ 占用生产实际外呼资源：请勿同时发起大量会话，建议晚上测试。

    - script_id: 剧本ID（llm_xxx）
    - overrides: 可选，会话级对话变量，如 {"coupon_a_lock_term": 3}；
      仅在本段会话首次请求（start_dialog）时生效且整段会话不变，会覆盖剧本测试版本
      全局变量的同名变量（不改测试版本全局变量）；未指定时使用剧本测试版本全局变量的当前值
    返回 session_id（后续 say_to_robot / end_dialog 使用）、robot_texts（开场白）。
    剧本未发布会报错，请先 publish_preview。
    """
    client = _get_client()
    coords = client.resolve_script(script_id)
    check = client.check_preview_published(coords)
    if not check["published"]:
        ti = check.get("train_info") or {}
        raise RuntimeError(
            f"剧本 {script_id} 测试版本未发布完成（version={ti.get('version')} "
            f"status={ti.get('status')}），请先调用 publish_preview")
    variables = client.fetch_preview_variable_values(coords=coords)
    if overrides:
        variables.update({str(k): str(v) for k, v in overrides.items()})
    context_str = json.dumps(variables, ensure_ascii=False, sort_keys=True)

    st = DialogState(script_id, coords, context_str, dict(variables))
    st.result_dir = new_result_dir("文本对话测试")
    data = client.talk(coords, context_str, "", st.session_id, 0)
    texts, node, completed = VolcAIBotClient.extract_reply(data)
    st.round_index = 0
    st.completed = completed
    st.last_node = node
    if texts:
        st.items.append({"Speaker": 1, "Content": "\n".join(texts),
                         "hangup": completed})
    md_path = st.write_round_md()   # 每轮实时写入（开场白轮）
    with _dialogs_lock:
        _dialogs[st.session_id] = st
    return {
        "session_id": st.session_id,
        "script_id": script_id,
        "agent_name": coords.get("agent_name"),
        "variables": variables,
        "robot_texts": texts,
        "node": node,
        "session_completed": completed,
        "transcript_md": str(md_path),
        "hint": "用 say_to_robot(session_id, text) 继续对话；"
                "结束后用 end_dialog(session_id) 做对话分析",
    }


@mcp.tool()
@tool_with_detail
def say_to_robot(session_id: str, text: str) -> dict:
    """向机器人说一句话（作为客户），返回机器人回复。

    - session_id: start_dialog 返回的会话ID
    - text: 本轮客户说的话
    返回 robot_texts（机器人回复）、session_completed（true 表示机器人
    已主动结束对话/挂机，之后请勿再调用本工具，可直接 end_dialog）。
    """
    st = _get_dialog(session_id)
    with st.lock:
        if st.completed:
            raise RuntimeError("该对话已被机器人结束（挂机），不能再说话；"
                               "请调用 end_dialog 做对话分析，或 start_dialog 开新会话")
        st.items.append({"Speaker": 2, "Content": text, "hangup": False})
        round_index = st.round_index + 1
        data = _get_client().talk(st.coords, st.context_str, text,
                                  st.session_id, round_index)
        texts, node, completed = VolcAIBotClient.extract_reply(data)
        st.round_index = round_index
        st.completed = completed
        st.last_node = node
        if texts:
            st.items.append({"Speaker": 1, "Content": "\n".join(texts),
                             "hangup": completed})
        st.write_round_md()   # 每轮结束后生成该轮 md
        return {
            "session_id": session_id,
            "round_index": round_index,
            "customer_text": text,
            "robot_texts": texts,
            "node": node,
            "session_completed": completed,
        }


@mcp.tool()
@tool_with_detail
def is_dialog_active(session_id: str) -> dict:
    """判断对话是否还在进行中（active=false 表示机器人已挂机或已结束分析）。"""
    st = _get_dialog(session_id)
    active = not st.completed and not st.ended
    return {
        "session_id": session_id,
        "script_id": st.script_id,
        "active": active,
        "session_completed": st.completed,
        "ended": st.ended,
        "round_index": st.round_index,
        "last_node": st.last_node,
        "reason": ("进行中" if active else
                   "机器人已主动结束对话（挂机）" if st.completed else "已结束测试"),
    }


@mcp.tool()
@tool_with_detail
def end_dialog(session_id: str) -> dict:
    """结束测试：对整段对话做分析（意向评级 + 对话摘要），并写入 result 目录。

    - session_id: start_dialog 返回的会话ID
    返回 LeadsGrading（意向评级）与 DialogSummary（对话摘要）。
    注意1：机器人挂机那轮的结束语不计入分析（与页面行为一致）；
    注意2：对话记录在每轮对话后已实时写入 rounds/round_XXX.md（每轮单文件，每行
    机器人:xxx / 客户:xxx），与对话进度同步；
    注意3：分析接口可能返回空结果（2026-09-03 抓包疑点），此时返回
    available=false，对话全文见 rounds/round_XXX.md（每轮单文件）供人工评估。
    """
    st = _get_dialog(session_id)
    with st.lock:
        if st.ended and st.analysis is not None:
            return st.analysis
        items = [{"Speaker": it["Speaker"], "Content": it["Content"]}
                 for it in st.items if not it.get("hangup")]
        if len(items) < 2:
            raise RuntimeError("对话内容太少，无法分析（需要至少一问一答）")
        result = _get_client().dialog_analysis(st.coords, items)
        available = bool(result.get("LeadsGrading")
                         or result.get("DialogSummary"))

        st.write_round_md()   # 确保最终状态落盘
        out_dir = st.result_dir
        transcript = {
            "script_id": st.script_id, "session_id": session_id,
            "variables": st.variables, "rounds": st.round_index,
            "robot_ended": st.completed, "dialog_items": items,
        }
        write_json(out_dir / "transcript.json", transcript)
        write_json(out_dir / "analysis.json", {
            "session_id": session_id, "available": available, "result": result})

        st.ended = True
        st.analysis = {
            "session_id": session_id,
            "script_id": st.script_id,
            "available": available,
            "LeadsGrading": result.get("LeadsGrading"),
            "DialogSummary": result.get("DialogSummary"),
            "result_dir": str(out_dir),
            "transcript_md": str(out_dir / "rounds" / "transcript.md"),
            "note": "" if available else
                "分析结果为空（2026-09-03 抓包同现象，疑点待人工确认），"
                "对话全文见 rounds/round_XXX.md（每轮单文件）",
        }
        return st.analysis


@mcp.tool()
@tool_with_detail
def list_dialogs() -> dict:
    """列出本 MCP 服务进程内全部文本测试会话及其状态。"""
    with _dialogs_lock:
        states = list(_dialogs.values())
    return {
        "dialogs": [
            {"session_id": st.session_id, "script_id": st.script_id,
             "round_index": st.round_index, "session_completed": st.completed,
             "ended": st.ended, "last_node": st.last_node,
             "turns": len(st.items)}
            for st in states
        ]
    }


# ---------------------------------------------------------------- 获取当前登录的账号

@mcp.tool()
@tool_with_detail
def get_current_user() -> dict:
    """获取当前登录的账号（GET /console/api/v2/user，id 即账号）。

    返回 {account(id), username, email, type}。
    说明：所有其他工具执行请求前会自动做账号守卫——校验当前登录账号
    是否为全局配置中允许的账号（同一 Cookie 未变化时免重复检查）；
    若全局配置未设置允许账号，会报错提示先在配置页设置。
    """
    user = _get_client().get_current_user()
    return {"account": str(user.get("id")), "username": user.get("username"),
            "email": user.get("email"), "type": user.get("type")}


# ---------------------------------------------------------------- 剧本基本信息/Sub Agent/分析Agents（2026-09-06 新增）

@mcp.tool()
@tool_with_detail
def query_script_info(script_id: str) -> dict:
    """查询剧本基本信息（汇总多接口，一次返回全字段）。

    返回：项目组、剧本ID/名称、剧本类型（1=纯PE型 Agent 2=Multi Agents
    3=对话流程编排 Agent）、最大对话轮次、最大模型出错次数、
    Agent 回复自动挂机关键词（;分隔）、LLM 模型、
    语音识别（ASR）设置-引用热词表、语音识别（ASR）设置-上传上下文
    （开启/未开启）、**非真人接听识别开关（answer_recognize_enabled
    开启/未开启）与识别后播报内容（answer_recognize_text，三种剧本类型
    均返回，页面 UI 仅在对话流程编排提供编辑入口）**、
    挂载的分析Agents（信息抽取/线索定级/通话总结，
    各含 ID/名称/状态/更新时间）、测试版本与线上版本发布
    （版本号/状态/更新时间）。
    结果同时写入 result/{时间_账号_查询剧本基本信息}/。
    """
    info = _get_client().get_script_info(script_id)
    import datetime
    for a in (info.get("analysis_agents") or {}).values():
        ts = a.get("update_time")
        a["update_time_text"] = (
            datetime.datetime.fromtimestamp(int(ts) / 1000).strftime(
                "%Y-%m-%d %H:%M:%S") if ts else "")
    out_dir = new_result_dir("查询剧本基本信息")
    out_json = write_json(out_dir / f"{safe_filename(script_id)}.json", info)
    return {"info": info, "result_file": str(out_json)}


@mcp.tool()
@tool_with_detail
def get_sub_agents(script_id: str) -> dict:
    """查询 Multi Agents 剧本的 Sub Agent 清单（ID/名称/顺序）。

    返回 {sub_agents: [{sub_agent_id, sub_agent_name, agent_mode}...]}；
    非 Multi Agents 剧本返回空列表。
    """
    subs = _get_client().get_sub_agents(script_id)
    return {"script_id": script_id, "total": len(subs),
            "sub_agents": [
                {"sub_agent_id": s.get("SubAgent", {}).get("SubAgentID"),
                 "sub_agent_name": s.get("SubAgent", {}).get("SubAgentName"),
                 "agent_mode": s.get("SubAgent", {}).get("AgentMode")}
                for s in subs]}


@mcp.tool()
@tool_with_detail
def get_sub_agent_info(script_id: str, sub_agent_id: str) -> dict:
    """查询单个 Sub Agent 的 LLM 模型与提示词配置。

    返回 ModelType（LLM 模型名）与提示词全文（系统/开场白）；
    提示词可达 60KB，全文落盘 result/{时间_账号_查询SubAgent配置}/，
    返回摘要（长度+前200字）与文件路径，供按需 Read 读取分析。
    """
    client = _get_client()
    prompt_cfg = client.get_prompt_config(script_id, sub_agent_id=sub_agent_id)
    config = client.get_script_config(script_id, sub_agent_id=sub_agent_id)
    only = prompt_cfg.get("PromptOnlyConfig") or {}
    prologue = only.get("Prologue") or {}
    sys_prompt = only.get("CustomPrompt") or ""
    out_dir = new_result_dir("查询SubAgent配置")
    out_json = write_json(out_dir / f"{safe_filename(script_id)}_"
                          f"{safe_filename(sub_agent_id)}.json",
                          {"script_id": script_id,
                           "sub_agent_id": sub_agent_id,
                           "prompt_config": prompt_cfg, "config": config})
    return {
        "script_id": script_id, "sub_agent_id": sub_agent_id,
        "model_type": prompt_cfg.get("ModelType") or "",
        "system_prompt_len": len(sys_prompt),
        "system_prompt_head": sys_prompt[:200],
        "prologue_fixed_text": prologue.get("FixedTextStr") or "",
        "latest_updated_version": prompt_cfg.get("LatestUpdatedVersion"),
        "latest_updated_user": prompt_cfg.get("LatestUpdatedUser"),
        "result_file": str(out_json),
    }


@mcp.tool()
@tool_with_detail
def query_analysis_agents(agent_type: str | None = None) -> dict:
    """查询分析Agent列表（CloudLadder 域，独立于剧本）。

    参数 agent_type：通话总结/信息抽取/线索定级（短名/全名/DSA 等标识
    均可，可省略=全部）。
    返回 {agents: [{name, id, type, status(已发布/未发布),
    update_time_text}...], total}。
    """
    client = _get_client()
    from volc_aibot.client import ladder_type_alias_map
    type_ids: list[str] | None = None
    if agent_type:
        tid = ladder_type_alias_map().get(agent_type)
        if tid is None:
            raise _ToolError(f"未知类型 {agent_type}；可选: "
                             "通话总结/信息抽取/线索定级（或 DSA/BDE/BLG）")
        type_ids = [tid]
    agents, total = client.list_cloud_ladder_agents(type_identifiers=type_ids)
    import datetime
    rows = []
    for a in agents:
        ts = a.get("UpdateTime")
        rows.append({
            "name": a.get("Name") or "", "id": a.get("AgentId") or "",
            "type": a.get("Type") or "",
            "status": client.ladder_agent_status(a),
            "update_time_text": (
                datetime.datetime.fromtimestamp(int(ts) / 1000).strftime(
                    "%Y-%m-%d %H:%M:%S") if ts else ""),
        })
    return {"agents": rows, "total": total}


@mcp.tool()
@tool_with_detail
def get_analysis_agent(agent_id: str) -> dict:
    """获取分析Agent内容：系统提示词/用户提示词/状态/更新时间/模型参数。

    提示词全文落盘 result/{时间_账号_获取分析Agent内容}/{AgentID}.json（含
    AgentConfig 原始结构），返回摘要（提示词长度+前200字/模型名/状态）
    与文件路径，供按需 Read 读取分析。
    """
    client = _get_client()
    agent_config = client.get_cloud_ladder_agent_config(agent_id)
    summary = ((agent_config.get("GeneralAgentConfig") or {})
               .get("SummaryAgentConfig") or {})
    tmpl_sys = tmpl_user = ""
    for t in summary.get("InputTmpls") or []:
        if t.get("Role") == 1:
            tmpl_sys = t.get("Text") or ""
        elif t.get("Role") == 2:
            tmpl_user = t.get("Text") or ""
    model = summary.get("ModelParam") or {}
    agents, _ = client.list_cloud_ladder_agents(agent_ids=[agent_id])
    info = agents[0] if agents else {}
    import datetime
    ts = info.get("UpdateTime")
    out_dir = new_result_dir("获取分析Agent内容")
    out_json = write_json(out_dir / f"{safe_filename(agent_id)}.json", {
        "agent_id": agent_id, "name": info.get("Name"),
        "type": info.get("Type"),
        "status": client.ladder_agent_status(info) if info else "未知",
        "update_time": ts,
        "model": model, "system_prompt": tmpl_sys,
        "user_prompt": tmpl_user, "agent_config": agent_config,
    })
    return {
        "agent_id": agent_id, "name": info.get("Name") or "",
        "type": info.get("Type") or "",
        "status": client.ladder_agent_status(info) if info else "未知",
        "update_time_text": (
            datetime.datetime.fromtimestamp(int(ts) / 1000).strftime(
                "%Y-%m-%d %H:%M:%S") if ts else ""),
        "model": model.get("ModelName") or "",
        "endpoint": model.get("Endpoint") or "",
        "temperature": model.get("Temperature"),
        "system_prompt_len": len(tmpl_sys),
        "system_prompt_head": tmpl_sys[:200],
        "user_prompt": tmpl_user,
        "result_file": str(out_json),
    }


# ---------------------------------------------------------------- 批量导出与关键字搜索（2026-09-06 新增）

@mcp.tool()
@tool_with_detail
def batch_export_scripts(group_names: list[str] | None = None,
                         concurrency: int | None = None) -> dict:
    """批量导出剧本：按项目组导出其下全部剧本（不指定=全部项目组）。

    参数 concurrency：获得剧本清单后的并行导出数（默认 5，范围 1~10；
    实测并发 5 提速约 3.7x 且无错误，过高有服务端频控风险）。
    返回 {export_dir（以 _批量导出剧本 结尾，目录名含当前火山账号）,
    total, exported, failed, scripts:[{项目组/剧本ID/剧本名称/导出文件}]}；
    目录内含 清单.json（亦记录账号，区分环境）。
    注意：项目组较多/剧本较多时耗时相应增加（并发导出）。
    """
    import time as _time
    from volc_aibot.result import EXPORT_DIR_SUFFIX
    from volc_aibot.concurrency import clamp_concurrency, run_parallel
    from volc_aibot import progress
    c = clamp_concurrency(concurrency)
    # 事件归属「批量导出」面板（web 快捷工具批量搜索页同款展示）
    progress.set_source("export")
    _start = _time.perf_counter()
    try:
        return _batch_export_impl(group_names, c)
    finally:
        elapsed_s = round(_time.perf_counter() - _start, 1)
        progress.set_source("")


def _batch_export_impl(group_names, c) -> dict:
    import time as _time
    from volc_aibot.result import EXPORT_DIR_SUFFIX
    from volc_aibot.concurrency import run_parallel
    from volc_aibot import progress
    _start = _time.perf_counter()
    client = _get_client()
    groups = client.query_project_groups()
    if group_names:
        wanted = set(group_names)
        selected = [g for g in groups if g.get("group_name") in wanted]
        missing = wanted - {g.get("group_name") for g in selected}
        if missing:
            raise _ToolError(f"项目组不存在: {sorted(missing)}；"
                             "先 query_project_groups 查可用名称")
    else:
        selected = groups
    if not selected:
        raise _ToolError("未找到任何项目组（检查账号权限）")

    # 账号写入目录名（prompt 约定 {时间_账号_功能描述}，区分环境）
    account = client.get_current_account()
    out_dir = new_result_dir(EXPORT_DIR_SUFFIX)

    manifest: list[dict] = []
    exported = failed = 0
    for g in selected:
        gid = g["id"]
        gname = g.get("group_name") or str(gid)
        scripts = client.list_group_all_scripts(gid)

        # 并发导出（prompt 2026-09-06：获得剧本后并行执行导出操作）
        def export_one(s: dict):
            sid = s.get("AgentID") or ""
            filename, content = client.export_script(sid)
            path = out_dir / safe_filename(
                f"{safe_filename(gname)}_{safe_filename(filename)}")
            path.write_bytes(content)
            return {"export_file": path.name, "size_bytes": len(content)}

        def on_progress(s: dict, index: int, total: int) -> None:
            progress.report_stage(
                f"导出剧本 {s.get('AgentID') or ''}", group=gname,
                script_id=s.get("AgentID") or "",
                script_name=s.get("AgentName") or "",
                index=index, total=total)

        results = run_parallel(scripts, export_one, concurrency=c,
                               progress=on_progress)
        for s, r in zip(scripts, results):
            sid = s.get("AgentID") or ""
            row = {"project_group": gname, "script_id": sid,
                   "script_name": s.get("AgentName") or "",
                   "agent_mode_name": agent_mode_name(s.get("AgentMode"))}
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
    # 完成事件：页面清空计时并显示完成提示（含总耗时）
    elapsed_s = round(_time.perf_counter() - _start, 1)
    progress.report_done(
        f"批量导出完成：成功 {exported}/{len(manifest)}"
        + (f"，{failed} 个失败" if failed else ""),
        elapsed_s=elapsed_s, total=len(manifest), exported=exported,
        failed=failed, export_dir=str(out_dir))
    return {"export_dir": str(out_dir), "account": str(account),
            "total": len(manifest), "exported": exported,
            "failed": failed, "scripts": manifest}


@mcp.tool()
@tool_with_detail
def search_exported_scripts(keyword: str,
                            export_dir: str | None = None,
                            with_count: bool = False) -> dict:
    """在「批量导出剧本」目录的导出文件中按行搜索关键字。

    参数：
    - keyword：要搜索的关键字；
    - export_dir：批量导出目录（省略=取最新一次导出目录）；
    - with_count：是否统计出现次数（默认 False，仅判定是否出现）。
    返回 {total, hits, results: [{项目组/剧本ID/剧本名称/剧本类型/
    关键字/是否出现/出现次数}]}（剧本类型取自导出清单；
    with_count=False 时出现次数为空）。
    """
    from volc_aibot.result import EXPORT_DIR_SUFFIX, RESULT_DIR
    if not keyword:
        raise _ToolError("缺少 keyword（要搜索的关键字）")

    def _find_export_dirs() -> list[Path]:
        if not RESULT_DIR.is_dir():
            return []
        return sorted((d for d in RESULT_DIR.iterdir()
                       if d.is_dir()
                       and d.name.endswith(EXPORT_DIR_SUFFIX)),
                      reverse=True)

    def _load_manifest(d: Path) -> dict:
        mf = d / "清单.json"
        if not mf.is_file():
            return {}
        return {row.get("export_file", ""): row
                for row in json.loads(
                    mf.read_text(encoding="utf-8")).get("scripts", [])}

    def _search_file(path: Path, kw: str) -> tuple[bool, int]:
        found, count = False, 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if kw in line:
                        found = True
                        count += line.count(kw)
        except OSError:
            pass
        return found, count

    target: Path | None
    if export_dir:
        p = Path(export_dir)
        if not p.is_absolute():
            p = (RESULT_DIR / export_dir
                 if (RESULT_DIR / export_dir).exists() else p)
        target = p if p.is_dir() else None
    else:
        dirs = _find_export_dirs()
        target = dirs[0] if dirs else None
    if target is None:
        raise _ToolError("导出目录不存在（先 batch_export_scripts），"
                         "或指定正确的 export_dir")

    manifest = _load_manifest(target)
    files = sorted(p for p in target.iterdir()
                   if p.is_file() and p.suffix == ".json"
                   and p.name != "清单.json")
    results: list[dict] = []
    for p in files:
        meta = manifest.get(p.name) or {}
        found, count = _search_file(p, keyword)
        results.append({"project_group": meta.get("project_group", ""),
                        "script_id": meta.get("script_id", ""),
                        "script_name": meta.get("script_name", ""),
                        "agent_mode_name": meta.get("agent_mode_name", ""),
                        "keyword": keyword,
                        "found": found,
                        "found_text": "是" if found else "否",
                        "count": count if with_count else ""})
    out_dir = new_result_dir("搜索已导出剧本内容关键字")
    write_json(out_dir / "结果.json", {
        "export_dir": str(target), "keyword": keyword,
        "with_count": with_count, "results": results})
    hits = [r for r in results if r["found"]]
    return {"export_dir": str(target), "total": len(results),
            "hits": len(hits), "results": results,
            "result_file": str(out_dir / "结果.json")}


# ---------------------------------------------------------------- 入口

def main() -> None:
    ap = argparse.ArgumentParser(description="火山引擎智能外呼 MCP 服务"
                                             "（同端口提供 Web 配置页）")
    ap.add_argument("--transport", choices=["sse", "stdio"], default="sse",
                    help="MCP 传输协议（默认 sse）")
    ap.add_argument("--host", default="127.0.0.1",
                    help="监听地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=None,
                    help="监听端口（默认读全局配置，缺省 19000；"
                         "HTTP 配置页与 MCP SSE 同端口）")
    ap.add_argument("--timeout", type=float, default=60.0,
                    help="HTTP 请求超时秒数（默认 60）")
    ap.add_argument("--cookie-api", default=None,
                    help="chrome_capture_operate Cookie 服务地址（默认 127.0.0.1:33445）")
    args = ap.parse_args()

    logger = setup_logging()
    global _client
    _client = VolcAIBotClient(cookie_api=args.cookie_api, timeout=args.timeout,
                              logger=logger)

    port = args.port or global_config.get_server_port()
    logger.info("MCP 服务启动: %s（mcp SDK %s），端口 %s："
                "配置页 http://%s:%s/ ，SSE 端点 http://%s:%s/sse",
                args.transport, "2.x" if MCP_SDK_V2 else "1.x", port,
                args.host, port, args.host, port)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return

    # 同端口：Web 配置页 + MCP SSE（自定义 uvicorn 服务）
    import asyncio
    import uvicorn

    # 注入 MCP 实例：配置页 /api/tools 查询工具清单（名称/描述/参数）
    set_mcp_instance(mcp)

    sse_app = mcp.sse_app(host=args.host)
    app = build_app(sse_app, host=args.host)

    async def serve() -> None:
        config = uvicorn.Config(app, host=args.host, port=port,
                                log_level="info")
        server = uvicorn.Server(config)
        # 托盘永远启动（无参数控制）：无窗口运行时托盘是唯一的人工退出入口
        _start_tray(args.host, port)
        await server.serve()

    asyncio.run(serve())


def _start_tray(host: str, port: int) -> None:
    """启动系统托盘（双击/右键打开配置页，右键退出）。失败不致命。"""
    import threading
    try:
        from volc_aibot.tray import run_tray
    except ImportError:
        return
    url = f"http://{host}:{port}/"
    threading.Thread(target=run_tray, args=(url,), daemon=True).start()


if __name__ == "__main__":
    main()