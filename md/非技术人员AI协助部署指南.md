# 非技术人员 AI 协助部署指南

本指南面向**非技术人员**，适用于「电脑上 Git、Python 都没有安装」的全新 Windows 电脑，说明如何借助 AI（如 Claude Code）完成本工具的部署，以及哪些步骤必须人工完成。

# 如何使用本文档

把**本文档的文件路径**发给 AI（AI 可直接读取文件内容，无需复制粘贴全文），并附一句话，例如：

```
请按照 D:\xxx\volcengine_aibot_scripts\md\非技术人员AI协助部署指南.md 这份文档帮我完成部署，
遇到「人工步骤」时停下来告诉我怎么操作，等我确认完成后再继续。
```

（`D:\xxx\volcengine_aibot_scripts` 替换为项目实际解压目录。）

AI 会按本文档中的「AI 执行清单」逐步操作（每条命令执行前会请求确认），到达检查点时停下等待人工完成。

# 本文要部署的工具

本工具（volcengine_aibot_scripts）把火山引擎智能外呼控制台的网页操作转换为脚本与 MCP 服务，之后可通过自然语言让 AI 完成剧本的查询、修改、导入、发布与对话测试。

部署前需要准备的软件共 3 个：

| 软件 | 用途 | 是否必须安装 |
|---|---|---|
| Python 3.10+ | 运行两个 Python 项目 | 必须，可由 AI 安装 |
| Git 项目 chrome_capture_operate | 向本工具提供火山引擎网站登录 Cookie | 必须 |
| Git 项目 volcengine_aibot_scripts（本工具） | 剧本操作与文本测试 | 必须 |

**Git 不需要安装**：所有代码都可以从网页直接下载 ZIP 压缩包获取，与 `git clone` 结果一致。

**Chrome 浏览器**：需要已安装（绝大多数电脑已具备），并在其中安装 chrome_capture_operate 的浏览器插件。

# 下载地址与国内镜像（已实测验证）

以下链接与速度在 2025-09 实测验证。**优先用国内地址**，官方境外地址仅在对应国内地址不可用时使用。

## Python 安装包（约 26MB）

| 来源 | 地址 | 实测结果 |
|---|---|---|
| 华为云镜像（推荐，国内快） | `https://mirrors.huaweicloud.com/python/3.12.6/python-3.12.6-amd64.exe` | **约 16 秒下载完成**，文件 26527368 字节，与官方一致 |
| python.org 官方 | `https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe` | 文件大小一致，国内网络下载超时（2 分钟未完成） |

> 华为云镜像目录 `https://mirrors.huaweicloud.com/python/` 同步了 Python 官网的全部版本，其他版本（如 3.11.x）把地址中的版本号替换即可。

## 两个项目代码 ZIP

| 来源 | 地址 | 实测结果 |
|---|---|---|
| Gitee（推荐，国内快） | `https://gitee.com/adrninistrator/volcengine_aibot_scripts/repository/archive/master.zip` | 约 3 秒，100KB |
| Gitee（推荐，国内快） | `https://gitee.com/adrninistrator/chrome_capture_operate/repository/archive/master.zip` | 约 3 秒，1.2MB |
| GitHub 备选 | `https://github.com/Adrninistrator/volcengine_aibot_scripts/archive/refs/heads/master.zip` | 约 3 秒，与 Gitee 内容一致 |
| GitHub 备选 | `https://github.com/Adrninistrator/chrome_capture_operate/archive/refs/heads/master.zip` | 约 3 秒，与 Gitee 内容一致 |

四个 ZIP 均校验完整可解压，解压后的根目录名为 `volcengine_aibot_scripts-master` 与 `chrome_capture_operate-master`。

## Python 依赖包（pip 安装来源）

pip 默认从官方 PyPI 下载依赖。国内网络下建议改用国内镜像（阿里云）：

| 来源 | 地址 | 实测结果（下载全部依赖） |
|---|---|---|
| 阿里云镜像（推荐） | `https://mirrors.aliyun.com/pypi/simple/` | **约 13 秒（33 个包）**；chrome_capture_operate 依赖约 62 秒（21 个包） |
| 官方 PyPI | 默认 | 可用但慢：约 2 分钟（33 个包） |

用法：在安装命令中加 `-i https://mirrors.aliyun.com/pypi/simple/`（详见「AI 执行清单」第 4 步）。

# 前提条件

| 条件 | 说明 |
|---|---|
| Windows 操作系统 | 必须。本工具及 chrome_capture_operate 仅支持 Windows，Mac 无法使用 |
| 已安装 AI 命令行工具（如 Claude Code） | 必须。本文档的「AI 执行清单」依赖 AI 能在电脑上执行命令 |
| 网络可访问 gitee.com 或 github.com | 必须。下载代码；Python 安装包默认从 python.org 下载，慢时用国内镜像（见下文） |
| 拥有火山引擎智能外呼控制台账号 | 必须。需人工登录一次 |
| 电脑管理员权限 | **不需要**。Python 可按用户级安装，全部软件仅安装到用户目录 |

# 分工总览：哪些 AI 能做，哪些必须人工

| 步骤 | 执行方 | 说明 |
|---|---|---|
| 安装 Python（官网安装包，用户级静默安装） | AI | 无需管理员权限，无需人工点击安装向导 |
| 下载两个项目 ZIP 并解压 | AI | 走 Gitee/GitHub 网页直链，不需要 Git |
| 两个项目安装依赖（install.bat） | AI | 等价命令见「AI 执行清单」，或直接执行 install.bat |
| 启动两个服务（start.bat）并验证运行 | AI | 后台无窗口运行，系统托盘显示图标 |
| 写入「允许操作的账号」配置 | AI | 人工登录完成后由 AI 执行一条命令写入 |
| 将 MCP 服务注册到 AI 工具 | AI | 一条命令 |
| 部署完成后的验证测试 | AI | 调用查询类脚本确认链路可用 |
| **安装 Chrome 插件** | **人工** | Chrome 的安全限制决定只能人工在浏览器界面中操作 |
| **配置插件的 Cookie 推送范围** | **人工** | 同上，只能人工在插件设置页操作 |
| **登录火山引擎智能外呼控制台** | **人工** | 需输入本人的账号密码/验证码，登录凭证不应交给任何人（包括 AI） |

人工步骤共 3 项，合计约 5 分钟，操作细节见「人工步骤」一节。

# AI 执行清单

AI 按以下顺序执行。每步完成后核对结果再进入下一步；**到「检查点」必须停下**，向用户说明需要人工做什么，等用户确认完成后继续。

以下命令中的目录为示例，可按用户指定的位置调整（建议放在同一父目录下）。

## 第 1 步：检查环境

```
python --version
```

- 若已输出 Python 3.10 及以上版本：跳过第 2 步，直接进入第 3 步；
- 若提示找不到命令：继续第 2 步安装 Python。

## 第 2 步：安装 Python（已装则跳过）

下载官方安装包（约 26MB，优先国内镜像；两种来源实测结果见「下载地址与国内镜像」）：

```
curl -L -o python-3.12.6-amd64.exe https://mirrors.huaweicloud.com/python/3.12.6/python-3.12.6-amd64.exe
```

镜像不可用时改用官方地址（国内较慢，耐心等待或使用断点续传 `curl -L -C -`）：

```
curl -L -o python-3.12.6-amd64.exe https://www.python.org/ftp/python/3.12.6/python-3.12.6-amd64.exe
```

用户级静默安装（无需管理员权限，自动加入 PATH）：

```
python-3.12.6-amd64.exe /passive InstallAllUsers=0 PrependPath=1
```

安装后验证：

```
"%LOCALAPPDATA%\Programs\Python\Python312\python.exe" --version
```

应输出 `Python 3.12.6`。

> 注意：安装后 PATH 变化只对**新打开的终端**生效，AI 当前终端可能仍找不到 `python` 命令。后续命令请直接使用完整路径 `%LOCALAPPDATA%\Programs\Python\Python312\python.exe`，避免此问题。

## 第 3 步：下载并解压两个项目

从 Gitee 下载（国内网络更快，已实测约 3 秒）：

```
curl -L -o volcengine_aibot_scripts.zip https://gitee.com/adrninistrator/volcengine_aibot_scripts/repository/archive/master.zip
curl -L -o chrome_capture_operate.zip https://gitee.com/adrninistrator/chrome_capture_operate/repository/archive/master.zip
```

备选（GitHub，已实测可用且与 Gitee 内容一致）：

```
curl -L -o volcengine_aibot_scripts.zip https://github.com/Adrninistrator/volcengine_aibot_scripts/archive/refs/heads/master.zip
curl -L -o chrome_capture_operate.zip https://github.com/Adrninistrator/chrome_capture_operate/archive/refs/heads/master.zip
```

解压（PowerShell）：

```
Expand-Archive volcengine_aibot_scripts.zip -DestinationPath .
Expand-Archive chrome_capture_operate.zip -DestinationPath .
```

两个压缩包解压后的根目录名分别为 `volcengine_aibot_scripts-master` 与 `chrome_capture_operate-master`。为便于后续引用，可分别重命名为 `volcengine_aibot_scripts` 与 `chrome_capture_operate`。

> 人工也可以在网页上手动下载：浏览器打开项目页面 → 右上角「克隆/下载」→「下载 ZIP」→ 解压。效果完全相同。

## 第 4 步：安装两个项目的依赖

对 chrome_capture_operate，进入其中的 `chrome_capture_operate_python` 目录执行安装；对 volcengine_aibot_scripts，在项目根目录执行安装。

方式一（与 install.bat 等价的三条命令，适合 AI 执行，避免批处理中的 pause 等待）：

```
<python路径> -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip -i https://mirrors.aliyun.com/pypi/simple/
.venv\Scripts\python.exe -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

其中 `<python路径>` 为第 1/2 步确认的 Python（已装则为 `python`，新装则为 `"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"`）。

> 使用阿里云 pip 镜像后，安装全部依赖实测约 13 秒（默认官方源约 2 分钟）。镜像不可用时去掉 `-i ...` 参数回退官方源。

方式二：直接运行各项目的 `install.bat`（双击或命令行执行，需 `python` 已在 PATH 中，结束后会提示 install done）。

安装失败常见原因：网络/代理问题。可让 AI 换镜像重试。

## 检查点 1：人工步骤

依赖安装完成后**暂停**，请用户依次完成「人工步骤」一节的 3 项操作（约 5 分钟），并确认 chrome_capture_operate 的服务已启动（见下）。用户确认完成后继续。

同时 AI 可先启动 chrome_capture_operate 服务（在 `chrome_capture_operate_python` 目录下运行其 `start.bat`），启动后系统托盘出现图标，管理页面为 `http://127.0.0.1:33445`。

> chrome_capture_operate 也支持在其管理页面「参数配置」中开启**是否自动启动**（开机自启动），推荐开启，免去每次开机手动启动。

## 第 5 步：启动本工具服务

在 volcengine_aibot_scripts 目录下运行：

```
start.bat
```

- 服务以无窗口方式后台运行，系统托盘出现图标；**双击托盘图标**打开 Web 配置页（默认 `http://127.0.0.1:19000/`）；
- 启动脚本会自检端口监听状态，15 秒内未监听会输出失败原因与日志位置（`log\startup.log`、`log\startup_err.log`）；
- 若提示端口被占用，说明已有实例在运行：从托盘右键退出旧实例后重试，或换端口 `start.bat 19001`。

## 第 6 步：写入「允许操作的账号」

前提：用户已在日常 Chrome 中登录火山引擎智能外呼控制台，且插件 Cookie 推送已生效。

```
.venv\Scripts\python.exe scripts\get_current_user.py --save-allowed
```

该命令获取当前 Chrome 登录的账号并写入全局配置（`C:\Users\<用户名>\.volcengine_aibot_scripts\global.json`）。

> 若报「Cookie 服务不可达」或「获取 Cookie 失败」：检查 chrome_capture_operate 服务是否运行（浏览器访问 `http://127.0.0.1:33445/`）、Chrome 是否已登录控制台、插件推送范围是否允许 volcengine.com。
>
> 「允许操作的账号」是一道保险：修改类操作执行前会校验当前 Chrome 登录账号是否为该账号，不一致则拒绝执行，防止误操作其他账号的生产剧本。

## 第 7 步：注册 MCP 服务到 AI 工具

```
claude mcp add --scope user --transport sse volc-aibot http://127.0.0.1:19000/sse
```

（端口与 start.bat 使用的一致；使用了自定义端口则相应修改。）

## 第 8 步：验证

向 AI 说：

```
使用 volc-aibot MCP，先调用 usage_guide 工具了解怎么用，然后查询项目组列表
```

能返回项目组列表即整条链路（Chrome 登录 → 插件推送 Cookie → chrome_capture_operate → 本工具 → AI）全部打通。部署完成。

# 人工步骤

以下 3 项因 Chrome 的安全限制或涉及个人凭证，**只能人工完成**，无法由 AI 代做。AI 会在「检查点 1」处等待。

## 1. 安装 Chrome 插件（约 2 分钟）

1. 在日常使用的 Chrome 中打开 `chrome://extensions`（地址栏输入后回车）；
2. 打开右上角的**开发者模式**开关；
3. 点击左上角**加载已解压的扩展程序**，选择解压后的 `chrome_capture_operate\chrome_capture_operate_extension` 目录；
4. 确认插件列表中出现 chrome_capture_operate 且开关为开启。

## 2. 配置 Cookie 推送范围（约 1 分钟）

1. 点击 Chrome 右上角扩展程序图标，打开 chrome_capture_operate 插件页面；
2. 在**参数配置**中找到**Cookie推送范围**（默认「全部禁止」，不推送任何 Cookie）；
3. 改为**全部允许**，或**按指定范围推送**并在允许清单中添加 `*.volcengine.com`；
4. 其余参数（推送目标地址、推送时间间隔）保持默认即可。

可在 chrome_capture_operate 管理页面的「Chrome Cookie接收状态」标签页确认是否收到推送记录。

## 3. 登录火山引擎智能外呼控制台（约 1 分钟）

在日常 Chrome 中打开 `https://console.volcengine.com/aibot`，像平时一样用自己的账号登录。登录后插件会自动把登录 Cookie 推送给 chrome_capture_operate。

# 部署完成后的日常使用

日常使用只需保证两件事，其余全部通过自然语言指挥 AI：

1. chrome_capture_operate 服务与本工具服务在运行（托盘有图标；建议前者开启开机自启动）；
2. 日常 Chrome 处于控制台登录状态（长时间未访问导致过期时重新登录一次）。

之后直接对 AI 提需求即可，例如：

```
使用 volc-aibot MCP：查询项目组下的剧本，找到剧本xxx，导出并检查其中的变量
```

不知道怎么描述时，先让 AI 调用 `usage_guide` 工具。更多场景与提示词见「提示词示例」页面。

# 注意事项

- **仅支持 Windows**：Mac 电脑无法使用，没有替代办法；
- **对话测试占用生产外呼资源**：使用文本对话测试时，请勿同时发起大量会话，避开业务高峰，建议在夜间测试；
- **公司管控严格的电脑**：若企业安全软件禁止程序安装或后台运行，AI 的安装/启动命令可能被拦截，此时需联系 IT 部门协助安装 Python，或人工双击安装包安装（勾选 Add Python to PATH）；
- **修改有自动备份**：每次修改类操作执行前，会自动把剧本与变量当前值备份到 result 目录，出错可恢复；
- **重启后**：两个服务不会自动运行（chrome_capture_operate 可配置开机自启动），本工具需重新运行 `start.bat`（也可让 AI 帮忙执行）。
