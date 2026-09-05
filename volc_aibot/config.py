# -*- coding: utf-8 -*-
"""全局配置常量（来源：抓包记录 + chrome_capture_operate 接口文档）。"""

import os

# 火山引擎控制台基址
BASE_URL = "https://console.volcengine.com"

# chrome_capture_operate Cookie 查询服务地址
# （优先级：脚本参数 --cookie-api > 环境变量 VOLC_COOKIE_API > 此默认值；
#   端口以 chrome_capture_operate 参数配置页实际值为准）
DEFAULT_COOKIE_API = os.environ.get("VOLC_COOKIE_API") or "http://127.0.0.1:33445"

# URL 路径中 products/<id> 一段：多份抓包中恒为 9（外呼机器人产品线，固定值）
PRODUCT_ID = 9

# 单个 HTTP 请求默认超时（秒）；talk / 写接口实测最慢 2~3 秒，留足余量
DEFAULT_TIMEOUT = 60.0

# 变量类型数值 -> 描述（prompt 背景知识）
VARIABLE_TYPES = {1: "String", 2: "Integer", 3: "Float", 4: "Boolean"}

# casbin 项目组查询使用的资源码（控制台前端写死值，抓包验证可用）
PERMISSION_RESOURCE = "llm_dialog_config"
