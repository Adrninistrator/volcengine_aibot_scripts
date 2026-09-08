# -*- coding: utf-8 -*-
"""结果文件目录管理。

约定（prompt 要求）：结果文件写入项目根 result/ 目录，
每次运行生成一个 {当前时间_当前查询到的火山引擎账号_功能描述} 子目录，如：
    result/20260903_201530_2105888584_导出剧本/【存客】multi-agent v2.json

账号注入方式（三层，互为补充）：
- VolcAIBotClient 构造时通过 set_account_provider 注册账号提供者，
  new_result_dir 惰性调用（在线脚本/MCP/Web 自动生效）；
- get_current_user/get_current_account 命中时通过 set_result_account
  直接注入（守卫/显式查询过账号的进程免去重复请求）；
- 提供者不可用（离线搜索、Cookie 服务未启动）时降级为
  {时间_功能描述}，不影响主流程。
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import PROJECT_ROOT

RESULT_DIR = PROJECT_ROOT / "result"

# 批量导出剧本目录名的固定后缀（prompt 约定：以「_批量导出剧本」结尾，
# 且其他功能的目录名不含该关键字；搜索导出内容按此后缀定位目录）
EXPORT_DIR_SUFFIX = "_批量导出剧本"

# 批量下载分析Agent目录名的固定后缀（prompt 2026-09-06：以
# 「_批量下载分析Agent」结尾，内含各分析Agent 系统提示词 md 文件与
# 清单.json；搜索下载内容按此后缀定位目录）
AGENTS_DIR_SUFFIX = "_批量下载分析Agent"

_SAFE = re.compile(r'[\\/:*?"<>|\r\n\t ]+')

# 账号上下文（进程级）：provider 由 client 注册；_account 为已解析账号；
# _failed 标记 provider 本次进程已失败（避免每次建目录都重试网络）。
# RLock：_resolve_account 持锁调 provider，provider 内 set_result_account
# 会再次取锁（同线程可重入）。
_account_lock = threading.RLock()
_account_provider: Callable[[], str] | None = None
_account: str = ""
_account_failed: bool = False


def _safe_name(text: str) -> str:
    """把功能描述 / 文件名片段转成 Windows 安全的目录/文件名。"""
    cleaned = _SAFE.sub("_", text or "result").strip("._")
    return cleaned[:80] or "result"


def set_account_provider(provider: Callable[[], str] | None) -> None:
    """注册账号提供者（client 构造时注册，返回账号字符串，可能抛异常）。"""
    global _account_provider
    with _account_lock:
        _account_provider = provider


def set_result_account(account: str) -> None:
    """直接记录当前账号（get_current_user 命中时注入；目录名使用）。"""
    global _account
    with _account_lock:
        _account = _sanitize_account(account)


def reset_result_account() -> None:
    """清空账号上下文（测试隔离用）。"""
    global _account, _account_failed
    with _account_lock:
        _account = ""
        _account_failed = False


def _sanitize_account(account: str) -> str:
    """账号仅保留数字（登录账号为数字 ID），最长 20 位。"""
    return re.sub(r"\D", "", str(account or ""))[:20]


def _resolve_account() -> str:
    """解析当前账号：已注入直接用；否则经 provider 查询一次（失败降级）。"""
    global _account, _account_failed
    with _account_lock:
        if _account:
            return _account
        if _account_failed or _account_provider is None:
            return ""
        try:
            account = _sanitize_account(_account_provider())
        except Exception:
            _account_failed = True   # 本次进程不再重试（离线场景常见）
            return ""
        if account:
            _account = account
            return account
        _account_failed = True
        return ""


def new_result_dir(desc: str) -> Path:
    """创建并返回 result/{当天日期}/{时间_账号_功能描述}/ 子目录。

    （prompt 2026-09-07：目录结构改为按当天日期分组）
    账号获取不到（离线/失败）时退化为 {时间}_{功能描述}。
    """
    now = datetime.now()
    day = now.strftime("%Y%m%d")
    ts = now.strftime("%Y%m%d_%H%M%S")
    account = _resolve_account()
    name = f"{ts}_{account}_{_safe_name(desc)}" if account else \
        f"{ts}_{_safe_name(desc)}"
    d = RESULT_DIR / day / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_result_dir(name: str) -> Path | None:
    """按目录名解析结果子目录（兼容新旧两种结构），不存在返回 None。

    - 新结构（2026-09-07 起）：result/{yyyyMMdd}/{目录名}；
    - 旧结构：result/{目录名}（2026-09-07 前的目录，如历史批量导出）。

    Web 快捷工具的搜索（批量搜索剧本内容/批量下载分析Agent 的第 2 步）
    由目录下拉传目录名进来——列表接口（export_dirs/agents_dirs）两种
    结构都会列出，解析必须同样兼容，否则新导出的目录“看得见却搜不了”
    （2026-09-08 修复：此前直接 RESULT_DIR/name，只命中旧结构）。
    多个日期下同名目录时取最新（按日期目录倒序的第一个命中）。
    """
    name = str(name or "").strip()
    if not name:
        return None
    # 防目录穿越：仅接受目录名（不含路径分隔符）
    if "/" in name or "\\" in name or ".." in name:
        return None
    legacy = RESULT_DIR / name
    if legacy.is_dir():
        return legacy
    try:
        days = [d for d in RESULT_DIR.iterdir() if d.name.isdigit()]
    except OSError:
        return None
    for d in sorted(days, reverse=True):
        cand = d / name
        if cand.is_dir():
            return cand
    return None


def write_json(path: Path, obj: Any) -> Path:
    """把对象写为 UTF-8 JSON 文件（ensure_ascii=False，缩进 2）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def safe_filename(name: str) -> str:
    """文件名安全化（保留扩展名语义，仅替换非法字符）。"""
    return _safe_name(name)


# ---------------------------------------------------------------- 对话记录 md

def md_speaker_line(speaker: int, content: str) -> str:
    """渲染一条对话为 md 行：Speaker 1 -> 机器人:xxx，2 -> 客户:xxx。

    内容内部换行/连续空白压成单个空格，保证一行一句
    （满足“每行内容为 机器人:xxx 或 客户:xxx”的格式约定）。
    """
    role = "机器人" if speaker == 1 else "客户"
    text = " ".join(str(content or "").split())
    return f"{role}:{text}"


def write_dialog_md(path: Path, header: list[str],
                    items: list[dict]) -> Path:
    """写文本对话测试的 md 记录：header 头部 + 每行 机器人:xxx / 客户:xxx。

    items: [{"Speaker": 1|2, "Content": str}, ...]（1=机器人，2=客户）。
    每轮对话后调用（整体重写，文件小，幂等且中断安全）。
    """
    lines = list(header) + [""]
    for it in items:
        lines.append(md_speaker_line(it.get("Speaker"), it.get("Content", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- 变量表 md（\t 分隔）

def md_table_row(cells: list) -> str:
    """渲染一行 \t 分隔的表格行（单元格转字符串，内部 \t/\n 清洗为空格）。"""
    return "\t".join(" ".join(str(c or "").split()) for c in cells)


def write_variables_md(path: Path, title: str,
                       variables: list[dict],
                       columns: list[tuple[str, str]]) -> Path:
    """写变量 md 文件（prompt 约定：\t 分隔各列）。

    - variables: 变量对象列表；
    - columns: [(取值字段名, 列标题), ...]，如
      [("name", "名称"), ("key", "调用名称"),
       ("VariableType", "变量类型数值"), ("VariableTypeDesc", "变量类型")]
    输出形如（示例）：
        # <title>

        名称\t调用名称\t变量类型数值\t变量类型
        座席工号\tagent_id\t1\tString
    """
    header_cells = [title for _, title in columns]
    lines = [f"# {title}", "", md_table_row(header_cells)]
    for v in variables or []:
        lines.append(md_table_row([v.get(field, "") for field, _ in columns]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- 对话轮 md（每轮单文件）

def write_dialog_round_md(path: Path, header: list[str],
                          variables: list[tuple],
                          items: list[dict]) -> Path:
    """写单轮文本对话测试 md（prompt 约定）。

    结构：
        <header 头部>
        # 变量
        <变量名>\t<变量值>          （每变量一行，\t 分隔）
        # 对话内容
        机器人:xxx                  （每句话占一行）
        客户:xxx

    - variables: [(变量名, 值), ...]（名称与值，\t 分隔）；
    - items: [{"Speaker":1|2, "Content":...}]。
    """
    lines = list(header) + [""]
    lines.append("# 变量")
    for name, value in variables or []:
        lines.append(md_table_row([name, value]))
    lines.append("")
    lines.append("# 对话内容")
    for it in items or []:
        lines.append(md_speaker_line(it.get("Speaker"), it.get("Content", "")))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
