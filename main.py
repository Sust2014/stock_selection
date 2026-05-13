# main.py

import argparse
import os
import time
from datetime import datetime

import pandas as pd
from wcwidth import wcswidth

from data_loader import load_a_stock_spot, disable_proxy
from filters import apply_filters
from config import OUTPUT_FILE
from strategy import scan_main_rising_stocks
from concept_analyzer import analyze_concept_resonance


# =========================
# 运行开关
# =========================

# 是否执行第二步：信号扫描
RUN_SIGNAL_SCAN = True

# 是否执行第三步：题材共振分析
# 现在东财接口不稳定时，建议先保持 False
RUN_CONCEPT_ANALYSIS = False

# 主板普通股票涨停阈值。
# 当前工程基础池已经排除 ST、创业板、科创板、北交所，
# 所以这里按主板 10cm 涨停近似判断。
LIMIT_UP_THRESHOLD = 9.95


# =========================
# 策略说明
# =========================

def print_strategy_descriptions():
    """
    打印当前策略说明。
    """

    print("\n当前策略说明：")
    print("=" * 80)

    print("一、突破 / 反转类策略")
    print("-" * 80)

    print("策略1：箱体突破")
    print("条件：")
    print("1. 今日收盘价 > 过去60个交易日最高价，不含今日")
    print("2. 今日成交量 > 过去20日均量 × 1.3")
    print("3. 过去20个交易日K线实体振幅 <= 20%，使用 open/close 取实体上下沿，避开影线误判")
    print()

    print("策略2：底部放量反转")
    print("条件：")
    print("1. 当前价格距40个交易日最低点 < 20%")
    print("2. 今日涨幅 > 5%")
    print("3. 今日成交量 > 过去20日均量 × 2")
    print()

    print("二、主升类策略")
    print("-" * 80)

    print("主升策略1：主升-箱体突破")
    print("条件：今日收盘价 > 过去60天最高收盘价，不含今日；今日成交量 > 过去20日平均成交量 × 1.5")
    print()

    print("主升策略2：主升-底部放量反转")
    print("条件：当前价格距离60日最低收盘价涨幅 < 30%；今日涨幅 > 5%；今日成交量 > 过去20日平均成交量 × 2")
    print()

    print("主升策略3：主升-缩量回调启动")
    print("条件：SMA5 < SMA20；SMA60 > 5天前SMA60；今日收盘价 > SMA5；今日成交量 > 过去20日平均成交量 × 1.5")
    print()

    print("主升策略4：主升-均线多头排列")
    print("条件：SMA5 > SMA10 > SMA20 > SMA60；今日涨幅 > 2%；今日成交量 > 过去20日平均成交量 × 1.2")
    print()

    print("统一二次过滤：")
    print("条件1：过去20个交易日日均成交额 >= 5000万元")
    print("条件2：过去15个交易日内，含今日，至少出现1次涨停；主板涨停定义为单日涨幅 >= 9.95%")

    print("=" * 80)


# =========================
# 终端中文对齐显示
# =========================

def display_width(text) -> int:
    """
    返回字符串在终端中的显示宽度。
    wcswidth 遇到少数不可见字符可能返回 -1，这里做兜底处理。
    """

    text = "" if pd.isna(text) else str(text)
    width = wcswidth(text)
    if width < 0:
        width = len(text)
    return width


def truncate_display_text(text, max_width: int) -> str:
    """
    按终端显示宽度截断字符串，避免日线预览表因为字段太长导致换行错位。
    只影响终端预览，不影响 Excel 导出数据。
    """

    text = "" if pd.isna(text) else str(text)

    if max_width <= 0:
        return ""

    if display_width(text) <= max_width:
        return text

    ellipsis = "…"
    ellipsis_width = display_width(ellipsis)
    keep_width = max(1, max_width - ellipsis_width)

    result = ""
    current_width = 0

    for ch in text:
        ch_width = display_width(ch)
        if current_width + ch_width > keep_width:
            break
        result += ch
        current_width += ch_width

    return result + ellipsis


def align_text(text, width, align="left"):
    """
    按中文显示宽度对齐字符串。
    中文字符通常占2个宽度，英文数字占1个宽度。
    """

    text = "" if pd.isna(text) else str(text)
    text_width = display_width(text)

    padding = width - text_width

    if padding <= 0:
        return text

    if align == "right":
        return " " * padding + text

    if align == "center":
        left = padding // 2
        right = padding - left
        return " " * left + text + " " * right

    return text + " " * padding


def format_date_for_table(value) -> str:
    """
    日线预览表日期格式化。
    把 2026-05-08 00:00:00 这类值压缩为 2026-05-08，避免表格列宽被撑大。
    """

    if pd.isna(value):
        return ""

    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        return str(value)

    return dt.strftime("%Y-%m-%d")


def print_stock_table(df: pd.DataFrame, max_rows: int = 50):
    """
    终端中以纯文本方式展示股票结果。
    解决中文列名、中文股票名、行业名、日期字段和长策略字段导致的错位问题。
    """

    if df is None or df.empty:
        print("没有可展示的股票。")
        return

    show_cols = [
        "代码",
        "名称",
        "题材",
        "K线日期",
        "信号类型",
        "最新价",
        "涨跌幅",
        "涨停状态",
        "行业",
        "突破反转策略",
        "主升策略",
        "命中策略数",
        "市值_亿元",
        "量比",
        "15日涨停",
    ]

    show_cols = [col for col in show_cols if col in df.columns]
    show_df = df[show_cols].copy().head(max_rows)

    if "代码" in show_df.columns:
        show_df["代码"] = show_df["代码"].astype(str).str.zfill(6)

    # 日期格式化：避免输出 2026-05-08 00:00:00 导致 K线日期 列被撑开或错位。
    for col in ["K线日期", "行情日期", "日期"]:
        if col in show_df.columns:
            show_df[col] = show_df[col].map(format_date_for_table)

    # 数字格式化。
    for col in ["最新价", "涨跌幅", "市值_亿元", "量比"]:
        if col in show_df.columns:
            show_df[col] = pd.to_numeric(show_df[col], errors="coerce").map(
                lambda x: "" if pd.isna(x) else f"{x:.2f}"
            )

    for col in ["15日涨停", "命中策略数"]:
        if col in show_df.columns:
            show_df[col] = pd.to_numeric(show_df[col], errors="coerce").map(
                lambda x: "" if pd.isna(x) else str(int(x))
            )

    # 终端预览字段过长时做显示截断，避免整行超出窗口后自动换行造成“错位”。
    # 注意：这里只改 show_df，不影响 Excel 导出的完整策略字段。
    max_display_widths = {
        "题材": 24,
        "信号类型": 16,
        "行业": 12,
        "突破反转策略": 22,
        "主升策略": 34,
    }

    for col, max_width in max_display_widths.items():
        if col in show_df.columns:
            show_df[col] = show_df[col].map(lambda x: truncate_display_text(x, max_width))

    min_widths = {
        "代码": 8,
        "名称": 10,
        "题材": 20,
        "K线日期": 10,
        "信号类型": 14,
        "最新价": 8,
        "涨跌幅": 8,
        "涨停状态": 10,
        "行业": 12,
        "突破反转策略": 18,
        "主升策略": 26,
        "命中策略数": 10,
        "市值_亿元": 10,
        "量比": 8,
        "15日涨停": 10,
    }

    col_widths = {}

    for col in show_cols:
        max_width = display_width(col)

        for value in show_df[col].astype(str).tolist():
            max_width = max(max_width, display_width(value))

        col_widths[col] = max(max_width, min_widths.get(col, 8))

    right_align_cols = {
        "最新价",
        "涨跌幅",
        "市值_亿元",
        "量比",
        "15日涨停",
        "命中策略数",
    }

    header_parts = []

    for col in show_cols:
        align = "right" if col in right_align_cols else "left"
        header_parts.append(align_text(col, col_widths[col], align))

    print(" | ".join(header_parts))

    sep_parts = []

    for col in show_cols:
        sep_parts.append("-" * col_widths[col])

    print("-+-".join(sep_parts))

    for _, row in show_df.iterrows():
        row_parts = []

        for col in show_cols:
            align = "right" if col in right_align_cols else "left"
            row_parts.append(align_text(row[col], col_widths[col], align))

        print(" | ".join(row_parts))

def print_concept_resonance(resonance_summary_df: pd.DataFrame):
    """
    打印题材共振结果。
    """

    print("\n题材共振：")
    print("=" * 80)

    if resonance_summary_df is None or resonance_summary_df.empty:
        print("今日没有发现题材共振。")
        print("=" * 80)
        return

    for _, row in resonance_summary_df.iterrows():
        print(f"{row['概念题材']}：命中 {row['命中数']} 只")
        print(f"股票：{row['命中股票']}")
        print("-" * 80)

    print("=" * 80)


# =========================
# 基础股票池
# =========================

def format_base_pool_df(selected_df: pd.DataFrame) -> pd.DataFrame:
    """
    整理基础股票池字段和单位。
    """

    selected_df = selected_df.copy()

    columns = [
        "代码",
        "名称",
        "最新价",
        "涨跌幅",
        "成交额",
        "总市值_亿元",
        "流通市值",
        "行业",
    ]

    columns = [col for col in columns if col in selected_df.columns]

    selected_df = selected_df[columns]

    if "代码" in selected_df.columns:
        selected_df["代码"] = selected_df["代码"].astype(str).str.zfill(6)

    # 成交额：元 -> 亿元
    if "成交额" in selected_df.columns:
        selected_df["成交额"] = pd.to_numeric(selected_df["成交额"], errors="coerce") / 100000000
        selected_df = selected_df.rename(columns={"成交额": "成交额_亿元"})

    # 流通市值：元 -> 亿元
    if "流通市值" in selected_df.columns:
        selected_df["流通市值"] = pd.to_numeric(selected_df["流通市值"], errors="coerce") / 100000000
        selected_df = selected_df.rename(columns={"流通市值": "流通市值_亿元"})

    number_cols = selected_df.select_dtypes(include=["number"]).columns
    selected_df[number_cols] = selected_df[number_cols].round(2)

    return selected_df


def load_or_create_base_pool() -> pd.DataFrame:
    """
    加载或生成基础股票池。
    优先读取 output/a_stock_selected.xlsx。
    如果文件不存在，才尝试重新获取。
    """

    os.makedirs("output", exist_ok=True)

    if os.path.exists(OUTPUT_FILE):
        print(f"发现已有筛选结果文件：{OUTPUT_FILE}")
        print("直接读取本地文件，不重新获取 A 股数据。")

        selected_df = pd.read_excel(OUTPUT_FILE, dtype={"代码": str})
        selected_df["代码"] = selected_df["代码"].astype(str).str.zfill(6)

        print(f"本地股票池数量：{len(selected_df)}")

        return selected_df

    print("未发现本地筛选结果文件，开始获取 A 股数据...")

    df = load_a_stock_spot()

    print(f"原始股票数量：{len(df)}")

    print("正在执行基础筛选...")
    selected_df = apply_filters(df)

    print(f"基础筛选后股票数量：{len(selected_df)}")

    selected_df = format_base_pool_df(selected_df)

    selected_df.to_excel(OUTPUT_FILE, index=False)

    print(f"筛选结果已导出：{OUTPUT_FILE}")

    backup_file = f"output/a_stock_selected_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    selected_df.to_excel(backup_file, index=False)

    print(f"基础股票池备份已导出：{backup_file}")

    return selected_df


# =========================
# 策略拆分
# =========================

def split_strategy_text(strategy_text: str):
    """
    根据命中策略文本拆分：
    1. 突破反转策略
    2. 主升策略
    3. 信号类型
    """

    if pd.isna(strategy_text):
        strategy_text = ""

    strategies = [
        item.strip()
        for item in str(strategy_text).split("、")
        if item.strip()
    ]

    breakthrough_strategies = []
    main_promotion_strategies = []

    for item in strategies:
        if item.startswith("主升-"):
            main_promotion_strategies.append(item)
        else:
            breakthrough_strategies.append(item)

    signal_types = []

    if breakthrough_strategies:
        signal_types.append("突破反转")

    if main_promotion_strategies:
        signal_types.append("主升")

    return {
        "信号类型": "、".join(signal_types),
        "突破反转策略": "、".join(breakthrough_strategies),
        "主升策略": "、".join(main_promotion_strategies),
        "突破反转策略数": len(breakthrough_strategies),
        "主升策略数": len(main_promotion_strategies),
        "命中策略数": len(strategies),
    }


def prepare_signal_export_df(signal_df: pd.DataFrame, stock_theme_map: dict) -> pd.DataFrame:
    """
    整理最终导出的信号股票。
    """

    signal_df = signal_df.copy()

    signal_df["代码"] = signal_df["代码"].astype(str).str.zfill(6)

    # 用策略计算时的 K 线数据覆盖展示字段，避免使用基础股票池旧行情
    if "收盘价" in signal_df.columns:
        signal_df["最新价"] = signal_df["收盘价"]

    if "今日涨跌幅" in signal_df.columns:
        signal_df["涨跌幅"] = signal_df["今日涨跌幅"]

    # 题材列：只显示共振题材
    signal_df["题材"] = signal_df["代码"].map(stock_theme_map).fillna("")

    # 如果 strategy.py 已经返回了这些字段，就不要重复拆分
    required_strategy_cols = [
        "信号类型",
        "突破反转策略",
        "主升策略",
        "突破反转策略数",
        "主升策略数",
        "命中策略数",
    ]

    has_strategy_cols = all(col in signal_df.columns for col in required_strategy_cols)

    if not has_strategy_cols:
        strategy_info_df = signal_df["命中策略"].apply(split_strategy_text).apply(pd.Series)
        signal_df = pd.concat([signal_df, strategy_info_df], axis=1)

    # 防止重复列名导致 pandas 赋值报错
    signal_df = signal_df.loc[:, ~signal_df.columns.duplicated()].copy()

    # 标记当前K线是否涨停，方便后续按“未涨停 / 已涨停”拆分展示和导出。
    if "涨跌幅" in signal_df.columns:
        pct_series = pd.to_numeric(signal_df["涨跌幅"], errors="coerce")
        signal_df["是否涨停"] = pct_series >= LIMIT_UP_THRESHOLD
        signal_df["涨停状态"] = signal_df["是否涨停"].map(lambda x: "已涨停" if bool(x) else "未涨停")
    else:
        signal_df["是否涨停"] = False
        signal_df["涨停状态"] = "未涨停"

    # 所有数字列统一保留 2 位小数
    number_cols = signal_df.select_dtypes(include=["number"]).columns
    for col in number_cols:
        signal_df[col] = signal_df[col].round(2)

    # 排序：
    # 第一优先级：命中策略数，从大到小
    # 第二优先级：量比，从大到小
    sort_cols = []
    ascending = []

    if "命中策略数" in signal_df.columns:
        sort_cols.append("命中策略数")
        ascending.append(False)

    if "量比" in signal_df.columns:
        sort_cols.append("量比")
        ascending.append(False)

    if sort_cols:
        signal_df = signal_df.sort_values(by=sort_cols, ascending=ascending)

    export_cols = [
        "代码",
        "名称",
        # "题材",
        "K线日期",
        "信号类型",
        "突破反转策略",
        "主升策略",
        "最新价",
        "涨跌幅",
        "涨停状态",
        "行业",
        # "命中策略",
        "命中策略数",
        "总市值_亿元",
        "量比",
        "15日涨停",
    ]

    export_cols = [col for col in export_cols if col in signal_df.columns]

    export_signal_df = signal_df[export_cols].copy()

    export_signal_df = export_signal_df.rename(
        columns={"总市值_亿元": "市值_亿元"}
    )

    return export_signal_df

def split_export_sections(export_signal_df: pd.DataFrame):
    """
    将最终结果拆成：
    1. 突破反转：只要命中突破反转就显示
    2. 主升信号：只显示纯主升，不包含同时命中突破反转的股票
    """

    if export_signal_df is None or export_signal_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    # 第一部分：所有命中突破反转的股票
    breakthrough_df = export_signal_df[
        export_signal_df["信号类型"].astype(str).str.contains("突破反转", na=False)
    ].copy()

    # 第二部分：只保留“纯主升”
    # 即：信号类型包含“主升”，但不包含“突破反转”
    main_promotion_df = export_signal_df[
        (
            export_signal_df["信号类型"].astype(str).str.contains("主升", na=False)
        ) &
        (
            ~export_signal_df["信号类型"].astype(str).str.contains("突破反转", na=False)
        )
    ].copy()

    return breakthrough_df, main_promotion_df


def split_limit_up_sections(export_signal_df: pd.DataFrame):
    """
    将最终结果先按涨停状态拆成两大类：
    1. 未涨停
    2. 已涨停

    后续每一大类内部再继续调用 split_export_sections() 拆分突破反转和纯主升。
    """

    if export_signal_df is None or export_signal_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    df = export_signal_df.copy()

    if "是否涨停" in df.columns:
        is_limit_up = df["是否涨停"].astype(bool)
    elif "涨停状态" in df.columns:
        is_limit_up = df["涨停状态"].astype(str).eq("已涨停")
    elif "涨跌幅" in df.columns:
        is_limit_up = pd.to_numeric(df["涨跌幅"], errors="coerce") >= LIMIT_UP_THRESHOLD
    else:
        is_limit_up = pd.Series(False, index=df.index)

    limit_up_df = df[is_limit_up].copy()
    not_limit_up_df = df[~is_limit_up].copy()

    return not_limit_up_df, limit_up_df


def write_df_to_excel_if_not_empty(writer, df: pd.DataFrame, sheet_name: str):
    """
    写入非空 DataFrame，避免生成一堆空 sheet。
    """

    if df is not None and not df.empty:
        df.to_excel(writer, sheet_name=sheet_name, index=False)


def print_signal_group(title: str, total_df: pd.DataFrame, max_rows: int = 50):
    """
    终端展示：先按未涨停 / 已涨停分大类，
    每个大类里面再拆突破反转、主升信号。
    """

    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)

    if total_df is None or total_df.empty:
        print("没有股票。")
        return

    breakthrough_df, main_promotion_df = split_export_sections(total_df)

    print(f"{title} 总数：{len(total_df)}")
    print(f"{title} - 突破反转数量：{len(breakthrough_df)}")
    print(f"{title} - 主升信号数量：{len(main_promotion_df)}")

    print(f"\n{title} - 突破反转股票预览：")
    if breakthrough_df.empty:
        print("没有突破反转类信号。")
    else:
        print_stock_table(breakthrough_df, max_rows=max_rows)

    print(f"\n{title} - 主升信号股票预览：")
    if main_promotion_df.empty:
        print("没有主升类信号。")
    else:
        print_stock_table(main_promotion_df, max_rows=max_rows)


# =========================
# 主程序
# =========================

def run_daily():
    disable_proxy()

    selected_df = load_or_create_base_pool()

    if selected_df is None or selected_df.empty:
        print("基础股票池为空，程序结束。")
        return

    if not RUN_SIGNAL_SCAN:
        print("已关闭第二步信号扫描。")
        return

    print("\n开始执行第二步：信号策略扫描...")

    signal_df = scan_main_rising_stocks(selected_df)

    if signal_df is None or signal_df.empty:
        print("今日没有股票命中信号。")
        return

    signal_df["代码"] = signal_df["代码"].astype(str).str.zfill(6)

    if RUN_CONCEPT_ANALYSIS:
        print("\n开始执行第三步：东方财富概念题材共振分析...")

        resonance_summary_df, resonance_detail_df, stock_theme_map = analyze_concept_resonance(
            signal_df,
            min_count=3
        )

        if resonance_summary_df is not None and not resonance_summary_df.empty:
            resonance_summary_df = resonance_summary_df.sort_values(
                by="命中数",
                ascending=False
            )
    else:
        print("已关闭第三步题材共振分析。")
        resonance_summary_df = pd.DataFrame()
        resonance_detail_df = pd.DataFrame()
        stock_theme_map = {}

    export_signal_df = prepare_signal_export_df(signal_df, stock_theme_map)

    # 第一层：未涨停 / 已涨停
    not_limit_up_df, limit_up_df = split_limit_up_sections(export_signal_df)

    # 第二层：每一类内部继续按“突破反转 / 纯主升”细分
    all_breakthrough_df, all_main_promotion_df = split_export_sections(export_signal_df)
    not_limit_up_breakthrough_df, not_limit_up_main_promotion_df = split_export_sections(not_limit_up_df)
    limit_up_breakthrough_df, limit_up_main_promotion_df = split_export_sections(limit_up_df)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    signal_output_file = f"output/a_stock_signal_selected_{timestamp}.xlsx"

    with pd.ExcelWriter(signal_output_file, engine="openpyxl") as writer:
        # 总览 sheet
        export_signal_df.to_excel(
            writer,
            sheet_name="全部信号",
            index=False
        )

        # 保留原来的大类 sheet，兼容你之前的查看习惯
        write_df_to_excel_if_not_empty(writer, all_breakthrough_df, "全部_突破反转")
        write_df_to_excel_if_not_empty(writer, all_main_promotion_df, "全部_主升信号")

        # 新增：先分未涨停 / 已涨停，再在内部细分
        write_df_to_excel_if_not_empty(writer, not_limit_up_df, "未涨停_全部")
        write_df_to_excel_if_not_empty(writer, not_limit_up_breakthrough_df, "未涨停_突破反转")
        write_df_to_excel_if_not_empty(writer, not_limit_up_main_promotion_df, "未涨停_主升信号")

        write_df_to_excel_if_not_empty(writer, limit_up_df, "涨停_全部")
        write_df_to_excel_if_not_empty(writer, limit_up_breakthrough_df, "涨停_突破反转")
        write_df_to_excel_if_not_empty(writer, limit_up_main_promotion_df, "涨停_主升信号")

        if resonance_summary_df is not None and not resonance_summary_df.empty:
            resonance_summary_df.to_excel(
                writer,
                sheet_name="题材共振",
                index=False
            )

        if resonance_detail_df is not None and not resonance_detail_df.empty:
            resonance_detail_df.to_excel(
                writer,
                sheet_name="共振明细",
                index=False
            )

    # print_strategy_descriptions()

    print_concept_resonance(resonance_summary_df)

    print(f"全部信号股票数量：{len(export_signal_df)}")
    print(f"未涨停股票数量：{len(not_limit_up_df)}")
    print(f"已涨停股票数量：{len(limit_up_df)}")
    print(f"全部突破反转股票数量：{len(all_breakthrough_df)}")
    print(f"全部主升信号股票数量：{len(all_main_promotion_df)}")
    print(f"信号结果已导出：{signal_output_file}")

    print_signal_group("未涨停信号", not_limit_up_df, max_rows=50)
    print_signal_group("已涨停信号", limit_up_df, max_rows=50)


# =========================
# 统一入口：盘后 / 实时
# =========================

def run_realtime(args):
    """
    盘中实时扫描模式。
    读取 output/a_stock_selected.xlsx 基础池，读取 cache/hist 的 BaoStock 历史K线缓存，
    再用 Tushare 老接口 get_realtime_quotes 获取盘中实时行情。
    """

    from realtime_strategy import scan_realtime_once

    if args.loop:
        while True:
            loop_start_time = time.time()

            print("\n" + "=" * 100)
            print(f"开始新一轮实时扫描：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print("=" * 100)

            try:
                scan_realtime_once(
                    batch_size=args.batch_size,
                    quote_sleep=args.quote_sleep,
                    max_stocks=args.max_stocks,
                    max_workers=args.max_workers,
                    enable_minute=not args.disable_minute,
                    minute_max_stocks=args.minute_max_stocks,
                )
            except KeyboardInterrupt:
                print("用户中断，实时扫描结束。")
                break
            except Exception as e:
                print(f"本轮实时扫描异常：{e}")

            elapsed = time.time() - loop_start_time
            sleep_seconds = max(0, args.interval - elapsed)

            print(
                f"本轮耗时：{elapsed:.2f} 秒，"
                f"目标间隔：{args.interval} 秒，"
                f"等待：{sleep_seconds:.2f} 秒后开始下一轮..."
            )

            try:
                time.sleep(sleep_seconds)
            except KeyboardInterrupt:
                print("用户中断，实时扫描结束。")
                break
    else:
        scan_realtime_once(
            batch_size=args.batch_size,
            quote_sleep=args.quote_sleep,
            max_stocks=args.max_stocks,
            max_workers=args.max_workers,
            enable_minute=not args.disable_minute,
            minute_max_stocks=args.minute_max_stocks,
        )


def parse_args():
    parser = argparse.ArgumentParser(description="A股股票筛选工具：盘后选股 / 盘中实时扫描")

    parser.add_argument(
        "--mode",
        choices=["daily", "realtime"],
        default="daily",
        help="运行模式：daily=盘后日线选股；realtime=盘中实时扫描。默认 daily。",
    )

    parser.add_argument(
        "--loop",
        action="store_true",
        help="realtime 模式下循环执行实时扫描。",
    )

    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="realtime 循环模式下每轮扫描间隔秒数，默认60秒。",
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="realtime 模式下实时行情每批请求股票数量，默认50。",
    )

    parser.add_argument(
        "--quote-sleep",
        type=float,
        default=0.5,
        help="realtime 模式下每批实时行情请求之间的间隔秒数，默认0.5秒。",
    )

    parser.add_argument(
        "--max-workers",
        type=int,
        default=4,
        help="realtime 模式下并发获取实时行情的线程数，默认4。",
    )

    parser.add_argument(
        "--max-stocks",
        type=int,
        default=0,
        help="测试用：realtime 模式下只扫描前N只股票。默认0表示扫描全部。",
    )

    parser.add_argument(
        "--disable-minute",
        action="store_true",
        help="realtime 模式下关闭 5分钟/30分钟 B点确认。默认开启。",
    )

    parser.add_argument(
        "--minute-max-stocks",
        type=int,
        default=0,
        help="测试用：分钟级确认只处理前N只日线命中股票。默认0表示全部。",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.mode == "realtime":
        run_realtime(args)
    else:
        run_daily()


if __name__ == "__main__":
    main()
