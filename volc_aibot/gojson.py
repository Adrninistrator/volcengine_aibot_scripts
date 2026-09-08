# -*- coding: utf-8 -*-
"""Go encoding/json.Marshal 复现（剧本导出 json 的 checksum 计算）。

火山剧本导出 json：{"data": {...}, "checksum": "<sha256 hex>"}，
checksum = sha256(GoMarshal(data))。修改剧本内容（如 meta.name 加后缀）
后须重算并写回 checksum，否则导入被拒（Code=101 "文件已被修改"）。

序列化规则（参考 prompt/剧本json文件HASH字段计算规则.md）：
1. dict 键**按 sorted 排序**（Go map 遍历序）；
2. 分隔符紧凑（",", ":"）无空格；
3. 字符串：\\、\"、\n、\r、\t 转义；<、>、&、U+2028、U+2029 与
   <0x20 控制字符转 \\uXXXX；其他字符（含中文）原样；
4. 数字：整数原样；浮点去 .0 尾（Go 与 Python 数值格式差异）；
5. 内嵌 JSON 字符串（prompt.information 等内层"字符串化的 JSON"）：
   解码再编码时**保持原键序不排序**（与规则 1 不同）——仅当需要修改
   内层内容时才重新编码；未修改的内层字符串应保持原样字节不动。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

SHORT = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
ESCAPE_ALWAYS = ("<", ">", "&", " ", " ")


def _encode_str(s: str) -> str:
    parts = ['"']
    for c in s:
        if c in SHORT:
            parts.append(SHORT[c])
        elif c in ESCAPE_ALWAYS or ord(c) < 0x20:
            parts.append(f"\\u{ord(c):04x}")
        else:
            parts.append(c)
    parts.append('"')
    return "".join(parts)


def _encode(o: Any, out: list, sort_keys: bool = True) -> None:
    if o is True:
        out.append("true")
    elif o is False:
        out.append("false")
    elif o is None:
        out.append("null")
    elif isinstance(o, int):
        out.append(str(o))
    elif isinstance(o, float):
        t = repr(o)
        out.append(t[:-2] if t.endswith(".0") else t)
    elif isinstance(o, str):
        out.append(_encode_str(o))
    elif isinstance(o, dict):
        out.append("{")
        keys = sorted(o) if sort_keys else list(o.keys())
        for i, k in enumerate(keys):
            if i:
                out.append(",")
            out.append(_encode_str(k))
            out.append(":")
            _encode(o[k], out, sort_keys)
        out.append("}")
    elif isinstance(o, (list, tuple)):
        out.append("[")
        for i, v in enumerate(o):
            if i:
                out.append(",")
            _encode(v, out, sort_keys)
        out.append("]")
    else:
        raise ValueError(f"无法序列化: {type(o).__name__}")


def go_marshal(obj: Any) -> bytes:
    """复现 Go encoding/json.Marshal 默认输出（键排序、紧凑分隔）。"""
    out: list = []
    _encode(obj, out)
    return "".join(out).encode("utf-8")


def calc_checksum(data: dict) -> str:
    """data（剧本 json 的 data 字段）-> sha256 hex（checksum 字段值）。"""
    return hashlib.sha256(go_marshal(data)).hexdigest()


def verify_checksum(text: str) -> bool:
    """校验 json 文本（或 bytes）的 checksum 字段与重算值一致。"""
    doc = load_json_text(text)
    return doc.get("checksum") == calc_checksum(doc.get("data") or {})


def load_json_text(text: str | bytes) -> dict:
    """读剧本 json（兼容 BOM）。"""
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    else:
        text = text.lstrip("﻿")
    return json.loads(text)


def dump_json_text(doc: dict) -> str:
    """写回剧本 json（顶层 {"data","checksum"}，与导出格式一致：
    顶层键序 data 在前 checksum 在后，紧凑分隔，BOM 不加）。"""
    # 顶层保持 data -> checksum 顺序（与导出文件一致），不排序
    out: list = ["{"]
    first = True
    for k in ["data", "checksum"]:
        if k not in doc:
            continue
        if not first:
            out.append(",")
        first = False
        out.append(_encode_str(k))
        out.append(":")
        _encode(doc[k], out)
    # 其余未知键（如有）按排序附后
    for k in sorted(set(doc) - {"data", "checksum"}):
        out.append(",")
        out.append(_encode_str(k))
        out.append(":")
        _encode(doc[k], out)
    out.append("}")
    return "".join(out)


def go_marshal_inner(obj: Any) -> str:
    """内嵌 JSON 字符串的还原编码（与外层 go_marshal 规则不同！）。

    导出文件的内层字段（prompt[].information / component[].information /
    backgroundAudio.background_audio / llm_variables.*_config 等
    "字符串化的 JSON"）使用**另一种转义风格**（2026-09-07 对 68 个导出
    剧本实测归纳，prompt/component/background 全部逐字节还原）：
    - 中文原样（同外层）；
    - 换行/回车/制表/反斜杠/引号 基础转义（同外层）；
    - **< > & 不转义**（外层会转义为 \\u003c 等，内层原样）；
    - 退格/换页等控制字符转 \\uXXXX；
    - **键序保持原样**（不排序，与外层规则 1 不同）。

    ⚠ 例外：`agent_config.asr_context_cfg.Context`（20/20 实测）使用
    **Python json.dumps 默认风格**（分隔符带空格 ", " ": "，中文原样）——
    修改该字段时用 `json.dumps(inner, ensure_ascii=False)` 而非本函数。

    用途：修改内层内容（如提示词）时，解码 → 改 → 用本函数重新编码回
    字符串 → 放回 data → 再用 calc_checksum 重算整体 checksum。
    未修改的内层字符串应保持原样字节不动（dump 流程不触碰）。
    """
    return _inner_encode_obj(obj)


def _inner_encode_obj(o: Any) -> str:
    BS = chr(92)
    if o is True:
        return "true"
    if o is False:
        return "false"
    if o is None:
        return "null"
    if isinstance(o, int):
        return str(o)
    if isinstance(o, float):
        t = repr(o)
        return t[:-2] if t.endswith(".0") else t
    if isinstance(o, str):
        s = (o.replace(BS, BS * 2).replace('"', BS + '"')
             .replace("\n", BS + "n").replace("\r", BS + "r")
             .replace("\t", BS + "t"))
        out = []
        for c in s:
            if c in ("\b", "\f"):
                out.append(BS + "u%04x" % ord(c))
            else:
                out.append(c)
        return '"' + "".join(out) + '"'
    if isinstance(o, dict):
        return "{" + ",".join(
            _inner_encode_obj(k) + ":" + _inner_encode_obj(v)
            for k, v in o.items()) + "}"
    if isinstance(o, (list, tuple)):
        return "[" + ",".join(_inner_encode_obj(v) for v in o) + "]"
    raise ValueError(f"无法序列化: {type(o).__name__}")


def refresh_checksum(content: str | bytes,
                     mutate: callable = None) -> str:
    """修改剧本 data 并重算 checksum，返回新的完整 json 文本。

    mutate：可选回调 mutate(data: dict)，就地修改 data（如改
    data["meta"]["name"]）。返回新文本（data + 新 checksum）。
    注意：内嵌 JSON 字符串字段（prompt[0].information 等）如需修改，
    解码再编码需**保持原键序**（go_marshal(sort_keys=False)）——本函数
    对 data 顶层按规则重排不影响未修改的内层字符串（保持原样字节）。
    """
    doc = load_json_text(content)
    data = doc.get("data") or {}
    if mutate is not None:
        mutate(data)
    new_checksum = calc_checksum(data)
    return dump_json_text({"data": data, "checksum": new_checksum})
