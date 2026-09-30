# 提供给AI的安装说明

按本文档完成 volcengine_aibot_scripts（火山引擎智能外呼 剧本操作与文本测试工具）从环境准备、项目安装到验证的全部安装步骤。本文档面向执行安装的 AI：用户已将本文件路径提供给你，按顺序执行，每步均给出执行命令与成功判定；需要人工介入的环节会明确标注「人工步骤」。

适用环境：Windows 10、Windows 11。

本工具把火山引擎智能外呼控制台的网页操作转换为 Python 脚本与 MCP 服务，安装完成后可通过自然语言让 AI 完成剧本的查询、修改、导入、发布与对话测试闭环。

# 安装前置处理（必须先完成）

本项目依赖 chrome_capture_operate（向本项目提供火山引擎网站登录 Cookie），先按其在线安装说明完成安装（含 Python、Chrome、Chrome 插件、Cookie 推送服务）：

- https://raw.giteeusercontent.com/adrninistrator/chrome_capture_operate/raw/master/提供给AI的安装说明.md
（国内优先，实测下载速度明显更快）
- https://raw.githubusercontent.com/Adrninistrator/chrome_capture_operate/refs/heads/master/提供给AI的安装说明.md
（备用）

（若本机已存在 chrome_capture_operate 项目，直接读其根目录下的本地副本即可，无需下载。）

前置处理的成功判定（全部满足才可继续本文档后续步骤）：

1. `curl http://127.0.0.1:33445/health` 返回 `{"ok": true, ...}`；
2. Chrome 插件已推送 Cookie：`curl http://127.0.0.1:33445/api/cookies/receives` 返回的最近推送记录数量大于 0 且成功；
3. 日常使用的 Chrome 已登录火山引擎智能外呼控制台（https://console.volcengine.com/aibot）。

注意：本文档结构沿用参考文档，其中公共依赖步骤（Python/Chrome/插件/Cookie 服务）已由前置处理完成——环境检查通过即直接跳过，不重复展开。

# 安装内容

本项目为纯 Python 项目，安装后得到：

| 内容 | 说明 |
|---|---|
| Python 虚拟环境 `.venv` | install.bat 创建并安装依赖（requests / mcp / uvicorn / sse-starlette / websockets） |
| volc-aibot 服务 | pythonw 后台运行（无窗口）+ 系统托盘；同一端口同时提供 Web 配置页与 MCP SSE |
| Web 配置页 | http://127.0.0.1:{端口}/ （快捷工具、MCP接口、配置参数、使用说明等标签页） |
| MCP SSE 端点 | http://127.0.0.1:{端口}/sse （注册到 AI Agent 后由 AI 调用 28 个工具） |

端口默认 19000，读取自全局配置 `C:\Users\<用户名>\.volcengine_aibot_scripts\global.json` 的 `server_port`。

# 环境检查

公共依赖已由前置处理安装，此处仅确认本项目需要的条件：

|软件|检测命令|判定标准|
|---|---|---|
|Python|`python --version`（失败时试 `py -3 --version`）|输出 3.10 或更高版本，且 `python --version` 能直接执行（说明已在 PATH）|
|git|`git --version`|能输出版本（决定下载本项目的方式；无 git 时用 ZIP 压缩包下载，不影响安装结果）|

Chrome 与 chrome_capture_operate 的检查见「安装前置处理」的成功判定。

# 联网检查

下载本项目需要访问其仓库所在的 Git 平台，分目标分别探测：

```
curl -I --connect-timeout 5 https://gitee.com
curl -I --connect-timeout 5 https://github.com
```

- 记录可达性，仅用于判断能否下载本项目；**本项目的仓库下载地址由用户指定**（见「Git访问方式」），AI 不根据可达性自行挑选仓库来源；
- 安装依赖（pip）需要访问 PyPI 或其国内镜像，检测：`curl -I --connect-timeout 5 https://pypi.tuna.tsinghua.edu.cn`；
- 全部不可达（不能访问互联网）：提示人工下载项目压缩包并解压到指定目录（见「Git访问方式」的 ZIP 方式），AI 等待人工完成后继续。

# 软件下载

无。本项目无额外软件要下载——Python/Chrome 已由前置处理完成安装；Python 依赖包由 install.bat 安装（见「安装步骤」）。

# Git访问方式（下载本项目）

**下载目录必须由用户指定，AI 不要自己选择**——包括不得默认沿用 chrome_capture_operate 已有的目录；用户未给出时向用户询问后再执行。下载 chrome_capture_operate 时同样由用户指定。两个项目须下载到**同一个目录**中（即两个项目目录同级、位于同一父目录——两者协同工作）。

**仓库下载地址由用户指定，AI 不自行挑选**（本项目公开仓库参考：gitee.com/adrninistrator/volcengine_aibot_scripts、github.com/Adrninistrator/volcengine_aibot_scripts，仍以用户确认为准）：

- git 可用且用户指定的仓库可达时使用 git clone：

```
git clone <用户指定的仓库地址>
```

- 没有 git 或指定仓库不可达时，通过 HTTP 下载压缩包并解压（地址同样以用户指定为准，参考形式：gitee 仓库的 `…/repository/archive/master.zip`、github 仓库的 `…/archive/refs/heads/master.zip`）：

```
curl -L -o master.zip <用户指定仓库对应的压缩包下载地址>
```

解压：PowerShell 执行 `Expand-Archive -Path master.zip -DestinationPath <目标目录>`；解压出的目录名可能带 `-master` 后缀，重命名为 `volcengine_aibot_scripts`。

# 源地址

install.bat 内的 pip 安装默认使用官方源；国内网络下如速度过慢，先执行以下命令切换清华镜像，再运行 install.bat：

```
python -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

（该配置写入当前用户的 pip 配置，对本项目虚拟环境内的安装同样生效。）

# 安装步骤

## 1. 安装依赖

进入项目目录，运行 `install.bat`（创建虚拟环境 `.venv` 并安装依赖，只需执行一次）。

成功判定（无输出即成功）：

```
.venv\Scripts\python.exe -c "import requests, mcp, uvicorn, sse_starlette, websockets"
```

## 2. 启动服务

运行 `start.bat`：使用 pythonw 后台运行（不弹出应用窗口），桌面右下角出现系统托盘图标（双击打开配置页，右键菜单可退出）。start.bat 会做端口占用预检与启动自检（15 秒内等待端口监听），失败时提示查看 `log\startup.log` / `log\startup_err.log`。

成功判定：

```
curl http://127.0.0.1:19000/api/config
```

返回 200 且为 JSON（含 `server_port` / `allow_mutation` / `allowed_account` 等字段）。

## 3. Chrome 插件配置（最终配置以本节为准）

前置处理文档允许「全部允许」与「按指定范围推送」二选一，本文档收紧为按指定范围推送（最小权限）。将 Cookie 推送范围设置为「按指定范围推送」，清单含 `*.volcengine.com`：

```
curl -X POST http://127.0.0.1:33445/api/extension/config -H "Content-Type: application/json" -d "{\"push_scope\": \"list\", \"allow_list\": [\"*.volcengine.com\"]}"
```

接口会自动回读验证，返回 ok 即生效。若前置处理阶段已选「全部允许」也能工作，只是覆盖面更大；若用户还需用 chrome_capture_operate 抓其他网站的 Cookie，在清单中追加对应域名即可。接口不可用时，引导用户在 chrome_capture_operate 的 Web 页面人工配置（页头**Chrome插件设置**按钮）。

## 4. 修改守卫配置（人工步骤，必须）

修改类操作（发布剧本测试版本、剧本变量修改、测试版本全局变量赋值、删除剧本）执行前会先检查「是否允许执行修改操作」开关，再校验当前登录账号是否为允许账号——**两项都只允许人工修改，AI 不得经接口代改**（避免用错环境）。不配置的后果：所有修改类操作被拒绝（查询类不受影响）。

引导用户完成（AI 可代做准备工作，最终勾选/填写由用户操作）：

1. AI 打开配置页：执行 `start "" http://127.0.0.1:19000/`（或请用户双击系统托盘图标），切到「配置参数」标签页；
2. AI 可先查询当前 Chrome 登录的火山引擎账号并告知用户参考：

```
curl -X POST http://127.0.0.1:19000/api/account
```

3. 「人工步骤」用户勾选「**是否允许执行修改操作**」（默认关闭，开启后修改类操作才放行）；
4. 「人工步骤」用户填写「**允许执行修改操作的账号**」（只允许一个；建议配置**测试环境**账号：在测试环境完成剧本修改后再人工同步到生产环境，保证生产环境账号数据不被 AI 误修改），保存。

成功判定：

```
curl http://127.0.0.1:19000/api/config
```

返回的 `allow_mutation` 为 `true` 且 `allowed_account` 非空。

## 5. MCP 服务注册

volc-aibot 的 MCP 服务需要注册到当前 AI Agent：

- Claude Code：

```
claude mcp add --scope user --transport sse volc-aibot http://127.0.0.1:19000/sse
```

- 其他 AI Agent：按其 MCP 配置方式做等效配置（SSE 地址 `http://127.0.0.1:{端口}/sse`，端口以全局配置 `server_port` 为准）。

（注意：参考文档中 chrome_capture_operate 的 MCP 由用户自行配置、不执行注册命令；本项目不同，volc-aibot 必须注册后 AI 才能调用其 28 个工具。）

## 6. 系统自启动（可选）

默认不开启。开启后开机自动以 pythonw 后台运行本服务（无窗口，系统托盘可见），端口跟随全局配置。AI 可经 HTTP 接口查询与设置：

```
curl http://127.0.0.1:19000/api/autostart
curl -X POST http://127.0.0.1:19000/api/autostart -H "Content-Type: application/json" -d "{\"enabled\": true}"
```

（关闭传 `"enabled": false`；与配置页勾选「系统自启动」等效。注意项目目录拷贝/移动后自启动不会跟随，需在新目录重新开启，旧目录残留项会自动清理。）

# 验证

全部安装完成后的验证（每项给出成功判定）：

1. **本服务**：`curl http://127.0.0.1:19000/api/config` 返回 200（端口以全局配置实际值为准）；
2. **Cookie 链路**：`curl http://127.0.0.1:33445/api/cookies/receives` 返回的最近推送记录数量大于 0 且成功（若为空，提示用户在插件「参数配置」标签页点击**立即推送一次**，或等待自动推送）；
3. **修改守卫已配置**：`curl http://127.0.0.1:19000/api/config` 返回的 `allow_mutation` 为 `true` 且 `allowed_account` 非空（对应人工步骤 4）；
4. **MCP 已注册**：Claude Code 执行 `claude mcp list` 应列出 volc-aibot；其他 Agent 按各自方式确认 MCP 配置生效；
5. **整条链路**：在 Claude Code 中用自然语言发起一次只读调用，例如「使用 volc-aibot MCP：先调用 usage_guide 工具了解怎么用，然后查询全部项目组」，能返回项目组列表即链路通畅（对话测试类调用会占用生产实际外呼资源，验证链路请勿使用）。

验证全部通过后，安装完成。使用方式见项目 README.md 与配置页「使用说明」「适用场景」「提示词示例」标签页。
