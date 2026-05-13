# config.py

import os

# =========================
# Tushare 配置
# =========================
# 为了避免把 Token 上传到 GitHub，优先读取环境变量 TUSHARE_TOKEN。
# 如果你只是本地测试，也可以把下面的“这里填你的token”替换成自己的 token。
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", "OaZRxcERYuAvUoZyhzJkwlvfbDvMSRtDlmLMvMUDzbCykDxYZHIuFMAlWXunwvev")

# 你购买的 Tushare 代理地址
TUSHARE_HTTP_URL = os.getenv("TUSHARE_HTTP_URL", "http://8.136.22.187:8011/")

# =========================
# 基础股票池筛选条件
# =========================
# 市值范围，单位：亿元
MIN_MARKET_VALUE = 100
MAX_MARKET_VALUE = 1500

# 排除行业关键词
EXCLUDE_INDUSTRIES = [
    "银行",
    "证券",
    "券商",
    "保险",
    "信托",
    "房地产",
    "地产",
    "钢铁",
    "煤炭",
    "煤炭开采",
    "铁路运输",
    "航运",
    "水运",
    "港口",
]

# 导出路径
OUTPUT_FILE = "output/a_stock_selected.xlsx"
