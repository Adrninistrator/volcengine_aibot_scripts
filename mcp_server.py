#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""火山引擎智能外呼 MCP 服务（SSE，默认端口 19000，支持配置）。

把全部独立脚本功能封装为 MCP 工具（共 28 个），供 AI 调用：
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
- 查询通话明细（2026-09-06 新增）：query_call_records
- 导出通话文本（2026-11-02 新增）：get_call_transcripts
- 删除剧本（2026-09-07 新增）：delete_script（仅 _由AI修改 后缀）

兼容 mcp SDK 1.x（FastMCP）与 2.x（MCPServer）两个大版本。

同端口服务（prompt 新要求）：
- HTTP 配置页 http://127.0.0.1:{port}/（监听端口、是否允许执行修改操作开关
  与允许账号，写全局配置 ~/.volcengine_aibot_scripts/global.json）；
- 系统自启动 HTTP 接口（prompt 2026-09-22）：GET/POST /api/autostart
  （AI 可 curl 查询/设置，见 usage_guide quickstart）；
- MCP SSE 端点 http://127.0.0.1:{port}/sse；
- 系统托盘（双击打开配置页，右键退出）；pythonw 运行无窗口。

修改守卫（prompt 约定，2026-09-07）：修改类操作执行前先检查「是否允许执行
修改操作」开关（默认关闭，未开启拒绝并弹配置页），再校验当前登录账号
（/console/api/v2/user 的 id）是否为全局配置允许的账号；同一 Cookie 未变化
时免重复检查；未配置账号时报错提示先到配置页设置。

运行前提：
1) install.bat 安装依赖（requests mcp uvicorn sse-starlette websockets）；
2) chrome_capture_operate 服务运行中（默认 http://127.0.0.1:33445），
   当前 Cookie 来源对应的 Chrome 已登录火山引擎控制台（来源在配置页
   维护、页面右上角切换：日常 Chrome 插件 profile 或 CDP 抓包端口）。

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
import datetime
import json
import sys
import threading
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from volc_aibot import global_config                     # noqa: E402
from volc_aibot.call_transcript import export_transcripts  # noqa: E402
from volc_aibot.client import (VolcAIBotClient,            # noqa: E402
                                  _fmt_ms_time, agent_mode_name,
                                  fmt_launch_time, mask_text_from_name)
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
   当前 Cookie 来源对应的 Chrome 已登录火山引擎智能外呼控制台（登录态
   Cookie 实时查询，脚本不保存；来源在配置页维护、页面右上角切换）；
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

**服务配置 HTTP 接口（AI 可直接 curl 调用，prompt 2026-09-22）**
- 服务端口：读全局配置 ~/.volcengine_aibot_scripts/global.json 的
  server_port（默认 19000；GET /api/config 亦可查询）；
- 查询系统自启动：GET http://127.0.0.1:{端口}/api/autostart
  → {autostart（是否已开启）, command（自启动命令行）, reg_name,
  other_entries（其他目录残留项）}；
- 设置系统自启动：POST http://127.0.0.1:{端口}/api/autostart，
  请求体 {"enabled": true} 开启 / {"enabled": false} 关闭（写/删注册表
  HKCU Run 键，与配置页勾选「系统自启动」等效）；
- 注意：监听端口、修改操作开关、允许账号等参数仅人工在配置页修改，
  AI 勿经 HTTP 修改（prompt 约定，以免用错环境）。

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
  文件含服务端 checksum；手工修改内容后仍可直接 import_script
  （导入时自动重算 checksum）。
- import_script(file_path, group_name)：导入剧本到项目组 → 返回新剧本ID
  （new_agent_id，llm_xxx）。file_path 用 export_script 返回的路径，手工
  修改过的文件也可直接导入（在副本上自动补「_由AI修改」后缀并重算
  checksum，原文件不动，无需人工预处理）；group_name 精确匹配（先
  query_project_groups 查可用名称）。
  ⚠ 新剧本未发布（版本0），对话测试前必须 publish_preview。

复制剧本完整链路：
export_script → import_script(导出文件, 项目组名) → publish_preview(新剧本ID)
→ start_dialog(新剧本ID) ...

修改剧本文件链路（格式说明先行）：export_script → **先 Read
api/bot_json_desc.md（项目根目录下）
理解文件格式**（三类剧本字段差异、字符串化 JSON 字段清单与重序列化
格式约束、checksum 重算要求、常见坑）→ 修改副本 → import_script(副本,
项目组名) → publish_preview(新剧本ID) ..."""
),
"variables": (
"""## 剧本变量（两套体系，勿混淆）

**1. 变量定义（增删改）：query_variables / add_variable / update_variable / delete_variable**
- 先 query_variables(script_id) 查现有变量（修改/删除需要变量 id 与全量列表）；
  返回全部属性，含 IsPreset（前置传入）/Description（变量描述）/
  IsMasked（是否掩码并替换）/MaskContent（掩码替换文本）；
- ⚠ 前置传入(IsPreset)与是否必填(IsRequired)语义绑定（2026-10-10 晚
  直连实测）：IsPreset=false 时必填恒为 false——服务端对
  IsRequired=true 且 IsPreset=false 的载荷不报错但静默置回 false；
  is_required=true 自动联动 is_preset=true，is_preset=false 自动联动
  is_required=false（工具内已内置，无需同时传）；
- add_variable(script_id, name, key, variable_type, is_required,
  is_preset?, description?)：key 只支持英文与下划线（如 test_vvv）；
  variable_type 1=String 2=Integer 3=Float 4=Boolean；
- update_variable(script_id, key, ...)：按调用名称定位，改 name/类型/
  必填/前置传入/描述/掩码（至少一项）；description 传空串 "" 清除描述；
  mask=true 时设置“是否掩码并替换”为是，掩码替换文本
  固定为该变量的变量名称仅保留中文/字母/数字的结果（不可指定其他值，
  名称含下划线/括号等先被过滤）——**用户要求设置掩码时直接调用
  mask=true 即可，无需先查询当前是否已掩码/掩码值（重复设置幂等）**；
  掩码的运行效果只能通过通话测试（真实拨号）后在通话明细中验证，
  文本对话测试不产生通话明细记录；
- delete_variable(script_id, key)：按调用名称定位（服务端按变量数字ID删除）；
- batch_modify_variables(script_id, add?, update?, delete?)：**批量**
  增删改（一次调用多个变量，可混合；与控制台弹窗一次提交多变量同
  口径）——批量修改场景（给一批变量统一设掩码/描述/必填）用它而非
  逐条调用；add/update/delete 每项参数与同名单条工具一致（update
  按调用名称定位）；
- ⚠ **修改变量与发布是两个独立操作**：修改定义后**不要为了"生效"
  自动发布**——发布（publish_preview / 发布上线）仅在**用户明确
  要求**时调用；未发布时修改保留为草稿（不影响已发布版本的对话与
  赋值），何时发布由用户决定。

**2. 变量赋值（测试/线上版本）：set_preview_variables**
- set_preview_variables(script_id, values, env="preview")，values 如
  {"coupon_a_lock_term": 3}（值统一按字符串提交）；env="preview"=测试
  版本（默认）/env="online"=线上版本（赋值即时生效于线上外呼、无需
  发布，⚠ 生产变更受修改守卫控制、谨慎使用，线上变量需剧本已发布
  上线）；
- ⚠ 必填变量（is_required=true）赋值时值不能为空，传空值会被拒绝；
- ⚠ 对话测试只需为前置传入=是的变量赋值（控制台"变量赋值"面板只显示
  它们；2026-10-11 实测：非前置传入变量在 context 中省略对话行为不变）；
- 修改的是测试版本（Preview）变量取值，不影响线上；
- start_dialog 默认使用测试版本全局变量作为对话变量；overrides 为会话级变量，会覆盖同名变量（仅本段会话生效，不改测试版本全局变量），未指定时使用测试版本全局变量。

修改后验证话术链路（发布步骤仅在用户明确要求发布时执行，修改后不自动发布）：
query_variables → update_variable → [用户要求时] publish_preview → start_dialog ...
或：set_preview_variables → start_dialog ...

**3. 通话测试（真实拨号，2026-10-12 新增）：start_call_test**
- start_call_test(script_id, phone, env="preview", variables?,
  wait_result=true)：对应控制台「测试与发布 → 通话测试」；
- env="preview" 用测试版本（默认）/"online" 用线上版本（未发布上线
  报错）；主叫号码恒为平台默认；phone 支持单个号码或列表；
- variables 指定本次通话变量值（仅 preview：先赋值再发起，值经回调
  接口注入；线上变量为发布快照不可指定）；
- ⚠ 真实拨打被叫号码（生产外呼资源）：生产剧本慎用，建议测试项目组
  剧本验证；发起接口不返回通话ID，wait_result 轮询通话明细至记录出现
  （call_records 返回接通/SIP/轮次/评级/CallID）。"""
),
"import_export": (
"""## 导入/导出剧本

- export_script(script_id)：导出剧本 JSON（写入 result/{日期}/{时间_账号_导出剧本}/），
  返回 export_file 路径、文件名、大小（文件含服务端 checksum）；
- import_script(file_path, group_name)：
  - file_path：剧本 JSON 文件路径，export_script 返回的 export_file 或
    手工修改过的文件（导入前在副本上自动补「_由AI修改」后缀并重算
    checksum，原文件不动，无需人工预处理）；
  - group_name：目标项目组名称，精确匹配（先用 query_project_groups 查询）；
  - 返回 new_agent_id（新剧本ID）、new_script_name（原名+导入时间戳后缀）、
    new_service_id；
- ⚠ 新导入剧本未发布（版本0），对话测试前必须先 publish_preview。

完整链路：export_script → import_script → publish_preview(新剧本ID)
→ start_dialog(新剧本ID)。"""
),
"publish": (
"""## 发布剧本（测试版本 / 上线）

publish_preview(script_id, description?)

- 提交发布并自动轮询到完成（默认最多 600 秒，无需人工等待干预）；
- 成功判据：release-launch 的 train_info.version 相比提交前 +1 且
  status=FINISHED（返回 before_version / new_version）；
- 发布的是服务端已保存的剧本草稿；本 MCP 的变量修改（add/update/delete_variable）
  直接提交到服务端，发布后生效；
- 哪些场景需要发布（**仅用户明确要求发布时才调用发布工具**——
  修改变量后不要为了生效自动发布）：
  - 新导入的剧本（版本0）→ 对话测试前必须发布；
  - 修改变量定义后 → 需发布才生效（是否发布、何时发布由用户决定）；
  - 测试版本变量赋值（set_preview_variables）→ 文本对话测试直接生效，
    实际外呼是否需发布未验证。

publish_online(script_id)

- 发布上线（**生产变更**）：把当前测试版本发布到线上；前置为已有发布
  成功的测试版本（版本0的未发布剧本会报错提示先 publish_preview）；
- 成功判据：release-launch 的 online_info.status == "Succeeded"（注意与
  测试版本的 FINISHED 拼写不同）；同版本重复发布幂等；
- ⚠ 仅在测试项目组剧本上使用，避免误改线上环境。

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
  或当前来源未提供 Cookie（日常 Chrome 来源：确认插件已装、推送范围允许
  volcengine.com、Chrome 已登录；CDP 来源：确认抓包 Chrome 已登录）；
  可在页面右上角切换到其他已启用来源（实时生效）；
- 修改类操作报「是否允许执行修改操作开关未开启」：该开关默认关闭，
  需人工在 Web 配置页「配置参数」中勾选并保存（AI 无法代开）；
- 修改类操作报「未配置允许账号 / 账号不在允许范围」：在配置页设置
  「允许执行修改操作的账号」（建议测试环境账号），并确认 Chrome 登录
  的是该账号；
- 接口 HTTP 401/403：当前来源对应的 Chrome 里重新登录火山引擎控制台
  （服务会按当前来源重取 Cookie 重试一次）；仍失败可切换 Cookie 来源；
- “剧本ID xxx 无精确匹配”：剧本ID 拼写错误，用 search_script 搜索；
- start_dialog 报“测试版本未发布”：先 publish_preview(script_id)；
- “会话不存在”：MCP 服务重启过（会话存内存），重新 start_dialog；
- end_dialog 结果 available=false：对话分析接口返回空（2026-09-03 抓包同
  现象，服务端疑点），对话全文已保存至 result 目录；
- import_script 报「文件读取/处理失败」：文件不是合法的剧本 JSON
  （须为 export_script 导出的文件或在其基础上修改）；
- “变量 xxx 不存在”：调用名称（key）拼写问题，先 query_variables；
- MCP 报 `-32602 Invalid request parameters` 或**所有工具同时不可用**
  （连 query_project_groups 也失败）：通常是客户端会话失效或服务未重启，
  请重启服务（start.bat）或重连 MCP 客户端，不是账号/业务接口问题。"""
),
"agents_info": (
"""## 剧本基本信息 / Sub Agent / 分析Agents

**剧本基本信息（query_script_info）**
- query_script_info(script_id)：一次返回 prompt 所需全字段——项目组/
  剧本名称/剧本类型（1=对话流程编排 2=纯PE型 3=Multi Agents）/
  最大对话轮次/最大模型出错次数/挂机关键词（;分隔）/LLM 模型/
  ASR 引用热词表/ASR 上传上下文（开启/未开启）/挂载的分析Agent
  （信息抽取/线索定级/通话总结 各 ID/名称/状态/更新时间）/
  测试版本与线上版本发布（版本号/状态/更新时间）；
- 结果写入 result/{日期}/{时间_账号_查询剧本基本信息}/。

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
- query_call_records(group_name, script_id, date_start, date_end,
  grading?/labeled_grading?/duration_op?/duration_value?或duration_min/max?/
  answer_recognize?/search_id?/search_type?/env?/is_connected?/task_ids?/
  order_by?/sort_type?, page_index?, all_pages?, page_size=100,
  include_records?)：查询通话明细（被叫/主叫号码、接通状态、SIP码、
  时长秒、轮次、环境、命中语音助手、意向等级、信息抽取、短信状态、
  创建时间、通话ID）。筛选条件与通话明细页一致；**默认只查第一页**
  （page_index=0），需要全量时传 `all_pages=true`（约 1 万条上限，
  触达返回 truncated/warning）。默认返回 total + 前 5 条样例 +
  result_file；`include_records=true` 才返回完整数组。结果落盘 result 目录。
- get_call_transcripts(call_id? 或 group_name+script_id+筛选条件?,
  output_dir?, limit?, order_by?, sort_type?, skip_existing?, workers?,
  include_call_info?, include_line_meta?, analyze?, keywords?)：
  导出通话文本——每条通话写入 `<output_dir>/<通话ID>.md`，对话行以
  机器人:/客户: 区分说话人（默认带轮次/时间/打断前缀）；
  机器人话术里的 `{{企业名称}}` 等双花括号是**生产账号的安全策略**（变量只
  回显名称、不展示取值），属正常现象，不要当作数据异常；
  数据已清除（页面「数据已清除」）的记录跳过并列入 skipped。
- search_exported_scripts(export_dir?, keyword, with_count?)：在导出
  目录中按行搜索关键字，返回 项目组/剧本ID/剧本名称/是否出现/出现次数
  （with_count=false 时次数为空）。export_dir 省略时取最新导出目录。
  典型用法：先 batch_export_scripts 导出，再 search_exported_scripts
  检查全部剧本内容是否含某关键字。"""
),
"transcript_loop": (
"""## 通话文本分析闭环

1. **抽样确认口径**：`query_call_records(page_index=0, ...)`
   先看第一页；需要全量时传 `all_pages=true`（注意约 1 万条上限，
   触达时返回 truncated/warning）。
2. **导出文本**：`get_call_transcripts(group_name, script_id,
   date_start, date_end, limit=50, output_dir=..., analyze=true)`；
   也可按 `call_id` 单条导出（建议同时传 `script_id`）。
3. **对照话术配置**：`export_script` / `query_variables` 查看剧本与变量。
4. **修改并发布测试版本**：`import_script` + `publish_preview`。
5. **复跑对比**：重复 1~2，对比同一筛选口径的文本与统计。

产物：
- `<output_dir>/<通话ID>.md`：头部通话信息 + 逐行对话（默认带轮次/
  时间/打断/节点前缀；事件行标 `[事件]`；`include_line_meta=false`
  输出纯 `机器人:/客户:`；`include_call_info=false` 只输出对话）；
- `manifest.json`：导出条数/跳过/失败/逐条文件；
- `query.json`：本次全部筛选参数；
- `summary.json`/`summary.md`：`analyze=true` 时输出关键词、意向、
  句长、打断、节点到达/末节点分布等统计。

常见坑：
- `Schema` 只接受具体剧本ID，按 `call_id` 查询也建议传 `script_id`；
- 默认单页，导出/全量统计必须传 `all_pages=true`（导出工具内部已处理）；
- 数据已清除（`IsDataPruned=true`）的记录没有文本，会列入 skipped；
- 文本中的 `{{企业名称}}` 等双花括号是生产安全策略，不是数据异常。"""
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
    测试版本/线上版本号与状态、剧本修改时间（modify_time，agent/list 的
    UpdateTime 格式化）等。
    """
    agent = _get_client().query_script(script_id)
    out = dict(agent)
    out["modify_time"] = _fmt_ms_time(agent.get("UpdateTime"))
    return out


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
    返回导出文件路径、文件名、大小。导出文件含服务端 checksum 字段；
    手工修改内容后仍可直接 import_script 导入（导入时会自动重算 checksum）。
    **剧本 JSON 文件格式说明（三类剧本字段结构/字符串化字段清单/修改约束）
    见 api/bot_json_desc.md（项目根目录下）
    ——修改剧本文件前必须先 Read 该文档理解格式**（嵌套字段是字符串化
    JSON，需按原格式重新序列化；checksum 修改后必须重算）。
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
        "hint": "可手工修改内容后 import_script 导入（自动补后缀+重算 checksum）；"
                 "格式说明见 api/bot_json_desc.md，修改前先 Read 理解格式",
    }


@mcp.tool()
@tool_with_detail
def import_script(file_path: str, group_name: str) -> dict:
    """导入剧本文件到指定项目组，生成新剧本（导入校验自动处理）。

    - file_path: 剧本 JSON 文件路径（export_script 返回的 export_file；
      手工修改过的文件也可直接导入，无需预处理）
    - group_name: 目标项目组名称（精确匹配，如 电销项目组_测试；
      可先用 query_project_groups 查询可用名称）
    **修改剧本文件前必须先 Read 格式说明**
    api/bot_json_desc.md（项目根目录下）
    （三类剧本字段结构、字符串化 JSON 字段清单与重序列化格式约束、
    checksum 重算要求、常见坑）。
    **导入前自动完成**（prompt 约定，使用者无需任何操作；需要修改时在
    副本上进行，传入的原文件保持不动）：
    - `data.meta.name`（剧本名称）不带「_由AI修改」后缀 → 自动补上（已带
      后缀——含时间戳形态「xxx_由AI修改(2026...)」——则不重复加）。
      名称加该后缀是为了**标记由 AI 导入的剧本，保证由 AI 导入的剧本才
      允许由 AI 删除**（delete_script 同一后缀约束），人工维护的剧本不会
      被 AI 误删；
    - 重算 checksum 并修正失配值（内容被手工修改过时），以通过火山引擎
      管理台后台校验（否则被拒，Code=101 文件已被修改）。
    返回新剧本信息：new_agent_id（新剧本ID，llm_xxx）、new_script_name、
    new_service_id、import_file（实际上传的文件，与传入文件不同时为
    处理后的副本）等。新剧本未发布（版本0），对话测试前需 publish_preview。
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
        "import_file": result.get("import_file"),
        "result_file": str(out_dir / "import.json"),
        "hint": "新剧本未发布，对话测试前请先 publish_preview",
    }


@mcp.tool()
@tool_with_detail
def delete_script(script: str, group_name: str | None = None) -> dict:
    """删除剧本（修改操作，谨慎使用）。

    - script：**剧本ID（llm_xxx）或剧本名称**均可——只知道名称时经
      agent/list 精确解析后删除（重名会报错并提示改用ID）；
    - group_name：可选，限定项目组（校验剧本在该组，防跨组误删）；
    **安全约束（prompt 约定）**：仅当剧本名称以「_由AI修改」为后缀
    （兼容导入时间戳形态「xxx_由AI修改(20260907...)」）才允许删除——
    双重校验（本地 + 调火山接口反查最新名称确认后缀），
    其他剧本一律拒绝。删除后回查列表确认已移除。
    返回 {script_id, script_name, deleted, remain_count, result_file}。
    """
    client = _get_client()
    r = client.delete_script(script, group_name=group_name)
    out_dir = new_result_dir("删除剧本")
    write_json(out_dir / "delete.json", r)
    return {
        "script_id": r["script_id"],
        "script_name": r["script_name"],
        "group_id": r["group_id"],
        "group_name": r.get("group_name") or "",
        "deleted": r["deleted"],
        "remain_count": r["remain_count"],
        "result_file": str(out_dir / "delete.json"),
        "note": "仅允许删除名称以 _由AI修改 为后缀的剧本；"
                "删除后回查列表确认" if r["deleted"] else
                "⚠ 回查仍存在，请到控制台人工确认",
    }


@mcp.tool()
@tool_with_detail
def publish_preview(script_id: str, description: str = "") -> dict:
    """发布剧本测试版本并轮询至完成（变量修改后需发布才生效；对话测试前置）。

    ⚠ 发布与修改变量是**独立操作**：仅在用户明确要求发布时调用本工具
    ——修改变量后不要为了"生效"自动发布（未发布时修改保留为草稿，
    不影响已发布版本的对话与赋值，何时发布由用户决定）。

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


@mcp.tool()
@tool_with_detail
def publish_online(script_id: str) -> dict:
    """发布上线（生产变更）：将当前测试版本发布到线上，轮询至完成。

    - script_id: 剧本ID（llm_xxx）
    前置：剧本需已有发布成功的测试版本（publish_preview）。
    成功判据（直连实测）：release-launch 的 online_info.status ==
    "Succeeded"（注意与测试版本 train_info 的 "FINISHED" 拼写不同）；
    同版本重复发布幂等。
    ⚠ 生产变更：请仅在测试项目组剧本上使用，避免误改线上环境。
    ⚠ **发布上线会影响正在进行中的通话**（通话中机器人使用的版本被
    切换；控制台发布确认弹窗亦提示"外呼中严禁发布上线"）——需在剧本
    **没有通话的时间段**执行，发布前先确认该剧本当前无进行中的通话。
    """
    client = _get_client()
    result = client.publish_online(script_id)
    out_dir = new_result_dir("发布上线")
    write_json(out_dir / "publish_online.json", result)
    return {
        "script_id": script_id,
        "agent_name": result.get("agent_name"),
        "before_train_version": result["before_train_version"],
        "before_online_version": result["before_online_version"],
        "new_version": result["new_version"],
        "status": result["status"],
        "update_time": result.get("update_time"),
        "result_file": str(out_dir / "publish_online.json"),
    }


# ---------------------------------------------------------------- 剧本变量

@mcp.tool()
@tool_with_detail
def query_variables(script_id: str) -> dict:
    """查询剧本变量定义列表（含前置传入/是否必填/掩码等全部属性）。

    - script_id: 剧本ID（llm_xxx）
    返回 variables: [{id, name(名称), key(调用名称), IsRequired(是否必填),
    VariableType, Description(变量描述), IsPreset(前置传入),
    IsMasked(是否掩码并替换), MaskContent(掩码替换文本)}]，
    VariableType: 1=String 2=Integer 3=Float 4=Boolean；未设置过掩码文本
    的变量无 MaskContent 字段，未设置描述的变量无 Description 字段。
    ⚠ 前置传入(IsPreset)与是否必填(IsRequired)服务端语义绑定：
    IsPreset=false 时必填恒为 false（详见 docs/接口协议.md §6）。
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
    columns = [("name", "名称"), ("key", "调用名称"), ("IsPreset", "前置传入"),
               ("IsRequired", "是否必填"),
               ("VariableType", "变量类型数值"), ("VariableTypeDesc", "变量类型"),
               ("IsMasked", "是否掩码并替换"), ("MaskContent", "掩码替换文本"),
               ("Description", "变量描述")]

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
        "coupling_notes": result.get("notes") or [],
        "hint": ("变量修改已保存（未发布时保留为草稿，不影响已发布"
                 "版本）；发布为独立操作——仅在用户明确要求时调用 "
                 "publish_preview，勿为生效自动发布"),
    }


@mcp.tool()
@tool_with_detail
def add_variable(script_id: str, name: str, key: str,
                 variable_type: int, is_required: bool = False,
                 is_preset: bool | None = None,
                 description: str | None = None) -> dict:
    """新增剧本变量。

    - script_id: 剧本ID（llm_xxx）
    - name: 变量名称（如 测试变量）
    - key: 调用名称，只支持英文与下划线（如 test_vvv）
    - variable_type: 变量类型 1=String 2=Integer 3=Float 4=Boolean
    - is_required: 是否必填（默认否）。⚠ 必填与前置传入语义绑定：
      is_required=true 时自动联动 is_preset=true（2026-10-10 晚直连
      实测：不带前置传入时服务端不报错但把必填静默置回否——此前记录的
      "新增变量 IsRequired 不生效"根因即此）。
    - is_preset: 前置传入（默认不传=否，与控制台新增行默认一致）；
      is_required=true 时自动置 true，无需显式传。
    - description: 变量描述（可选，控制台 2026-10-10 晚新增列）
    """
    if variable_type not in VARIABLE_TYPES:
        raise RuntimeError(f"variable_type 必须是 1/2/3/4（{VARIABLE_TYPES}）")
    item = {"name": name, "key": key,
            "IsRequired": bool(is_required), "VariableType": variable_type}
    if is_preset is not None:
        item["IsPreset"] = bool(is_preset)
    if description:
        item["Description"] = description
    return _do_modify(script_id, add=[item])


@mcp.tool()
@tool_with_detail
def update_variable(script_id: str, key: str, name: str | None = None,
                    variable_type: int | None = None,
                    is_required: bool | None = None,
                    is_preset: bool | None = None,
                    description: str | None = None,
                    mask: bool | None = None) -> dict:
    """修改已存在的剧本变量（按调用名称定位，至少改一项）。

    - script_id: 剧本ID（llm_xxx）
    - key: 变量调用名称（必须已存在，先 query_variables 确认）
    - name / variable_type: 需要修改的字段（可选，至少一个）
    - is_required: 是否必填。⚠ 与前置传入语义绑定（2026-10-10 晚直连
      实测）：is_required=true 自动联动 is_preset=true，否则服务端
      不报错但把必填静默置回 false。
    - is_preset: 前置传入。置 false 时自动联动 is_required=false
      （镜像控制台行为：关前置传入会重置必填为否）。
    - description: 变量描述（控制台 2026-10-10 晚新增列）。传空串 ""
      表示清除描述（服务端空串清除，null/省略为 no-op 保留原值）。
    - mask: 是否掩码并替换。**用户要求对变量设置掩码时直接调用本工具
      即可（mask=True），无需先判断当前字段是否已掩码或掩码值——工具
      总是设置目标状态（已掩码时重复设置同样成功、幂等）**。True 时
      设置 IsMasked=true，掩码替换文本固定为该变量的变量名称**仅保留
      中文/字母/数字的结果**（不允许指定其他值；名称含下划线/括号等
      字符会被过滤，满足服务端对掩码文本的字符约束）；False 时
      IsMasked=false 且清空替换文本；None 表示不修改该项。
    修改变量后需发布测试版本（publish_preview）才能生效——但**发布是
    独立操作：修改后不要为生效自动发布**，仅在用户明确要求发布时调用
    发布工具（未发布时修改保留为草稿，不影响已发布版本的对话与赋值）。
    ⚠ 掩码的**运行效果**（变量值被掩码文本替换）只能通过通话测试
    （start_call_test，真实拨打电话）后在通话明细中验证——文本对话
    测试不产生通话明细记录，无法验证掩码效果。
    """
    client = _get_client()
    variables = client.query_variables(script_id)
    target = next((v for v in variables if v.get("key") == key), None)
    if target is None:
        raise RuntimeError(f"变量 {key} 不存在（先 query_variables 确认调用名称）")
    item = {"id": target["id"]}
    if name is not None:
        item["name"] = name
    if variable_type is not None:
        item["VariableType"] = variable_type
    if is_required is not None:
        item["IsRequired"] = bool(is_required)
    if is_preset is not None:
        item["IsPreset"] = bool(is_preset)
    if description is not None:
        # 空串=清除（服务端空串清除，null/省略为 no-op）
        item["Description"] = description
    if mask is not None:
        item["IsMasked"] = bool(mask)
        # 掩码文本=变量名称仅保留中文/字母/数字（prompt 2026-10-12）
        item["MaskContent"] = (mask_text_from_name(target.get("name"))
                               if mask else "")
    if len(item) == 1:
        raise RuntimeError("至少需要修改一项（name/variable_type/"
                           "is_required/is_preset/description/mask）")
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
def batch_modify_variables(script_id: str,
                           add: "list[dict] | None" = None,
                           update: "list[dict] | None" = None,
                           delete: "list[str] | None" = None) -> dict:
    """批量增删改剧本变量（一次调用处理多个变量，与控制台全局变量
    弹窗一次编辑多个变量后点一次确定提交同口径；三段操作可同时进行，
    至少提供一段）。

    底层接口（POST llm/global_variables）天然批量：{delete:[多个ID],
    add:[多条], update:[...]}一次提交——批量修改场景（如给一批变量
    统一设置掩码/描述/必填）应使用本工具，避免逐条调用。

    - script_id: 剧本ID（llm_xxx）
    - add: [{"name","key","variable_type","is_required"?,
      "is_preset"?,"description"?}, ...]——每项同 add_variable 单条
      参数（name/key/variable_type 必填；is_required=true 自动联动
      is_preset=true）
    - update: [{"key", "name"?,"variable_type"?,"is_required"?,
      "is_preset"?,"description"?,"mask"?}, ...]——按 key 定位（必须
      已存在），字段语义同 update_variable 单条（mask=true 时掩码
      文本为变量名称清洗结果、直接调用无需预查当前掩码状态；前置
      传入/必填联动一致；每项至少改一个字段）
    - delete: [调用名称, ...]（必须已存在）
    返回 {added, updated, deleted, before_total, after_total,
    coupling_notes, before_file, after_file}（修改前后清单写入
    result 目录，before.md/after.md 含全部变量全字段）。
    修改变量后需发布测试版本才生效——但**发布是独立操作：批量修改后
    不要为生效自动发布**，仅在用户明确要求发布时调用发布工具（未发布
    时修改保留为草稿，不影响已发布版本的对话与赋值）。
    """
    client = _get_client()
    add = [dict(a) for a in (add or [])]
    update = [dict(u) for u in (update or [])]
    delete_keys = list(delete or [])
    if not (add or update or delete_keys):
        raise RuntimeError("至少提供一段操作（add/update/delete 之一）")

    variables = client.query_variables(script_id)
    by_key = {v.get("key"): v for v in variables}

    add_items: list[dict] = []
    for a in add:
        name, key, vtype = a.get("name"), a.get("key"), a.get("variable_type")
        if not (name and key and vtype is not None):
            raise RuntimeError(
                f"add 每项需要 name/key/variable_type，缺项: {a}")
        if vtype not in VARIABLE_TYPES:
            raise RuntimeError(f"variable_type 必须是 1/2/3/4: {key}")
        if key in by_key:
            raise RuntimeError(f"add 的变量 {key} 已存在（id="
                               f"{by_key[key].get('id')}），应用 update 段")
        if any(x["key"] == key for x in add_items):
            raise RuntimeError(f"add 列表中调用名称 {key} 重复出现")
        item = {"name": name, "key": key,
                "IsRequired": bool(a.get("is_required")),
                "VariableType": int(vtype)}
        if a.get("is_preset") is not None:
            item["IsPreset"] = bool(a["is_preset"])
        if a.get("description"):
            item["Description"] = a["description"]
        add_items.append(item)

    update_items: list[dict] = []
    for u in update:
        key = u.get("key")
        target = by_key.get(key)
        if target is None:
            raise RuntimeError(f"update 的变量 {key} 不存在（先 "
                               "query_variables 确认调用名称）")
        item = {"id": target["id"]}
        if u.get("name") is not None:
            item["name"] = u["name"]
        if u.get("variable_type") is not None:
            if u["variable_type"] not in VARIABLE_TYPES:
                raise RuntimeError(f"variable_type 必须是 1/2/3/4: {key}")
            item["VariableType"] = u["variable_type"]
        if u.get("is_required") is not None:
            item["IsRequired"] = bool(u["is_required"])
        if u.get("is_preset") is not None:
            item["IsPreset"] = bool(u["is_preset"])
        if u.get("description") is not None:
            # 空串=清除（服务端空串清除，null/省略为 no-op）
            item["Description"] = u["description"]
        if u.get("mask") is not None:
            item["IsMasked"] = bool(u["mask"])
            # 掩码文本=变量名称仅保留中文/字母/数字（与单条工具同口径）
            item["MaskContent"] = (mask_text_from_name(target.get("name"))
                                   if u["mask"] else "")
        if len(item) == 1:
            raise RuntimeError(f"update 的 {key} 至少需要修改一项"
                               "（name/variable_type/is_required/is_preset"
                               "/description/mask）")
        update_items.append(item)

    delete_ids: list[int] = []
    for key in delete_keys:
        target = by_key.get(key)
        if target is None:
            raise RuntimeError(f"delete 的变量 {key} 不存在")
        delete_ids.append(target["id"])

    result = _do_modify(script_id, add=add_items, update=update_items,
                        delete_ids=delete_ids)
    result.update({
        "added": len(add_items), "updated": len(update_items),
        "deleted": len(delete_ids),
        "hint": ("批量变量修改已保存（未发布时保留为草稿，不影响已发布"
                 "版本）；发布为独立操作——仅在用户明确要求时调用 "
                 "publish_preview，勿为生效自动发布"),
    })
    return result


@mcp.tool()
@tool_with_detail
def set_preview_variables(script_id: str, values: dict,
                          env: str = "preview") -> dict:
    """全局变量赋值（修改变量取值，全量提交仅改目标项；支持测试/线上版本）。

    - script_id: 剧本ID（llm_xxx）
    - values: {调用名称: 新值}，值统一按字符串提交（如 {"coupon_a_lock_term": 3}）
    - env: "preview"=测试版本（默认）/"online"=线上版本（2026-10-13 新增：
      同端点 body 键为 OnlineVariables，**赋值即时生效于线上外呼、无需
      发布**——⚠ 生产变更：直接影响实际外呼通话的变量值，受修改守卫
      控制请谨慎使用；线上变量需剧本已发布上线）
    ⚠ 必填变量（is_required=true）赋值时值不能为空——传空值会被拒绝。
    ⚠ 对话测试只需为**前置传入=是**的变量赋值（2026-10-11 实测：控制台
    "变量赋值"面板只显示前置传入=是的变量，必填的未赋值会阻断开始测试）；
    给前置传入=否的变量赋值服务端仍会保存，但控制台赋值面板不显示
    （返回 non_preset 提示）。
    修改前/后值写入 result 目录（before.md/after.md，\t 分隔，
    含变量类型数值与字符串形式）。
    文本对话测试（start_dialog）默认使用测试版本的值。
    """
    client = _get_client()
    if env == "online":
        result = client.set_online_variables(script_id, values)
        out_dir = new_result_dir("线上版本全局变量赋值")
    elif env == "preview":
        result = client.set_preview_variables(script_id, values)
        out_dir = new_result_dir("测试版本全局变量赋值")
    else:
        raise RuntimeError("env 必须是 preview（测试版本）或 online"
                           f"（线上版本），收到: {env!r}")
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
        "non_preset": result.get("non_preset") or [],
        "non_preset_hint": (
            "以下变量前置传入=false：控制台\"变量赋值\"面板不显示（对话测试"
            "无需为它们指定值），本次赋值已保存但控制台不可见: "
            + ", ".join(result.get("non_preset") or []))
        if result.get("non_preset") else "",
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
      全局变量的同名变量（不改测试版本全局变量）；未指定时使用剧本测试版本全局变量的当前值。
      对话测试**只需为前置传入=是的变量指定值**（2026-10-11 实测：context
      省略前置传入=否的变量对话行为完全一致；必填变量缺值不报错但话术
      渲染为空，赋值环节才校验必填）
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


# ---------------------------------------------------------------- 通话测试（真实拨号，2026-10-12 新增）

@mcp.tool()
@tool_with_detail
def start_call_test(script_id: str, phone: "str | list[str]",
                    env: str = "preview",
                    variables: dict | None = None,
                    wait_result: bool = True) -> dict:
    """发起通话测试（控制台「测试与发布 → 通话测试」；真实拨打电话）。

    ⚠ 真实拨打被叫号码（占用生产外呼资源）：生产剧本慎用，建议在
    测试项目组剧本上验证；同一号码短时间内勿重复发起。

    - script_id: 剧本ID（llm_xxx）
    - phone: 被叫号码（单个字符串或列表；主叫号码恒为平台默认）
    - env: "preview"=使用测试版本（默认）/"online"=使用线上版本
      （剧本从未发布上线时报错）
    - variables: 可选 {调用名称: 值}，指定本次通话使用的变量值——
      仅 env=preview 支持（先赋值再发起，值经回调接口注入；等价于
      页面"全局变量赋值"后发起）；env=online 时传值报错（线上变量
      来自发布时的快照，不能在发起时指定）
    - wait_result: True（默认）发起后轮询通话明细（call_list）至本次
      记录出现（几秒~十几秒）并返回接通/SIP/轮次/评级/CallID；False 仅
      提交立即返回（发起接口本身不返回通话ID，结果需另行查询）。
    返回 {env, phone_numbers, submitted_at, call_records, timed_out}。
    修改类操作：需全局配置允许修改（同其他修改类工具）。
    """
    result = _get_client().start_call_test(
        script_id, phone, env=env, variables=variables,
        wait_result=wait_result)
    out_dir = new_result_dir("通话测试")
    write_json(out_dir / "call_test.json", result)
    records = result.get("call_records") or []
    note = ""
    if result.get("timed_out"):
        note = (f"超时未匹配到全部通话记录（{len(records)}/"
                f"{len(result['phone_numbers'])} 条）；可稍后用 "
                "query_call_records（按项目组名+剧本ID+提交时间附近的"
                "时间范围）查询，Env 字区分 " + result["env"])
    return {
        "script_id": result["script_id"],
        "env": result["env"],
        "phone_numbers": result["phone_numbers"],
        "variables": result["variables"],
        "submitted_at": result["submitted_at"],
        "call_records": records,
        "timed_out": result["timed_out"],
        "result_file": str(out_dir / "call_test.json"),
        "note": note,
    }


# ---------------------------------------------------------------- 获取当前登录的账号

@mcp.tool()
@tool_with_detail
def get_current_user() -> dict:
    """获取当前登录的账号（GET /console/api/v2/user，id 即账号）。

    返回 {account(id), username, email, type}。
    说明：所有其他工具执行请求前会自动做修改守卫——修改类操作需要
    全局配置中「是否允许执行修改操作」开关已开启（默认关闭，人工在
    配置页设置），且当前登录账号为允许的账号（同一 Cookie 未变化时
    免重复检查）；开关未开启或账号未配置/不匹配时修改类操作会报错
    提示先在配置页设置。查询类操作不受影响。
    """
    user = _get_client().get_current_user()
    return {"account": str(user.get("id")), "username": user.get("username"),
            "email": user.get("email"), "type": user.get("type")}


# ---------------------------------------------------------------- 剧本基本信息/Sub Agent/分析Agents（2026-09-06 新增）

@mcp.tool()
@tool_with_detail
def query_script_info(script_id: str) -> dict:
    """查询剧本基本信息（汇总多接口，一次返回全字段）。

    返回：项目组、剧本ID/名称、剧本类型（1=对话流程编排 Agent 2=纯PE型
    Agent 3=Multi Agents；2026-10-10 修正此前 1/2/3 错位）、最大对话轮次、
    最大模型出错次数、
    Agent 回复自动挂机关键词（;分隔）、LLM 模型、
    语音识别（ASR）设置-引用热词表、语音识别（ASR）设置-上传上下文
    （开启/未开启）、**非真人接听识别开关（answer_recognize_enabled
    开启/未开启）与识别后播报内容（answer_recognize_text，三种剧本类型
    均返回，页面 UI 仅在对话流程编排提供编辑入口）**、
    挂载的分析Agents（信息抽取/线索定级/通话总结，
    各含 ID/名称/状态/更新时间）、测试版本与线上版本发布
    （版本号/状态/更新时间）、**剧本修改时间（modify_time）与
    修改后是否有发布（modified_published：剧本修改时间<=线上版本
    发布时间+60秒容差=是；否=修改晚于线上发布或从未发布上线——
    发布动作本身会触碰修改时间 +2~+7 秒，容差覆盖该扰动）**。
    结果同时写入 result/{日期}/{时间_账号_查询剧本基本信息}/。
    """
    info = _get_client().get_script_info(script_id)
    import datetime
    for a in (info.get("analysis_agents") or {}).values():
        ts = a.get("update_time")
        a["update_time_text"] = (
            datetime.datetime.fromtimestamp(int(ts) / 1000).strftime(
                "%Y-%m-%d %H:%M:%S") if ts else "")
    # 发布时间格式化文本（ISO -> YYYY-MM-DD HH:MM:SS）
    for key in ("preview_publish", "online_publish"):
        p = info.get(key) or {}
        p["update_time_text"] = fmt_launch_time(p.get("update_time"))
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
    提示词可达 60KB，全文落盘 result/{日期}/{时间_账号_查询SubAgent配置}/，
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

    提示词全文落盘 result/{日期}/{时间_账号_获取分析Agent内容}/{AgentID}.json（含
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
def query_call_records(group_name: str, script_id: str,
                       date_start: str, date_end: str,
                       grading: str | None = None,
                       labeled_grading: str | None = None,
                       duration_op: str | None = None,
                       duration_value: int | None = None,
                       duration_min: int | None = None,
                       duration_max: int | None = None,
                       answer_recognize: str | None = None,
                       search_id: str | None = None,
                       search_type: str | None = None,
                       env: str | None = None,
                       is_connected: bool | None = None,
                       task_ids: str | None = None,
                       order_by: str | None = None,
                       sort_type: int | None = None,
                       page_index: int | None = 0,
                       all_pages: bool = False,
                       page_size: int = 100,
                       include_records: bool = False) -> dict:
    """查询通话明细（外呼通话记录，POST llm/call_list）。

    参数（与通话明细页筛选项一致）：
    - group_name / script_id / date_start / date_end：必填（项目组名、
      剧本ID、起止时间 "YYYY-MM-DD HH:MM:SS"）；
    - grading：可选，意向等级过滤（非固定值，如 A/B/高意愿；多个用
      半角逗号连接）；
    - labeled_grading：可选，人工标记等级过滤；
    - duration_op：可选，"大于等于"/"小于"/"介于"，配 duration_value
      或 duration_min/max（单位秒）；
    - answer_recognize：可选，"是"/"否"/"未启用识别能力"（命中语音助手
      过滤，对应 AnswerRecognizeType 2/1/0——2026-11-03 修正原反向映射：
      2=是、1=否、0=未启用识别能力；"是"即非真人接听（AI 助理/秘书台/
      自动应答），"否"即真人接听）；
    - search_id / search_type：可选，页面「通话id/被叫号码」搜索；
      search_type="parent_or_session_id"（通话id，默认）或
      "phone_number"（被叫号码）；
    - env：可选，"preview"（测试）/"online"（线上）；
    - is_connected：可选，True=接通 / False=未接通；
    - task_ids：可选，任务ID（页面「任务名称」对应的 TaskIDs）；
    - order_by / sort_type：可选，排序字段 Duration/Rounds 与方向
      （1=升序、2=降序）；
    - page_index：分页页号（**0 基**，默认 0=只查第一页；返回带
      total_pages 供翻页）；
    - all_pages：True 时自动翻页收集全部（受服务端约 1 万条上限约束，
      触达时返回 truncated/warning）；
    - page_size：分页大小（默认 100）；
    - include_records：默认 false，返回只给 total/count + 前 5 条
      sample + result_file；传 true 才返回完整 records（B11）。
    结果落盘 result/{日期}/{时间_账号_查询通话明细}/records.json + md。
    """
    client = _get_client()
    r = client.query_call_records(
        group_name=group_name, script_id=script_id,
        date_start=date_start, date_end=date_end,
        grading=grading, labeled_grading=labeled_grading,
        duration_op=duration_op,
        duration_value=duration_value, duration_min=duration_min,
        duration_max=duration_max, answer_recognize=answer_recognize,
        search_id=search_id, search_type=search_type, env=env,
        is_connected=is_connected, task_ids=task_ids,
        order_by=order_by, sort_type=sort_type,
        page_size=page_size, page_index=page_index,
        all_pages=all_pages)
    out_dir = new_result_dir("查询通话明细")
    # 请求参数全量记录（prompt 2026-09-07：有指定的请求参数都要记录；
    # 未指定的可选项标注"未指定"）
    def _v(x):
        return "未指定" if x is None else x

    params = {
        "项目组": group_name,
        "剧本": script_id,
        "开始时间": date_start,
        "结束时间": date_end,
        "意向等级": _v(grading),
        "人工标记等级": _v(labeled_grading),
        "通话时长-判断方式": _v(duration_op),
        "通话时长-数值": _v(duration_value),
        "通话时长-数值-最小": _v(duration_min),
        "通话时长-数值-最大": _v(duration_max),
        "命中语音助手": _v(answer_recognize),
        "搜索类型": _v(search_type),
        "搜索值": _v(search_id),
        "环境类型": _v(env),
        "接通状态": ("未指定" if is_connected is None
                    else ("接通" if is_connected else "未接通")),
        "任务ID": _v(task_ids),
        "排序字段": _v(order_by),
        "排序方向": _v(sort_type),
        "分页-页号": ("全部收集" if all_pages
                      else f"第 {0 if page_index is None else page_index} 页（0 基）"),
        "分页-页大小": page_size,
        "是否收集全部": all_pages,
    }
    r = dict(r, request_params=params)
    write_json(out_dir / "records.json", r)
    md = ["# 查询通话明细 " + script_id, "", "## 请求参数", ""]
    md += [f"- {k}：{v}" for k, v in params.items()]
    md += ["", f"- 总数：{r['total']}", f"- 本次收集：{r['count']} 条",
           "", "## 通话记录", "",
           "\t".join(["被叫号码", "主叫号码", "接通状态", "SIP状态码",
                      "通话时长/秒", "交互轮次", "环境类型", "命中语音助手",
                      "意向等级", "信息抽取", "短信状态", "创建时间",
                      "通话ID"])]
    for rec in r["records"]:
        md.append("\t".join(
            " ".join(str(rec.get(c, "") or "").split()) for c in
            ["被叫号码", "主叫号码", "接通状态", "SIP状态码", "通话时长/秒",
             "交互轮次", "环境类型", "命中语音助手", "意向等级", "信息抽取",
             "短信状态", "创建时间", "通话ID"]))
    (out_dir / "records.md").write_text(
        "\n".join(md) + "\n", encoding="utf-8")
    out = {
        "total": r["total"], "count": r["count"],
        "request_params": params,
        "result_file": str(out_dir / "records.md"),
        "result_json": str(out_dir / "records.json"),
        "note": "记录已全量落盘（含请求参数）；大数据量时按需读取文件",
    }
    for key in ("truncated", "warning", "note"):
        if key in r:
            out[key] = r[key]
    if include_records:
        out["records"] = r["records"]
    else:
        out["sample"] = r["records"][:5]
        out["note"] = (out["note"] + "；默认仅返回前 5 条样例，"
                       "include_records=true 返回完整数组")
    return out


@mcp.tool()
@tool_with_detail
def get_call_transcripts(
        call_id: str | None = None,
        group_name: str | None = None,
        script_id: str | None = None,
        date_start: str | None = None,
        date_end: str | None = None,
        search_id: str | None = None,
        search_type: str | None = None,
        grading: str | None = None,
        labeled_grading: str | None = None,
        env: str | None = None,
        duration_op: str | None = None,
        duration_value: int | None = None,
        duration_min: int | None = None,
        duration_max: int | None = None,
        answer_recognize: str | None = None,
        is_connected: bool | None = None,
        task_ids: str | None = None,
        order_by: str | None = None,
        sort_type: int | None = None,
        output_dir: str = "",
        page_size: int = 100,
        limit: int | None = 100,
        skip_existing: bool = True,
        workers: int = 4,
        include_call_info: bool = True,
        include_line_meta: bool = True,
        analyze: bool = False,
        keywords: str | None = None) -> dict:
    """导出通话文本（对话记录）：每条通话写入 <output_dir>/<通话ID>.md。

    两种输入方式（二选一）：
    1. call_id：直接按通话ID（或 SessionID）定位单条记录；建议同时传
       script_id（服务端 Schema 只接受具体剧本ID，传 script_id 可避免
       逐剧本枚举；不传时自动按项目组枚举剧本尝试）；
    2. 筛选条件：group_name + script_id（+ date_start/date_end 等），
       查询满足条件的全部记录并逐条导出；date_start/date_end 省略时
       默认最近 30 天。筛选参数与 query_call_records 完全一致
       （grading/labeled_grading/duration_*/answer_recognize/search_*/
       env/is_connected/task_ids）。

    output_dir：可选。不传时默认
    `result/{日期}/{时间_账号_查询通话文本}/{HHMMSS_短uuid}/`。
    相对路径按项目根目录解析。
    输出：
    - `include_call_info=true`（默认）：头部含通话元数据；
      `false` 时只写对话行；
    - `include_line_meta=true`（默认）：每行带
      `[第N轮 00:12.4→00:19.2 6.8s 打断成功]` 前缀；
    - `limit`：最多导出条数（MCP 默认 100；CLI 默认不限）；
      `skip_existing=true`（默认）跳过已存在文件；
      `workers` 并发数（默认 4）；
    - `analyze=true`：额外输出 summary.json/summary.md（关键词等统计）；
    - 导出目录固定写 `manifest.json`，另写 `query.json` 记录本次参数。

    ⚠ 「机器人:」话术里出现 `{{企业名称}}`/`{{客户姓氏}}`/`{{坐席工号}}`/
    `{{企业贷授信额度}}` 这类**双花括号占位符属正常情况**：生产账号出于
    客户信息安全要求，话术变量**只回显变量名、不展示实际取值**（这是平台的
    安全策略，不会改变，也无法通过接口取到值）。不要把占位符误判为“变量未
    渲染/数据异常”，也不要据此推断客户实际听到的内容。

    ⚠ 通话明细「操作」列为「数据已清除」（IsDataPruned=true）的记录
    没有 SessionURL/对话数据，不会生成 md，会列入 skipped 返回。
    结果同时返回 exported/skipped/failed 明细。
    """
    out_raw = str(output_dir or "").strip()
    if not out_raw:
        base = new_result_dir("查询通话文本")
        out_dir = base / (datetime.datetime.now().strftime("%H%M%S")
                          + "_" + uuid.uuid4().hex[:6])
    else:
        out_dir = Path(out_raw).expanduser()
        if not out_dir.is_absolute():
            out_dir = (_ROOT / out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    client = _get_client()
    matched_total = 1
    truncated = False
    warning = ""
    if call_id:
        # Schema 必须是具体剧本ID（传 "*" 会 403）；script_id 可选，
        # 省略时按项目组枚举剧本逐个尝试
        record = client.find_call_record(call_id, group_name, script_id)
        records = [record]
        query_desc = f"call_id={call_id}"
    else:
        if not group_name or not script_id:
            raise RuntimeError(
                "按筛选条件导出时 group_name 与 script_id 必填；"
                "或改用 call_id 指定单条通话")
        ds, de = date_start, date_end
        if not ds or not de:
            end = datetime.datetime.now()
            start = end - datetime.timedelta(days=29)
            ds = ds or start.strftime("%Y-%m-%d 00:00:00")
            de = de or end.strftime("%Y-%m-%d 23:59:59")
        date_start, date_end = ds, de   # query.json 记录实际生效时间
        ob = {"create_time": None, "duration": "Duration",
              "rounds": "Rounds"}.get(order_by or "create_time", order_by)
        st = sort_type if sort_type is not None else 2
        r = client.query_call_records(
            group_name=group_name, script_id=script_id,
            date_start=ds, date_end=de,
            grading=grading, labeled_grading=labeled_grading,
            duration_op=duration_op, duration_value=duration_value,
            duration_min=duration_min, duration_max=duration_max,
            answer_recognize=answer_recognize,
            search_id=search_id, search_type=search_type, env=env,
            is_connected=is_connected, task_ids=task_ids,
            order_by=ob, sort_type=st if ob else None,
            raw=True, page_size=(min(page_size, int(limit))
                                if limit is not None and limit > 0
                                else page_size),
            max_pages=200, all_pages=True,
            max_records=(int(limit) if limit is not None else None))
        records = r.get("records") or []
        matched_total = r.get("total") or len(records)
        truncated = bool(r.get("truncated"))
        warning = r.get("warning") or r.get("note") or ""
        query_desc = (f"group={group_name}, script={script_id}, "
                      f"{ds}~{de}")

    keywords_list = ([k.strip() for k in str(keywords).split(",")
                      if k.strip()] if keywords else None)
    query_params = {
        "call_id": call_id, "group_name": group_name,
        "script_id": script_id, "date_start": date_start,
        "date_end": date_end, "search_id": search_id,
        "search_type": search_type, "grading": grading,
        "labeled_grading": labeled_grading, "env": env,
        "duration_op": duration_op, "duration_value": duration_value,
        "duration_min": duration_min, "duration_max": duration_max,
        "answer_recognize": answer_recognize,
        "is_connected": is_connected, "task_ids": task_ids,
        "order_by": order_by, "sort_type": sort_type,
        "limit": limit, "skip_existing": skip_existing,
        "workers": workers, "include_call_info": include_call_info,
        "include_line_meta": include_line_meta,
        "analyze": analyze, "keywords": keywords_list,
        "output_dir": str(out_dir),
        "exported_at": datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"),
    }
    (out_dir / "query.json").write_text(
        json.dumps(query_params, ensure_ascii=False, indent=2),
        encoding="utf-8")

    result = export_transcripts(
        client, records, out_dir, skip_existing=skip_existing,
        workers=workers, include_call_info=include_call_info,
        include_line_meta=include_line_meta,
        analyze=analyze, keywords=keywords_list, limit=limit,
        matched_total=matched_total, manifest_extra=query_params)
    result.update({
        "output_dir": str(out_dir),
        "query": query_desc,
        "query_file": str(out_dir / "query.json"),
        "truncated": truncated,
        "note": ("每条通话写入 <output_dir>/<通话ID>.md；对话行以 "
                 "机器人:/客户: 区分说话人；数据已清除的记录不生成文件；"
                 "total/fetched=本次处理条数，matched_total=命中总数。"
                 + ("；结果不完整: " + warning if warning else "")),
    })
    if len(result["files"]) > 500:
        result["files_note"] = (f"files 仅返回前 500 条，"
                                f"实际导出 {len(result['files'])} 条")
        result["files"] = result["files"][:500]
    return result


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
        # 两层结构（prompt 2026-09-07 result/{日期}/...），兼容旧一级
        if not RESULT_DIR.is_dir():
            return []
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
        return sorted(found, reverse=True)

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
            # 兼容目录名（旧一级）与 日期层/目录名（新结构）
            for cand in ([RESULT_DIR / export_dir]
                         + [RESULT_DIR / day / export_dir
                            for day in sorted(
                                (d.name for d in RESULT_DIR.iterdir()
                                 if d.is_dir() and d.name.isdigit()),
                                reverse=True)]):
                if cand.is_dir():
                    p = cand
                    break
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

    # 兼容不同 mcp SDK 版本：旧版 FastMCP.sse_app(host=...) 支持 host，
    # 新版（实测 2026-11-03 的 1.x/2.x）只接受 mount_path/无参——
    # 传 host 会直接 TypeError 导致服务起不来（表现为端口始终不监听）。
    try:
        sse_app = mcp.sse_app(host=args.host)
    except TypeError:
        sse_app = mcp.sse_app()
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