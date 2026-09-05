# -*- coding: utf-8 -*-
"""结果文件目录管理。

约定（prompt 要求）：结果文件写入项目根 result/ 目录，
每次运行生成一个 {当前时间_功能描述} 子目录，如：
    result/20260903_201530_导出剧本/【存客】multi-agent v2.json
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import PROJECT_ROOT

RESULT_DIR = PROJECT_ROOT / "result"

_SAFE = re.compile(r'[\\/:*?"<>|\r\n\t ]+')


def _safe_name(text: str) -> str:
    """把功能描述 / 文件名片段转成 Windows 安全的目录/文件名。"""
    cleaned = _SAFE.sub("_", text or "result").strip("._")
    return cleaned[:80] or "result"


def new_result_dir(desc: str) -> Path:
    """创建并返回 result/{YYYYMMDD_HHMMSS}_{功能描述}/ 子目录。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = RESULT_DIR / f"{ts}_{_safe_name(desc)}"
    d.mkdir(parents=True, exist_ok=True)
    return d


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
