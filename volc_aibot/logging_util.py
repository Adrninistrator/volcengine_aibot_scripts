# -*- coding: utf-8 -*-
"""日志工具：log/ 目录下每天一个日志文件，同时输出到控制台。

- CLI 脚本与 MCP 服务统一调用 setup_logging() 获取 logger；
- 使用 TimedRotatingFileHandler(when="midnight")：当天写 log/volc_aibot.log，
  跨天后自动轮转出 log/volc_aibot.log.YYYY-MM-DD 旧文件；
- 顺带把 stdout/stderr 重配置为 UTF-8（Windows GBK 控制台打印中文防乱码）。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from . import PROJECT_ROOT

LOGGER_NAME = "volc_aibot"
LOG_DIR = PROJECT_ROOT / "log"
LOG_FILE = LOG_DIR / "volc_aibot.log"

_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_configured = False


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 重配置失败不影响主流程
            pass


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """初始化并返回全局 logger（重复调用安全，只配置一次）。"""
    global _configured
    _ensure_utf8_stdout()
    logger = logging.getLogger(LOGGER_NAME)
    if _configured:
        return logger
    logger.setLevel(level)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    file_handler = TimedRotatingFileHandler(
        LOG_FILE, when="midnight", backupCount=30, encoding="utf-8")
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(console)

    logger.propagate = False
    _configured = True
    return logger
