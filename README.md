# 火山引擎智能外呼 剧本操作与文本测试工具集

把火山引擎智能外呼控制台（console.volcengine.com/aibot）的网页功能转换为 Python 脚本与 MCP 服务，供 AI 通过自然语言完成剧本的查询、修改、导入、发布与对话测试闭环。

**17 个功能脚本 + Web 快捷工具**：获取当前登录账号、查询项目组、查询项目组下的剧本、按剧本ID查询、按名称搜索、导出剧本、导入剧本、发布测试版本、查询剧本变量、变量增删改、测试版本变量赋值、文本对话测试、查询剧本基本信息（含 Sub Agent）、查询分析Agent、获取分析Agent内容、批量导出剧本、搜索已导出剧本内容关键字；Web 快捷工具：批量查询剧本信息、批量搜索剧本内容、批量下载搜索分析Agent。

## 目录结构

```
volcengine_aibot_scripts/
├─ volc_aibot/                  # 公共包（所有脚本与 MCP 共用）
│  ├─ client.py                 # 控制台 API 客户端（全部接口封装+账号守卫）
│  ├─ cookie_client.py          # chrome_capture_operate Cookie 查询客户端
│  ├─ global_config.py          # 全局配置（~/.volcengine_aibot_scripts/global.json）
│  ├─ web_server.py             # Web 配置页（与 MCP SSE 同端口）
│  ├─ tray.py                   # 系统托盘（双击开页面/右键退出，无窗口运行）
│  ├─ config.py                 # 常量（基址/产品线/变量类型表）
│  ├─ logging_util.py           # 日志（log/ 每天一个文件）
│  └─ result.py                 # 结果目录（result/{时间_账号_功能}/）
├─ scripts/                     # 17 个独立可执行脚本（一功能一脚本）
│  ├─ get_current_user.py       # 获取当前登录的账号（--save-allowed 写配置）
│  ├─ query_project_groups.py   # 查询项目组
│  ├─ list_group_scripts.py     # 查询项目组下的剧本
│  ├─ query_script.py           # 根据剧本ID查询剧本
│  ├─ search_script.py          # 根据剧本名称搜索剧本
│  ├─ export_script.py          # 导出剧本
│  ├─ import_script.py          # 导入剧本
│  ├─ publish_preview.py        # 发布剧本测试版本
│  ├─ query_variables.py        # 查询剧本变量
│  ├─ modify_variables.py       # 剧本变量新增/修改/删除
│  ├─ set_preview_variables.py  # 测试版本全局变量赋值
│  ├─ text_chat_test.py         # 文本对话测试（固定话术文件模式）
│  ├─ query_script_info.py      # 查询剧本基本信息（含 Sub Agent/分析Agents）
│  ├─ query_analysis_agents.py  # 查询分析Agent列表（CloudLadder 域）
│  ├─ get_analysis_agent.py     # 获取分析Agent内容（提示词/状态/模型）
│  ├─ batch_export_scripts.py   # 批量导出剧本（按项目组/全部）
│  └─ search_exported_scripts.py # 搜索已导出剧本内容关键字
├─ mcp_server.py                # 服务入口（同端口：Web 配置页 + MCP SSE；托盘）
├─ md/                          # 文档（Web 页面内容：使用说明/适用场景/提示词示例；非技术人员AI协助部署指南）
├─ install.bat                  # 安装依赖（虚拟环境 .venv）
├─ start.bat                    # 启动服务（pythonw 无窗口 + 系统托盘）
├─ requirements.txt             # Python 依赖
├─ queries.txt                  # 文本对话测试示例话术（每行一句）
├─ log/                         # 运行日志（每天一个文件，运行时生成）
└─ result/                      # 结果输出（每次运行一个子目录，运行时生成）
```

完整文档（项目架构、火山引擎接口协议、用法说明）在服务页面的「使用说明」等标签页中查看（启动后浏览器打开）。

非技术人员部署：电脑上未安装 Git、Python 时，将 `md/非技术人员AI协助部署指南.md` 的**文件路径**发给 AI（如 Claude Code）即可，AI 可自行读取该文档并按其协助完成部署，无需复制全文。文档包含已实测验证的下载地址与国内镜像（Python 安装包、项目 ZIP、pip 依赖）、AI 可自动完成与必须人工完成的步骤划分（人工仅 3 项：安装 Chrome 插件、配置 Cookie 推送范围、登录火山引擎控制台）。

## 快速开始

### 1. 安装依赖

```bat
install.bat          # 创建/复用 .venv 并安装 requests/mcp/uvicorn/sse-starlette/websockets
```

### 2. 运行前提（关键）

- **chrome_capture_operate 服务**已启动（默认 `http://127.0.0.1:33445`），Chrome 插件已安装且允许推送 `volcengine.com` 的 Cookie；
- **日常 Chrome** 已登录火山引擎智能外呼控制台。脚本不写死任何 Cookie，登录态每次运行时实时查询获取。

chrome_capture_operate 的安装与配置说明见其项目 README（github.com/Adrninistrator/chrome_capture_operate 或 gitee.com/adrninistrator/chrome_capture_operate）。

### 3. 运行脚本（示例）

```bat
.venv\Scripts\python.exe scripts\query_script.py llm_xxx
.venv\Scripts\python.exe scripts\export_script.py llm_xxx
.venv\Scripts\python.exe scripts\publish_preview.py llm_xxx --description "发布测试"
.venv\Scripts\python.exe scripts\text_chat_test.py llm_xxx queries.txt
```

（`llm_xxx` 替换为实际的剧本ID。）脚本可在任意目录下执行（内部自动把项目根加入 sys.path，PyCharm 与命令行包引用均正确）；每个脚本支持 `--help` 查看参数。

### 4. 启动服务（pythonw 无窗口 + 系统托盘；同端口 Web 配置页 + MCP SSE）

```bat
start.bat                              # 默认端口（全局配置，缺省 19000）
start.bat 19001                        # 本次覆盖端口
```

- 系统托盘：**双击打开配置页**（http://127.0.0.1:19000/），右键菜单可退出；
- 配置页（与 MCP SSE 同端口）：**快捷工具**（批量查询剧本信息、批量搜索剧本内容、批量下载搜索分析Agent，结果支持复制与导出 Excel（首行冻结+筛选，文件名含导出时间）；页面上方可折叠的执行状态面板经 WebSocket 实时展示当前请求、剧本ID/名称与百分比进度，完成时提醒）、设置**监听端口**（改后需重启）与
 **允许操作的账号**（唯一），显示当前可用的 MCP SSE URL（带复制按钮）；
- 全局配置文件：`C:\Users\<用户名>\.volcengine_aibot_scripts\global.json`；
- **账号守卫**：修改类操作执行前校验当前 Chrome 登录账号（/console/api/v2/user
 的 id）是否为允许账号，不一致拒绝执行；同一 Cookie 未变化时免重复检查；未配置账号时提示先到配置页设置（可用 `scripts\get_current_user.py --save-allowed` 直接写入当前账号）。

### 5. 安装 MCP 服务到 Claude Code

```bash
claude mcp add --scope user --transport sse volc-aibot http://127.0.0.1:19000/sse
```

MCP 服务共 26 个工具（含 `get_current_user` 账号工具与 `usage_guide` 使用说明工具——返回运行前提、各工具用法与典型调用序列，AI 不确定怎么用时先调它）。

新增的剧本信息与分析 Agents 工具：`query_script_info`（剧本基本信息：类型/最大轮次/模型/ASR/分析Agents挂载/发布状态）、`get_sub_agents` / `get_sub_agent_info`（Multi Agents 剧本的 Sub Agent 清单与配置）、`query_analysis_agents` / `get_analysis_agent`（分析Agent 列表与内容：系统/用户提示词、发布状态）、`batch_export_scripts`（批量导出）与 `search_exported_scripts`（导出内容关键字搜索）。

对话测试提示词示例（⚠ 对话测试占用生产实际外呼资源：请勿同时发起大量会话，避免业务高峰，建议晚上测试——MCP 的 usage_guide 与 Web 配置页均有此提示）：

```
使用 volc-aibot MCP，先调用 usage_guide 工具了解怎么用，然后与机器人进行任意对话，对话5轮，剧本ID：llm_xxx
```

## 典型链路

- **改剧本变量并测试**：`query_variables.py` → `modify_variables.py --op update ...` → `publish_preview.py`（发布后生效）→ `text_chat_test.py`
- **复制剧本**：`export_script.py` → `import_script.py <文件> <项目组名>` → 得到新剧本ID → `publish_preview.py`（新剧本未发布，对话前必须发布）
- **变量赋值后对话**：`set_preview_variables.py --set 变量名=值` → `text_chat_test.py`（对话 context 默认使用测试版本变量值）
- **批量导出并检查关键字**：`batch_export_scripts.py`（按项目组导出全部剧本）→ `search_exported_scripts.py --dir <导出目录> --keyword <关键字> [--count]`（按行搜索是否出现/出现次数）
- **剧本配置体检**：`query_script_info.py llm_xxx`（类型/轮次/模型/ASR/分析Agents/发布状态一览）；Multi Agents 剧本可用 MCP `get_sub_agents`/`get_sub_agent_info` 查各 Sub Agent 的模型与提示词

## 说明

- **测试版本全局变量赋值**：必填变量（is_required=true）赋值时值不能为空，传空值会被拒绝；
- 修改类操作执行前会自动备份剧本与变量当前值到 result 目录；
- 运行产物（log/、result/）运行时生成，已默认 git 忽略。
