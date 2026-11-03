# -*- coding: utf-8 -*-
"""全局配置文件管理（prompt 要求）。

位置：C:\\Users\\<username>\\.volcengine_aibot_scripts\\global.json
（即 ~/.volcengine_aibot_scripts/global.json，按当前用户主目录解析）

配置项：
- server_port     监听端口（HTTP 配置页 + MCP SSE 同端口，默认 19000，改后需重启）
- allow_mutation  是否允许执行修改操作（prompt 2026-09-07：开启后才允许
                  执行修改类操作，默认关闭；只允许人工在配置页修改）
- allowed_account 允许执行修改操作的账号（唯一，数字字符串；为空表示
                   未配置，此时工具执行前应提醒先到配置页设置）
- cookie_sources  Cookie 获取来源列表（2026-11-02）：
                   type=extension 按日常 Chrome 插件推送的 Chrome 实例编号
                   （profile）读取；type=cdp 按抓包 Chrome 的调试端口实时
                   读取。默认来源（id=default，日常 Chrome，profile=0）
                   始终存在且不允许修改/删除；
                   enabled=false 表示不允许使用（不显示在页面右上角切换框）
- cookie_source_current 当前使用的来源 id（必须在 enabled 的来源中；
                   来源被禁用/删除时自动回退到默认来源）

并发安全（prompt：客户端重连要求之外的实际竞态防护）：
- **跨进程文件锁**：读写经 .lock 文件 + msvcrt（Windows）排他锁，
  防止多客户端/脚本并发读写同一 global.json 时互相覆盖
  （实测场景：并行验证进程一方改空账号，导致另一方修改类操作被守卫拦截）；
- 进程内另有线程锁（同进程多线程并发读写）；
- 写入用“临时文件+原子替换”，读方永远不会读到半个 JSON。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".volcengine_aibot_scripts"
CONFIG_FILE = CONFIG_DIR / "global.json"
_LOCK_FILE = CONFIG_DIR / ".global.json.lock"

COOKIE_SOURCE_DEFAULT_ID = "default"
_COOKIE_SOURCE_DEFAULT: dict[str, Any] = {
    "id": COOKIE_SOURCE_DEFAULT_ID,
    "name": "日常 Chrome（插件，profile 0）",
    "type": "extension",
    "profile": 0,
    "enabled": True,
    "fixed": True,
}


def _default_cookie_sources() -> list[dict]:
    return [dict(_COOKIE_SOURCE_DEFAULT)]


DEFAULTS: dict[str, Any] = {
    "server_port": 19000,
    "allow_mutation": False,
    "allowed_account": "",
    "cookie_sources": _default_cookie_sources(),
    "cookie_source_current": COOKIE_SOURCE_DEFAULT_ID,
}

def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_cookie_sources(raw: Any, strict: bool = False) -> list[dict]:
    """规范化 Cookie 来源列表。

    - 默认来源（日常 Chrome 插件，profile 0）始终存在、始终启用、排在首位，
      且不允许修改/删除：客户端提交的同 id 条目其字段一律忽略；
    - strict=True（配置页保存）时非法条目抛 ValueError；
      strict=False（读取损坏配置）时跳过非法条目；
    - 非默认条目缺 id 或 id 重复时自动生成 id（页面保存时由服务端分配）。
    """
    if raw is not None and not isinstance(raw, list):
        if strict:
            raise ValueError("cookie_sources 需为数组")
        raw = []
    out: list[dict] = [dict(_COOKIE_SOURCE_DEFAULT)]
    seen = {COOKIE_SOURCE_DEFAULT_ID}
    seen_values = {("extension", 0)}   # 默认来源已占用
    for item in raw or []:
        if not isinstance(item, dict):
            if strict:
                raise ValueError("Cookie 来源条目需为对象")
            continue
        sid = str(item.get("id") or "").strip()
        if sid == COOKIE_SOURCE_DEFAULT_ID:
            continue
        typ = str(item.get("type") or "").strip().lower()
        if typ not in ("extension", "cdp"):
            if strict:
                raise ValueError("Cookie 来源类型需为 extension 或 cdp")
            continue
        if typ == "extension":
            value = _int_or_none(item.get("profile"))
            if value is None or value < 0:
                if strict:
                    raise ValueError("日常 Chrome 插件的 profile 需为 >=0 的整数")
                continue
            entry = {"id": sid, "name": "", "type": typ,
                     "profile": value, "enabled": bool(item.get("enabled", True)),
                     "fixed": False}
            default_name = f"日常 Chrome 插件（profile {value}）"
        else:
            value = _int_or_none(item.get("port"))
            if value is None or not (1024 <= value <= 65535):
                if strict:
                    raise ValueError("CDP 端口需为 1024~65535 的整数")
                continue
            entry = {"id": sid, "name": "", "type": typ,
                     "port": value, "enabled": bool(item.get("enabled", True)),
                     "fixed": False}
            default_name = f"CDP Chrome（端口 {value}）"
        key = (typ, value)
        if key in seen_values:
            if strict:
                raise ValueError(f"Cookie 来源已存在: {default_name}")
            continue
        seen_values.add(key)
        if not sid or sid in seen:
            sid = uuid.uuid4().hex[:8]
            entry["id"] = sid
        seen.add(sid)
        entry["name"] = str(item.get("name") or "").strip() or default_name
        out.append(entry)
    return out


def _valid_current(cfg: dict) -> str:
    enabled = {s["id"] for s in cfg.get("cookie_sources") or []
               if s.get("enabled")}
    current = str(cfg.get("cookie_source_current") or "").strip()
    return current if current in enabled else COOKIE_SOURCE_DEFAULT_ID


def _normalize_loaded(cfg: dict) -> dict:
    cfg = dict(cfg)
    cfg["cookie_sources"] = normalize_cookie_sources(cfg.get("cookie_sources"))
    cfg["cookie_source_current"] = _valid_current(cfg)
    return cfg


_lock = None
_sig_lock = threading.Lock()
_sig_cache: tuple[tuple[str, int, int], str] | None = None


def _get_lock():
    global _lock
    if _lock is None:
        import threading
        _lock = threading.Lock()
    return _lock


class _CrossProcessFileLock:
    """跨进程文件锁（Windows msvcrt / 降级为重命名锁）。

    用法：with _CrossProcessFileLock(): ...
    - Windows：lock 文件上 msvcrt.locking(LK_NBLCK) 排他字节锁，
      竞争方阻塞重试直至超时（默认 5 秒），防死锁退化为 best-effort；
    - 非 Windows/失败：锁不致命（返回无操作上下文），写侧另有原子替换兜底。
    """

    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout
        self._fh = None

    def __enter__(self):
        try:
            import time as _time
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            self._fh = open(_LOCK_FILE, "a+")
            try:
                import msvcrt
                deadline = _time.time() + self.timeout
                while True:
                    try:
                        msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
                        return self
                    except OSError:
                        if _time.time() >= deadline:
                            # 超时：放弃跨进程锁（best-effort），继续执行
                            self._fh.close()
                            self._fh = None
                            return self
                        _time.sleep(0.05)
            except ImportError:
                # 非 Windows：无 msvcrt，降级为无跨进程锁（单机使用场景足够）
                return self
        except Exception:
            # 锁机制任何失败都不阻塞配置读写（best-effort）
            self._fh = None
            return self

    def __exit__(self, exc_type, exc, tb):
        if self._fh is not None:
            try:
                import msvcrt
                try:
                    self._fh.seek(0)
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            except ImportError:
                pass
            finally:
                try:
                    self._fh.close()
                except OSError:
                    pass
                self._fh = None
        return False


def load_config() -> dict:
    """读取全局配置（文件不存在/损坏时返回默认值）。跨进程加锁读取。"""
    cfg = dict(DEFAULTS)
    try:
        with _CrossProcessFileLock():
            if CONFIG_FILE.is_file():
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    cfg.update({k: v for k, v in data.items()
                                if k in DEFAULTS})
    except (ValueError, OSError):
        pass  # 损坏则用默认值
    return _normalize_loaded(cfg)


def save_config(cfg: dict) -> dict:
    """合并保存配置（只保留已知键），返回保存后的完整配置。

    跨进程文件锁 + 读改写在同一临界区内（防止并发“读-改-写”丢更新，
    即一方写入被另一方读旧值后覆盖）；临时文件 + 原子替换防半写。
    """
    with _get_lock():                 # 进程内线程互斥
        with _CrossProcessFileLock():  # 跨进程互斥
            # 锁内直接读文件（不走 load_config，避免重复加锁路径）
            merged = _read_file_unlocked()
            incoming = {k: v for k, v in cfg.items() if k in DEFAULTS}
            if "cookie_sources" in incoming:
                # 配置页保存走严格校验；默认来源由规范化强制保留
                incoming["cookie_sources"] = normalize_cookie_sources(
                    incoming["cookie_sources"], strict=True)
            merged.update(incoming)
            # 始终写入规范化后的来源与当前来源（旧配置文件首次保存时补齐）
            merged["cookie_sources"] = normalize_cookie_sources(
                merged.get("cookie_sources"))
            merged["cookie_source_current"] = _valid_current(merged)
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            # 临时文件 + 原子替换：读方永远不会读到半个 JSON
            fd, tmp = tempfile.mkstemp(dir=str(CONFIG_DIR),
                                       prefix=".global.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(merged, ensure_ascii=False, indent=2))
                os.replace(tmp, str(CONFIG_FILE))   # Windows 上原子替换
                global _sig_cache
                _sig_cache = None            # C8：配置已变，签名缓存失效
            except OSError:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return merged


def _read_file_unlocked() -> dict:
    """不加锁读文件（调用方已持锁）。损坏/不存在返回默认值。"""
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_FILE.is_file():
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except (ValueError, OSError):
        pass
    return _normalize_loaded(cfg)


# ---------------------------------------------------------------- Cookie 来源

def get_cookie_sources() -> list[dict]:
    """全部 Cookie 来源（含未启用的；默认来源始终在首位）。"""
    return load_config()["cookie_sources"]


def get_cookie_source_current() -> str:
    """当前使用的 Cookie 来源 id。"""
    return load_config()["cookie_source_current"]


def resolve_cookie_source() -> dict:
    """当前生效的来源（读取时解析，切换后下一次取 Cookie 即生效）。"""
    cfg = load_config()
    for src in cfg["cookie_sources"]:
        if src["id"] == cfg["cookie_source_current"]:
            return dict(src)
    return dict(_COOKIE_SOURCE_DEFAULT)


def cookie_source_signature() -> str:
    """当前来源的签名；客户端据此判断是否需要重建 Cookie 会话。

    2026-11-03（C8）：按配置文件 mtime_ns+size 缓存，避免每个 HTTP 请求
    都加锁读取/解析 global.json；save_config 后主动失效。
    """
    global _sig_cache
    try:
        st = CONFIG_FILE.stat()
        key = (str(CONFIG_FILE), st.st_mtime_ns, st.st_size)
    except OSError:
        key = None
    with _sig_lock:
        if key is not None and _sig_cache is not None \
                and _sig_cache[0] == key:
            return _sig_cache[1]
    src = resolve_cookie_source()
    sig = "|".join(str(src.get(k, ""))
                   for k in ("id", "type", "profile", "port"))
    with _sig_lock:
        _sig_cache = (key, sig) if key is not None else None
    return sig


def set_cookie_source_current(source_id: str) -> dict:
    """切换当前来源（只允许切换到已启用的来源）。"""
    sid = str(source_id or "").strip()
    for src in get_cookie_sources():
        if src["id"] == sid:
            if not src.get("enabled"):
                raise ValueError("Cookie 来源未启用: %s" % sid)
            save_config({"cookie_source_current": sid})
            return dict(src)
    raise ValueError("Cookie 来源不存在: %s" % sid)


def set_cookie_sources(sources: Any) -> list[dict]:
    """保存来源列表（严格校验，默认来源不可修改/删除）。"""
    normalized = normalize_cookie_sources(sources, strict=True)
    return save_config({"cookie_sources": normalized})["cookie_sources"]


def set_server_port(port: int) -> dict:
    return save_config({"server_port": int(port)})


def set_allowed_account(account: str) -> dict:
    """设置允许操作的账号（唯一）。传空串表示清除。"""
    return save_config({"allowed_account": str(account or "").strip()})


def get_allowed_account() -> str:
    return str(load_config().get("allowed_account") or "").strip()


def get_allow_mutation() -> bool:
    """是否允许执行修改操作（prompt 2026-09-07，默认关闭）。"""
    return bool(load_config().get("allow_mutation"))


def set_allow_mutation(enabled: bool) -> dict:
    """设置是否允许执行修改操作（仅人工经配置页操作）。"""
    return save_config({"allow_mutation": bool(enabled)})


def get_server_port() -> int:
    try:
        return int(load_config().get("server_port") or DEFAULTS["server_port"])
    except (TypeError, ValueError):
        return int(DEFAULTS["server_port"])


def config_path() -> str:
    return str(CONFIG_FILE)


def account_configured() -> bool:
    """允许账号是否已配置（未配置时工具应提醒打开配置页）。"""
    return bool(get_allowed_account())
