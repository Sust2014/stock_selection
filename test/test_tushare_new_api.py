# -*- coding: utf-8 -*-

import tushare as ts


def main():
    print("正在初始化 Tushare 新接口...")

    pro = ts.pro_api("OaZRxcERYuAvUoZyhzJkwlvfbDvMSRtDlmLMvMUDzbCykDxYZHIuFMAlWXunwvev")

    # 关键：必须指定新的接口地址
    pro._DataApi__http_url = "http://8.136.22.187:8011/"

    print("初始化完成，开始测试 index_basic...")

    df = pro.index_basic(limit=5)
    print("\nindex_basic 返回结果：")
    print(df)

    print("\n开始测试 pro_bar...")

    df = ts.pro_bar(
        api=pro,
        ts_code="000001.SZ",
        limit=3
    )

    print("\npro_bar 返回结果：")
    print(df)

    print("\n测试完成。")


if __name__ == "__main__":
    main()