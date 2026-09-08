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
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".volcengine_aibot_scripts"
CONFIG_FILE = CONFIG_DIR / "global.json"
_LOCK_FILE = CONFIG_DIR / ".global.json.lock"

DEFAULTS: dict[str, Any] = {
    "server_port": 19000,
    "allow_mutation": False,
    "allowed_account": "",
}

_lock = None


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
    return cfg


def save_config(cfg: dict) -> dict:
    """合并保存配置（只保留已知键），返回保存后的完整配置。

    跨进程文件锁 + 读改写在同一临界区内（防止并发“读-改-写”丢更新，
    即一方写入被另一方读旧值后覆盖）；临时文件 + 原子替换防半写。
    """
    with _get_lock():                 # 进程内线程互斥
        with _CrossProcessFileLock():  # 跨进程互斥
            # 锁内直接读文件（不走 load_config，避免重复加锁路径）
            merged = _read_file_unlocked()
            merged.update({k: v for k, v in cfg.items() if k in DEFAULTS})
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            # 临时文件 + 原子替换：读方永远不会读到半个 JSON
            fd, tmp = tempfile.mkstemp(dir=str(CONFIG_DIR),
                                       prefix=".global.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(json.dumps(merged, ensure_ascii=False, indent=2))
                os.replace(tmp, str(CONFIG_FILE))   # Windows 上原子替换
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
    return cfg


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
