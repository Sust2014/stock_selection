# -*- coding: utf-8 -*-

import tushare as ts


TOKEN = "OaZRxcERYuAvUoZyhzJkwlvfbDvMSRtDlmLMvMUDzbCykDxYZHIuFMAlWXunwvev"
HTTP_URL = "http://8.136.22.187:8011/"


def main():
    print("正在初始化 Tushare 新接口...")

    pro = ts.pro_api(TOKEN)

    # 关键：必须指定你的新接口地址
    # 如果少了这一行，可能会提示 Token 不对
    pro._DataApi__http_url = HTTP_URL

    print("初始化完成，开始测试 rt_min 实时分钟接口...")

    try:
        df = pro.rt_min(
            ts_code="000001.SZ,600000.SH",
            freq="1MIN"
        )

        print("\nrt_min 返回结果：")
        print(df)

        if df is None:
            print("\n测试结果：失败，返回 None")
        elif df.empty:
            print("\n测试结果：接口可调用，但返回为空。可能是非交易时间、无实时数据或权限问题。")
        else:
            print(f"\n测试结果：成功，返回 {len(df)} 行数据。")
            print("\n字段列表：")
            print(list(df.columns))

    except Exception as e:
        print("\n测试结果：失败")
        print("错误类型：", type(e).__name__)
        print("错误信息：", e)

        print("\n排查重点：")
        print("1. 是否写了 pro._DataApi__http_url = HTTP_URL")
        print("2. freq 是否为大写，例如 1MIN")
        print("3. ts_code 是否为 000001.SZ / 600000.SH 这种格式")
        print("4. 当前 token 是否包含 rt_min 权限")
        print("5. 是否触发 IP 数量限制")


if __name__ == "__main__":
    main()