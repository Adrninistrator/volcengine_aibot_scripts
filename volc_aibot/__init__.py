# -*- coding: utf-8 -*-
"""volc_aibot —— 火山引擎智能外呼控制台操作公共包。

目录约定（均相对项目根目录）：
- log/     运行日志（每天一个文件）
- result/  脚本产出结果（每次运行一个 {时间_账号_功能描述} 子目录）
"""

from pathlib import Path

# 项目根目录：本包位于 <root>/volc_aibot/，向上两级即根
PROJECT_ROOT = Path(__file__).resolve().parents[1]

__all__ = ["PROJECT_ROOT"]
