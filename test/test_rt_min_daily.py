# -*- coding: utf-8 -*-

import tushare as ts

TOKEN = "OaZRxcERYuAvUoZyhzJkwlvfbDvMSRtDlmLMvMUDzbCykDxYZHIuFMAlWXunwvev"
HTTP_URL = "http://8.136.22.187:8011/"


def main():
    pro = ts.pro_api(TOKEN)
    pro._DataApi__http_url = HTTP_URL

    for freq in ["1MIN", "5MIN", "30MIN"]:
        print("=" * 80)
        print(f"测试 rt_min_daily freq={freq}")

        df = pro.rt_min_daily(
            ts_code="000001.SZ",
            freq=freq,
        )

        print(df.head())
        print(df.tail())
        print("返回行数：", 0 if df is None else len(df))
        print("字段：", [] if df is None else list(df.columns))


if __name__ == "__main__":
    main()