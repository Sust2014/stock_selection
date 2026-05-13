import os
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from collections import deque
import pandas as pd
from wcwidth import wcswidth
import tushare as ts
from config import TUSHARE_TOKEN, TUSHARE_HTTP_URL

MINUTE_CACHE_DIR = "cache/minute"
MINUTE_OUTPUT_DIR = "output/minute_buy_points"
DEFAULT_MINUTE_DAYS = 5

_TUSHARE_PRO = None
_TUSHARE_PRO_LOCK = threading.Lock()

# rt_min 接口限制：每分钟最多 50 次请求。
# 这里默认控制在 45 次/分钟，留一点余量，避免多线程并发时触发限流。
RT_MIN_MAX_CALLS_PER_MINUTE = int(os.getenv("RT_MIN_MAX_CALLS_PER_MINUTE", "45"))
_RT_MIN_CALL_TIMES = deque()
_RT_MIN_RATE_LOCK = threading.Lock()

# stk_mins 盘后历史分钟接口限速。
# 官方没有给出固定的每分钟次数，这里默认 80 次/分钟，可通过环境变量调整。
# 如果仍出现“您请求速度过快”，把环境变量调低，例如 60；如果想更快可以试 100。
STK_MINS_MAX_CALLS_PER_MINUTE = int(os.getenv("STK_MINS_MAX_CALLS_PER_MINUTE", "80"))
STK_MINS_RETRY_TIMES = int(os.getenv("STK_MINS_RETRY_TIMES", "3"))
STK_MINS_RETRY_SLEEP_SECONDS = float(os.getenv("STK_MINS_RETRY_SLEEP_SECONDS", "10"))
_STK_MINS_CALL_TIMES = deque()
_STK_MINS_RATE_LOCK = threading.Lock()

# 控制台单行刷新锁，避免多线程限速提示互相抢打印，导致不断往下刷屏。
_CONSOLE_PRINT_LOCK = threading.Lock()
_CONSOLE_LINE_WIDTH = 180


def print_same_line(message: str):
    """在控制台原地刷新一行，不换行。"""
    with _CONSOLE_PRINT_LOCK:
        text = str(message)
        print("\r" + text.ljust(_CONSOLE_LINE_WIDTH), end="\r", flush=True)


# 盘后校准进度状态。
# 限速等待发生在线程池子线程里，普通进度统计发生在主线程里，
# 所以用一个共享状态，让“限速等待提示”也能带上当前进度。
_CALIBRATION_PROGRESS_LOCK = threading.Lock()
_CALIBRATION_PROGRESS = {
    "enabled": False,
    "finished": 0,
    "total": 0,
    "success_items": 0,
    "failed_items": 0,
    "start_time": None,
}


def set_calibration_progress(
    *,
    enabled=None,
    finished=None,
    total=None,
    success_items=None,
    failed_items=None,
    start_time=None,
):
    """更新盘后校准共享进度。"""
    with _CALIBRATION_PROGRESS_LOCK:
        if enabled is not None:
            _CALIBRATION_PROGRESS["enabled"] = bool(enabled)
        if finished is not None:
            _CALIBRATION_PROGRESS["finished"] = int(finished)
        if total is not None:
            _CALIBRATION_PROGRESS["total"] = int(total)
        if success_items is not None:
            _CALIBRATION_PROGRESS["success_items"] = int(success_items)
        if failed_items is not None:
            _CALIBRATION_PROGRESS["failed_items"] = int(failed_items)
        if start_time is not None:
            _CALIBRATION_PROGRESS["start_time"] = start_time


def get_calibration_progress_text() -> str:
    """生成当前盘后校准进度文本，用于限速等待时原地刷新。"""
    with _CALIBRATION_PROGRESS_LOCK:
        enabled = _CALIBRATION_PROGRESS.get("enabled", False)
        finished = int(_CALIBRATION_PROGRESS.get("finished", 0) or 0)
        total = int(_CALIBRATION_PROGRESS.get("total", 0) or 0)
        success_items = int(_CALIBRATION_PROGRESS.get("success_items", 0) or 0)
        failed_items = int(_CALIBRATION_PROGRESS.get("failed_items", 0) or 0)
        start_time = _CALIBRATION_PROGRESS.get("start_time")

    if not enabled or total <= 0:
        return ""

    if start_time:
        elapsed = time.time() - start_time
    else:
        elapsed = 0.0

    if finished > 0:
        avg = elapsed / finished
        remain = avg * (total - finished)
        remain_text = f"{remain:.1f} 秒"
    else:
        remain_text = "计算中"

    return (
        f"进度：{finished}/{total} | "
        f"成功周期数：{success_items} | "
        f"失败周期数：{failed_items} | "
        f"预计剩余：{remain_text}"
    )


def print_calibration_status(prefix: str):
    """打印带当前盘后校准进度的单行状态。"""
    progress_text = get_calibration_progress_text()
    if progress_text:
        print_same_line(f"{prefix} | {progress_text}")
    else:
        print_same_line(prefix)


def wait_before_rt_min_call():
    """
    rt_min 全局限速器。

    你的分钟级扫描会并发处理多只股票，并且每只股票通常会请求 5m 和 30m，
    开启 1分钟精确买点时还会额外请求 1m。
    rt_min 每分钟最多请求 50 次，所以这里在所有线程之间共享一个限速队列。
    """

    max_calls = max(1, int(RT_MIN_MAX_CALLS_PER_MINUTE or 45))

    while True:
        with _RT_MIN_RATE_LOCK:
            now = time.time()

            while _RT_MIN_CALL_TIMES and now - _RT_MIN_CALL_TIMES[0] >= 60:
                _RT_MIN_CALL_TIMES.popleft()

            if len(_RT_MIN_CALL_TIMES) < max_calls:
                _RT_MIN_CALL_TIMES.append(now)
                return

            wait_seconds = 60 - (now - _RT_MIN_CALL_TIMES[0]) + 0.5

        wait_seconds = max(0.5, wait_seconds)
        print_same_line(f"rt_min 请求接近频率限制，等待 {wait_seconds:.1f} 秒后继续...")
        time.sleep(wait_seconds)



def wait_before_stk_mins_call():
    """
    stk_mins 全局轻量限速器。

    只用于盘后校准的 stk_mins 请求，不影响盘中 rt_min 实时扫描。
    多线程时所有线程共享一个滑动窗口，避免瞬间请求过快触发 Tushare 限频。
    """

    max_calls = max(1, int(STK_MINS_MAX_CALLS_PER_MINUTE or 80))

    while True:
        with _STK_MINS_RATE_LOCK:
            now = time.time()

            while _STK_MINS_CALL_TIMES and now - _STK_MINS_CALL_TIMES[0] >= 60:
                _STK_MINS_CALL_TIMES.popleft()

            if len(_STK_MINS_CALL_TIMES) < max_calls:
                _STK_MINS_CALL_TIMES.append(now)
                return

            wait_seconds = 60 - (now - _STK_MINS_CALL_TIMES[0]) + 0.2

        wait_seconds = max(0.2, wait_seconds)
        print_calibration_status(f"stk_mins 请求接近频率限制，等待 {wait_seconds:.1f} 秒后继续...")
        time.sleep(wait_seconds)


def get_tushare_pro_cached():
    """
    全局复用 Tushare Pro 对象。
    避免每只股票、每个周期都重新初始化一次 pro，减少分钟级扫描耗时。
    """

    global _TUSHARE_PRO

    if _TUSHARE_PRO is not None:
        return _TUSHARE_PRO

    with _TUSHARE_PRO_LOCK:
        if _TUSHARE_PRO is None:
            pro = ts.pro_api(TUSHARE_TOKEN)
            if TUSHARE_HTTP_URL:
                pro._DataApi__http_url = TUSHARE_HTTP_URL
            _TUSHARE_PRO = pro

    return _TUSHARE_PRO


def is_a_share_trading_time_now() -> bool:
    """
    判断当前是否处于A股连续竞价/集合竞价附近时间。
    非交易时间直接使用本地分钟缓存，避免 loop 模式每轮重复请求 Tushare。
    """

    now = datetime.now()

    # 周六周日不请求历史分钟接口
    if now.weekday() >= 5:
        return False

    current = now.time()

    morning_start = datetime.strptime("09:15", "%H:%M").time()
    morning_end = datetime.strptime("11:35", "%H:%M").time()
    afternoon_start = datetime.strptime("12:55", "%H:%M").time()
    afternoon_end = datetime.strptime("15:10", "%H:%M").time()

    return (
        morning_start <= current <= morning_end
        or afternoon_start <= current <= afternoon_end
    )

# =========================
# 文本对齐
# =========================
def align_text(text, width, align="left"):
    text = "" if pd.isna(text) else str(text)
    text_width = wcswidth(text)
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

# =========================
# Tushare 分钟数据增量加载
# =========================

def get_ts_code(code: str) -> str:
    """把 6 位股票代码转成 Tushare ts_code。"""
    code = str(code).zfill(6)
    if code.startswith(("600", "601", "603", "605", "688")):
        return f"{code}.SH"
    return f"{code}.SZ"


def get_rt_min_freq(frequency: str) -> str:
    """把 1/5/15/30/60 转成 rt_min 要求的大写频率。"""
    freq = str(frequency).strip().upper()
    freq = freq.replace("MIN", "").replace("M", "")

    freq_map = {
        "1": "1MIN",
        "5": "5MIN",
        "15": "15MIN",
        "30": "30MIN",
        "60": "60MIN",
    }

    if freq not in freq_map:
        raise ValueError(f"不支持的分钟周期：{frequency}，rt_min 只支持 1/5/15/30/60")

    return freq_map[freq]


def normalize_stk_mins_df(df: pd.DataFrame, code: str) -> pd.DataFrame:
    """
    统一分钟K线字段，输出策略层可直接使用的字段。

    兼容两类 Tushare 返回：
    1. stk_mins: trade_time/open/high/low/close/vol/amount
    2. rt_min:   time/open/close/high/low/vol/amount

    统一输出：datetime、开盘、最高、最低、收盘、成交量、成交额、代码
    """
    code = str(code).zfill(6)

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()

    rename_map = {
        "open": "开盘",
        "high": "最高",
        "low": "最低",
        "close": "收盘",
        "vol": "成交量",
        "amount": "成交额",
        "trade_time": "datetime",
        "time": "datetime",
    }
    df = df.rename(columns=rename_map)

    if "datetime" not in df.columns:
        return pd.DataFrame()

    # rt_min 正常返回是 "YYYY-MM-DD HH:MM:SS"，这里兼容只有 HH:MM:SS 的情况。
    dt_text = df["datetime"].astype(str)
    only_time_mask = dt_text.str.match(r"^\d{2}:\d{2}:\d{2}$", na=False)
    if only_time_mask.any():
        today = datetime.now().strftime("%Y-%m-%d")
        dt_text.loc[only_time_mask] = today + " " + dt_text.loc[only_time_mask]

    df["datetime"] = pd.to_datetime(dt_text, errors="coerce")
    df = df.dropna(subset=["datetime"])
    df["代码"] = code

    for col in ["开盘", "最高", "最低", "收盘", "成交量", "成交额"]:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")

    keep_cols = ["datetime", "开盘", "最高", "最低", "收盘", "成交量", "成交额", "代码"]
    df = df[keep_cols].dropna(subset=["开盘", "最高", "最低", "收盘"])
    df = df.drop_duplicates(subset=["datetime"], keep="last")
    df = df.sort_values("datetime").reset_index(drop=True)

    return df


def fetch_rt_min(code: str, frequency: str) -> pd.DataFrame:
    """
    使用 Tushare rt_min 获取盘中实时分钟K线。

    注意：rt_min 通常返回当天已经形成的分钟K线。
    为了让 30分钟结构判断有足够K线，load_tushare_minute() 会把 rt_min
    返回结果追加到 cache/minute 的历史缓存中，再统一保留最近 minute_days 天。
    """
    code = str(code).zfill(6)
    pro = get_tushare_pro_cached()
    freq = get_rt_min_freq(frequency)

    # 全局限速，避免并发扫描时超过 rt_min 每分钟 50 次请求限制。
    wait_before_rt_min_call()

    df_new = pro.rt_min(
        ts_code=get_ts_code(code),
        freq=freq,
    )

    return normalize_stk_mins_df(df_new, code)


def get_stk_mins_freq(frequency: str) -> str:
    """把 1/5/15/30/60 转成 stk_mins 要求的频率。"""
    freq = str(frequency).strip().lower()
    freq = freq.replace("min", "").replace("m", "")
    if freq not in {"1", "5", "15", "30", "60"}:
        raise ValueError(f"不支持的分钟周期：{frequency}，stk_mins 只支持 1/5/15/30/60")
    return f"{freq}min"


def format_stk_mins_dt(value) -> str:
    """stk_mins start_date/end_date 使用 datetime 字符串格式。"""
    dt = pd.to_datetime(value, errors="coerce")
    if pd.isna(dt):
        dt = datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_frequency_minutes(frequency: str) -> int:
    """把分钟周期转成整数分钟。"""
    freq = str(frequency).strip().lower().replace("min", "").replace("m", "")
    try:
        return int(freq)
    except Exception:
        return 5


def is_cache_updated_after_market(latest_dt, frequency: str) -> bool:
    """
    判断本地缓存是否已经更新到今天收盘附近。

    对 1m/5m/30m 都统一用 14:55 作为盘后判断阈值，避免因为最后一根K线时间
    在不同数据源里显示为 14:55、15:00 造成反复请求。
    """
    if pd.isna(latest_dt):
        return False

    latest_dt = pd.to_datetime(latest_dt, errors="coerce")
    if pd.isna(latest_dt):
        return False

    now = datetime.now()
    if latest_dt.date() < now.date():
        return False

    close_check_time = datetime.strptime("14:55", "%H:%M").time()
    return latest_dt.time() >= close_check_time


def fetch_stk_mins(
    code: str,
    frequency: str,
    days: int = DEFAULT_MINUTE_DAYS,
    start_dt=None,
    end_dt=None,
) -> pd.DataFrame:
    """
    使用 stk_mins 拉取历史分钟K线。

    - start_dt/end_dt 为空：拉最近 days 天。
    - start_dt/end_dt 不为空：按指定 datetime 区间拉取，用于盘后增量更新。
    - 带轻量限速和“请求速度过快”自动重试。
    """
    code = str(code).zfill(6)
    frequency = str(frequency)
    pro = get_tushare_pro_cached()

    if start_dt is None:
        start_dt = datetime.now() - timedelta(days=days)
    if end_dt is None:
        end_dt = datetime.now()

    start_date = format_stk_mins_dt(start_dt)
    end_date = format_stk_mins_dt(end_dt)
    freq = get_stk_mins_freq(frequency)

    last_error = ""
    retry_times = max(0, int(STK_MINS_RETRY_TIMES or 0))

    for attempt in range(retry_times + 1):
        try:
            wait_before_stk_mins_call()

            df_new = pro.stk_mins(
                ts_code=get_ts_code(code),
                asset="E",
                start_date=start_date,
                end_date=end_date,
                freq=freq,
            )

            df_new = normalize_stk_mins_df(df_new, code)
            if df_new.empty:
                return pd.DataFrame()

            cutoff = datetime.now() - timedelta(days=days)
            df_new = df_new[df_new["datetime"] >= cutoff].copy()
            df_new = df_new.sort_values("datetime").reset_index(drop=True)

            return df_new

        except Exception as e:
            last_error = str(e)
            is_rate_error = "请求速度过快" in last_error or "频率" in last_error or "rate" in last_error.lower()

            if is_rate_error and attempt < retry_times:
                sleep_seconds = STK_MINS_RETRY_SLEEP_SECONDS * (attempt + 1)
                print_calibration_status(
                    f"{code} {frequency}m stk_mins 请求过快，"
                    f"等待 {sleep_seconds:.1f} 秒后重试 {attempt + 1}/{retry_times}..."
                )
                time.sleep(sleep_seconds)
                continue

            raise Exception(last_error)

    return pd.DataFrame()


def overwrite_stk_mins_cache(code: str, frequency: str, days: int = DEFAULT_MINUTE_DAYS) -> dict:
    """
    盘后校准：使用 stk_mins 增量更新本地分钟缓存。

    说明：保留函数名是为了不改 realtime_strategy.py 的调用链。
    现在不再全量覆盖，而是：
    1. 读取 cache/minute/{代码}_{周期}m.csv
    2. 找到本地最新 datetime
    3. 如果已更新到今天收盘附近，直接跳过请求
    4. 否则从最新时间前推一个周期开始拉取，合并去重
    5. 只保留最近 days 天，写回 csv
    """
    code = str(code).zfill(6)
    frequency = str(frequency)
    cache_file = os.path.join(MINUTE_CACHE_DIR, f"{code}_{frequency}m.csv")
    os.makedirs(MINUTE_CACHE_DIR, exist_ok=True)

    old_df = pd.DataFrame()
    if os.path.exists(cache_file):
        try:
            old_df = pd.read_csv(cache_file, dtype={"代码": str})
            old_df["datetime"] = pd.to_datetime(old_df["datetime"], errors="coerce")
            old_df = old_df.dropna(subset=["datetime"])
            old_df = old_df.drop_duplicates(subset=["datetime"], keep="last")
            old_df = old_df.sort_values("datetime")

            cutoff = datetime.now() - timedelta(days=days)
            old_df = old_df[old_df["datetime"] >= cutoff].copy()
        except Exception:
            old_df = pd.DataFrame()

    latest_dt = old_df["datetime"].max() if not old_df.empty else pd.NaT

    if not old_df.empty and is_cache_updated_after_market(latest_dt, frequency):
        return {
            "success": True,
            "code": code,
            "frequency": frequency,
            "rows": len(old_df),
            "new_rows": 0,
            "update_mode": "skip",
            "latest_dt": latest_dt,
            "cache_file": cache_file,
            "error": "已更新到今天收盘附近，跳过请求",
        }

    try:
        freq_minutes = get_frequency_minutes(frequency)

        if old_df.empty or pd.isna(latest_dt):
            # 无缓存：拉最近 days 天。
            start_dt = datetime.now() - timedelta(days=days)
            update_mode = "init"
            old_count = 0
        else:
            # 有缓存：从最新时间前推一个周期开始拉，避免边界漏K线；重复数据后续去重。
            start_dt = pd.to_datetime(latest_dt) - timedelta(minutes=max(1, freq_minutes))
            update_mode = "incremental"
            old_count = len(old_df)

        end_dt = datetime.now()
        df_new = fetch_stk_mins(code, frequency, days=days, start_dt=start_dt, end_dt=end_dt)

        if df_new is None or df_new.empty:
            if not old_df.empty:
                return {
                    "success": True,
                    "code": code,
                    "frequency": frequency,
                    "rows": len(old_df),
                    "new_rows": 0,
                    "update_mode": "no_new",
                    "latest_dt": latest_dt,
                    "cache_file": cache_file,
                    "error": "stk_mins 增量返回为空，保留旧缓存",
                }

            return {
                "success": False,
                "code": code,
                "frequency": frequency,
                "rows": 0,
                "new_rows": 0,
                "update_mode": "error",
                "latest_dt": pd.NaT,
                "cache_file": cache_file,
                "error": "stk_mins 返回为空，且本地无缓存",
            }

        if not old_df.empty:
            df = pd.concat([old_df, df_new], ignore_index=True)
        else:
            df = df_new

        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
        df = df.dropna(subset=["datetime"])
        df = df.drop_duplicates(subset=["datetime"], keep="last")
        df = df.sort_values("datetime")

        cutoff = datetime.now() - timedelta(days=days)
        df = df[df["datetime"] >= cutoff].copy()

        df.to_csv(cache_file, index=False, encoding="utf-8-sig")
        latest_after = df["datetime"].max() if not df.empty else pd.NaT

        if old_df.empty:
            new_rows = len(df)
        else:
            old_times = set(pd.to_datetime(old_df["datetime"], errors="coerce").dropna().astype(str).tolist())
            new_times = set(pd.to_datetime(df["datetime"], errors="coerce").dropna().astype(str).tolist())
            new_rows = max(0, len(new_times - old_times))

        return {
            "success": True,
            "code": code,
            "frequency": frequency,
            "rows": len(df),
            "new_rows": int(new_rows),
            "update_mode": update_mode,
            "latest_dt": latest_after,
            "cache_file": cache_file,
            "error": "",
        }

    except Exception as e:
        return {
            "success": False,
            "code": code,
            "frequency": frequency,
            "rows": len(old_df),
            "new_rows": 0,
            "update_mode": "error",
            "latest_dt": latest_dt,
            "cache_file": cache_file,
            "error": str(e),
        }


def calibrate_one_stock_minute_cache(code: str, frequencies: list[str], days: int = DEFAULT_MINUTE_DAYS) -> dict:
    """单只股票盘后分钟缓存校准。"""
    code = str(code).zfill(6)
    details = []

    for frequency in frequencies:
        details.append(overwrite_stk_mins_cache(code, str(frequency), days=days))

    success_count = sum(1 for item in details if item.get("success"))
    failed_count = len(details) - success_count

    return {
        "code": code,
        "success_count": success_count,
        "failed_count": failed_count,
        "details": details,
    }


def calibrate_minute_cache_after_market(
    stock_df: pd.DataFrame,
    max_stocks: int = 0,
    minute_days: int = DEFAULT_MINUTE_DAYS,
    max_workers: int = 4,
    include_1m: bool = False,
) -> pd.DataFrame:
    """
    盘后分钟缓存校准入口。

    默认校准 1m / 5m / 30m。
    include_1m 参数保留兼容旧调用，但当前盘后校准默认已经包含 1m。
    """
    if stock_df is None or stock_df.empty:
        print("盘后分钟校准：股票池为空，跳过。")
        return pd.DataFrame()

    df = stock_df.copy()
    df["代码"] = df["代码"].astype(str).str.zfill(6)

    if max_stocks and max_stocks > 0:
        df = df.head(max_stocks).copy()

    # 盘后校准默认直接更新 1m / 5m / 30m，不再需要额外开关。
    frequencies = ["1", "5", "30"]

    total = len(df)
    max_workers = max(1, int(max_workers or 1))
    max_workers = min(max_workers, total)
    minute_days = int(minute_days or DEFAULT_MINUTE_DAYS)

    print(
        f"\n开始盘后分钟K线校准：股票数 {total}，"
        f"周期：{','.join(f + 'm' for f in frequencies)}，"
        f"范围：最近 {minute_days} 天，并发数：{max_workers}"
    )
    print(
        "说明：本操作使用 stk_mins 从本地缓存最新时间开始增量拉取，"
        "并与 cache/minute 下对应 csv 合并去重。"
    )
    print(
        f"限速：stk_mins 默认每分钟最多 {STK_MINS_MAX_CALLS_PER_MINUTE} 次请求，"
        "可通过环境变量 STK_MINS_MAX_CALLS_PER_MINUTE 调整。"
    )

    result_rows = []
    start_time = time.time()
    set_calibration_progress(
        enabled=True,
        finished=0,
        total=total,
        success_items=0,
        failed_items=0,
        start_time=start_time,
    )

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for _, row in df.iterrows():
            code = str(row["代码"]).zfill(6)
            future = executor.submit(
                calibrate_one_stock_minute_cache,
                code,
                frequencies,
                minute_days,
            )
            future_map[future] = code

        finished = 0
        success_items = 0
        failed_items = 0

        for future in as_completed(future_map):
            finished += 1
            code = future_map.get(future, "")

            try:
                item = future.result()
            except Exception as e:
                item = {
                    "code": code,
                    "success_count": 0,
                    "failed_count": len(frequencies),
                    "details": [
                        {
                            "success": False,
                            "code": code,
                            "frequency": f,
                            "rows": 0,
                            "new_rows": 0,
                            "update_mode": "error",
                            "latest_dt": pd.NaT,
                            "cache_file": "",
                            "error": str(e),
                        }
                        for f in frequencies
                    ],
                }

            for detail in item.get("details", []):
                if detail.get("success"):
                    success_items += 1
                else:
                    failed_items += 1

                result_rows.append({
                    "代码": detail.get("code", code),
                    "周期": f"{detail.get('frequency', '')}m",
                    "是否成功": bool(detail.get("success")),
                    "数据行数": int(detail.get("rows", 0) or 0),
                    "新增行数": int(detail.get("new_rows", 0) or 0),
                    "更新方式": detail.get("update_mode", ""),
                    "最新时间": detail.get("latest_dt", ""),
                    "缓存文件": detail.get("cache_file", ""),
                    "错误信息": detail.get("error", ""),
                })

            set_calibration_progress(
                finished=finished,
                success_items=success_items,
                failed_items=failed_items,
            )

            if finished % 5 == 0 or finished == total:
                elapsed = time.time() - start_time
                avg = elapsed / finished if finished else 0
                remain = avg * (total - finished)
                print_calibration_status(
                    f"盘后校准进度：{finished}/{total}"
                )

    print()
    set_calibration_progress(enabled=False)

    result_df = pd.DataFrame(result_rows)
    os.makedirs("output/minute_calibration", exist_ok=True)
    output_file = os.path.join(
        "output/minute_calibration",
        f"minute_calibration_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
    )
    result_df.to_excel(output_file, index=False)

    elapsed = time.time() - start_time
    print(f"盘后分钟K线校准完成，耗时：{elapsed:.2f} 秒")
    print(f"校准明细已保存：{output_file}")
    print(f"成功周期数：{success_items}，失败周期数：{failed_items}")

    if failed_items > 0:
        failed_df = result_df[~result_df["是否成功"]].copy()
        print("失败样例：")
        print(failed_df.head(10).to_string(index=False))

    return result_df


def load_tushare_minute(
    code: str,
    frequency: str,
    days: int = DEFAULT_MINUTE_DAYS,
    use_cache: bool = True,
    force_update: bool = False,
) -> pd.DataFrame:
    """
    读取或增量更新 Tushare rt_min 实时分钟 K 线。
    支持文件缓存，格式：000001_1m.csv / 000001_5m.csv / 000001_30m.csv

    force_update=True 时：
    - 不管当前是否交易时间，都尝试请求 Tushare rt_min；
    - 用于提前验证 1分钟/5分钟/30分钟 B点扫描速度；
    - 仍然会和本地缓存合并、去重、只保留最近 days 天，不会让文件无限变大。
    """
    code = str(code).zfill(6)
    frequency = str(frequency)
    cache_file = os.path.join(MINUTE_CACHE_DIR, f"{code}_{frequency}m.csv")
    os.makedirs(MINUTE_CACHE_DIR, exist_ok=True)

    old_df = pd.DataFrame()
    if use_cache and os.path.exists(cache_file):
        try:
            old_df = pd.read_csv(cache_file, dtype={"代码": str})
            old_df["datetime"] = pd.to_datetime(old_df["datetime"])
            old_df = old_df.sort_values("datetime")
        except Exception:
            old_df = pd.DataFrame()

    # 增量开始日期
    # 即使缓存里已有旧的30天数据，也只保留最近 days 天。
    if not old_df.empty:
        cutoff = datetime.now() - timedelta(days=days)
        old_df = old_df[old_df["datetime"] >= cutoff].copy()

        if not old_df.empty:
            latest_dt = pd.to_datetime(old_df["datetime"], errors="coerce").max()
            now = datetime.now()

            # 非交易时间直接使用本地缓存。
            # force_update=True 时跳过这里，用于盘后校准或手动强制更新。
            if (not force_update) and (not is_a_share_trading_time_now()):
                return old_df.sort_values("datetime")

            # 交易时间内，不能只判断“是不是今天”，必须判断最新分钟是否足够接近当前时间。
            # 不同周期允许不同延迟：
            # 1分钟：允许落后2分钟
            # 5分钟：允许落后6分钟
            # 30分钟：允许落后35分钟
            freq_text = str(frequency).replace("min", "").replace("m", "")
            allow_lag_minutes_map = {
                "1": 2,
                "5": 6,
                "15": 16,
                "30": 35,
                "60": 65,
            }
            allow_lag_minutes = allow_lag_minutes_map.get(freq_text, 6)

            if (not force_update) and pd.notna(latest_dt):
                if latest_dt >= now - timedelta(minutes=allow_lag_minutes):
                    return old_df.sort_values("datetime")

            # 如果缓存明显落后，就继续请求 rt_min 补当天实时分钟数据。
        else:
            pass
    else:
        pass

    try:
        # Tushare rt_min 获取盘中实时分钟数据。
        # rt_min 不需要 start_date/end_date，只需要 ts_code + 大写 freq。
        df_new = fetch_rt_min(code, frequency)
        if df_new is None or df_new.empty:
            return old_df

        # 合并增量
        if not old_df.empty:
            df = pd.concat([old_df, df_new], ignore_index=True)
        else:
            df = df_new
        df = df.drop_duplicates(subset=["datetime"], keep="last")
        df = df.sort_values("datetime")
        cutoff = datetime.now() - timedelta(days=days)
        df = df[df["datetime"] >= cutoff].copy()

        # 保存缓存
        df.to_csv(cache_file, index=False, encoding="utf-8-sig")
        return df
    except Exception as e:
        print(f"{code} Tushare rt_min 分钟数据获取失败：{e}")
        return old_df

# =========================
# 指标计算
# =========================
def prepare_minute_data(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).sort_values("datetime")
    for col in ["开盘", "最高", "最低", "收盘", "成交量", "成交额"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["MA5"] = df["收盘"].rolling(5).mean()
    df["MA10"] = df["收盘"].rolling(10).mean()
    df["MA20"] = df["收盘"].rolling(20).mean()
    df["VOL20"] = df["成交量"].shift(1).rolling(20).mean()
    df["前12根最高"] = df["最高"].shift(1).rolling(12).max()
    df["前12根最低"] = df["最低"].shift(1).rolling(12).min()
    df["前12根振幅"] = df["前12根最高"] / df["前12根最低"] - 1
    return df

# =========================
# 构建日线分组
# =========================
def build_daily_group(row: pd.Series) -> str:
    text = "、".join([str(row.get("突破反转策略","")), str(row.get("主升策略","")), str(row.get("命中策略",""))])
    groups = []
    if "主升-缩量回调启动" in text or "主升-均线多头排列" in text:
        groups.append("主升趋势类")
    if "箱体突破" in text:
        groups.append("突破类")
    if "底部放量反转" in text:
        groups.append("放量启动类")
    if not groups:
        groups.append("其他")
    return "、".join(dict.fromkeys(groups))

# =========================
# 分钟策略判定
# =========================
from strategies import evaluate_minute_strategies

def evaluate_minute_buy_point(
    row: pd.Series,
    df5: pd.DataFrame,
    df30: pd.DataFrame,
    df1: pd.DataFrame = pd.DataFrame(),
    enable_1m_buy: bool = False,
):
    group = build_daily_group(row)
    is_hit, buy_points, structure_msg = evaluate_minute_strategies(
        row=row,
        df1=df1,
        df5=df5,
        df30=df30,
        daily_group=group,
        enable_1m_buy=enable_1m_buy,
    )
    return is_hit, buy_points, group, structure_msg

# =========================
# 保存分钟结果
# =========================
def save_minute_buy_points(df: pd.DataFrame):
    if df is None or df.empty:
        return ""
    os.makedirs(MINUTE_OUTPUT_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    output_file = os.path.join(MINUTE_OUTPUT_DIR, f"minute_buy_points_{today}.xlsx")
    save_df = df.copy()
    save_df["代码"] = save_df["代码"].astype(str).str.zfill(6)
    save_df["保存时间"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_df["分钟Key"] = save_df["代码"] + "|" + save_df["分钟B点"].fillna("").astype(str) + "|" + save_df["触发时间"].fillna("").astype(str)
    if os.path.exists(output_file):
        try:
            old_df = pd.read_excel(output_file, dtype={"代码": str})
            old_keys = set(old_df["分钟Key"].dropna().astype(str).tolist()) if "分钟Key" in old_df else set()
            new_df = save_df[~save_df["分钟Key"].astype(str).isin(old_keys)].copy()
            if not new_df.empty:
                save_df = pd.concat([old_df, new_df], ignore_index=True)
        except Exception:
            pass
    save_df.to_excel(output_file, index=False)
    return output_file



def print_minute_buy_point_table(df: pd.DataFrame, max_rows: int = 30):
    """
    打印分钟级B点预览。
    重点展示 30分钟结构、日线分组、分钟B点，方便看到是否命中缠论一买/二买/三买。
    """

    if df is None or df.empty:
        print("没有可展示的分钟级B点。")
        return

    show_cols = [
        "代码",
        "名称",
        "触发时间",
        "最新价",
        "涨跌幅",
        "行业",
        "日线分组",
        "30分钟结构",
        "分钟B点",
    ]

    show_cols = [col for col in show_cols if col in df.columns]
    show_df = df[show_cols].copy().head(max_rows)

    if "代码" in show_df.columns:
        show_df["代码"] = show_df["代码"].astype(str).str.zfill(6)

    for col in ["最新价", "涨跌幅"]:
        if col in show_df.columns:
            show_df[col] = pd.to_numeric(show_df[col], errors="coerce").map(
                lambda x: "" if pd.isna(x) else f"{x:.2f}"
            )

    min_widths = {
        "代码": 8,
        "名称": 10,
        "触发时间": 20,
        "最新价": 8,
        "涨跌幅": 8,
        "行业": 12,
        "日线分组": 16,
        "30分钟结构": 18,
        "分钟B点": 36,
    }

    col_widths = {}
    for col in show_cols:
        max_width = wcswidth(col)
        for value in show_df[col].astype(str).tolist():
            max_width = max(max_width, wcswidth(value))
        col_widths[col] = max(max_width, min_widths.get(col, 8))

    right_align_cols = {"最新价", "涨跌幅"}

    header_parts = []
    for col in show_cols:
        align = "right" if col in right_align_cols else "left"
        header_parts.append(align_text(col, col_widths[col], align))

    print(" | ".join(header_parts))
    print("-+-".join("-" * col_widths[col] for col in show_cols))

    for _, row in show_df.iterrows():
        row_parts = []
        for col in show_cols:
            align = "right" if col in right_align_cols else "left"
            row_parts.append(align_text(row[col], col_widths[col], align))
        print(" | ".join(row_parts))

# =========================
# 扫描候选股票
# =========================
def trim_minute_days(df: pd.DataFrame, days: int = DEFAULT_MINUTE_DAYS) -> pd.DataFrame:
    """
    只保留最近 N 天分钟数据。
    这里用于避免旧缓存里保留 30 天甚至更多分钟数据，导致实时 B 点确认变慢。
    """

    if df is None or df.empty:
        return pd.DataFrame()

    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"])

    cutoff = datetime.now() - timedelta(days=days)
    df = df[df["datetime"] >= cutoff].copy()
    df = df.sort_values("datetime")

    return df


def scan_one_minute_candidate(
    row: pd.Series,
    minute_days: int = DEFAULT_MINUTE_DAYS,
    enable_1m_buy: bool = False,
    force_update_minute: bool = False,
):
    """
    单只股票分钟级 B 点确认。
    给线程池调用，子线程不打印进度，避免并发输出错乱。
    """

    code = str(row["代码"]).zfill(6)
    name = row.get("名称", "")

    try:
        # 加载 Tushare 5 / 30 分钟。
        # 如果 enable_1m_buy=True，才加载 1 分钟做最终精确买点确认；默认关闭。
        # 如果关闭 1 分钟，则只做到 30分钟趋势 + 5分钟结构/缠论B点确认，速度更快。
        df1 = pd.DataFrame()
        df5 = trim_minute_days(
            load_tushare_minute(code, "5", minute_days, force_update=force_update_minute),
            minute_days,
        )
        df30 = trim_minute_days(
            load_tushare_minute(code, "30", minute_days, force_update=force_update_minute),
            minute_days,
        )

        if enable_1m_buy:
            df1 = trim_minute_days(
                load_tushare_minute(code, "1", minute_days, force_update=force_update_minute),
                minute_days,
            )

        if df5.empty or df30.empty:
            return {
                "success": False,
                "hit": False,
                "code": code,
                "name": name,
                "result": None,
                "error": "5分钟或30分钟数据为空",
            }

        is_hit, buy_points, daily_group, structure_msg = evaluate_minute_buy_point(
            row,
            df5,
            df30,
            df1,
            enable_1m_buy=enable_1m_buy,
        )

        if not is_hit:
            return {
                "success": True,
                "hit": False,
                "code": code,
                "name": name,
                "result": None,
                "error": "",
            }

        df5_prepared = prepare_minute_data(df5)
        if df5_prepared.empty:
            return {
                "success": False,
                "hit": False,
                "code": code,
                "name": name,
                "result": None,
                "error": "5分钟指标数据为空",
            }

        latest5 = df5_prepared.iloc[-1]
        result = {
            "代码": code,
            "名称": name,
            "触发时间": latest5["datetime"].strftime("%Y-%m-%d %H:%M:%S"),
            "最新价": row.get("最新价", latest5["收盘"]),
            "涨跌幅": row.get("涨跌幅", pd.NA),
            "行业": row.get("行业", ""),
            "日线分组": daily_group,
            "日线策略": "、".join([
                str(row.get("突破反转策略", "")),
                str(row.get("主升策略", "")),
            ]).strip("、"),
            "30分钟结构": structure_msg,
            "分钟B点": "、".join(buy_points),
        }

        return {
            "success": True,
            "hit": True,
            "code": code,
            "name": name,
            "result": result,
            "error": "",
        }

    except Exception as e:
        return {
            "success": False,
            "hit": False,
            "code": code,
            "name": name,
            "result": None,
            "error": str(e),
        }


def scan_minute_buy_points(
    daily_signal_df: pd.DataFrame,
    max_stocks: int = 0,
    minute_days: int = DEFAULT_MINUTE_DAYS,
    max_workers: int = 4,
    enable_1m_buy: bool = False,
    force_update_minute: bool = False,
) -> pd.DataFrame:
    if daily_signal_df is None or daily_signal_df.empty:
        print("分钟级确认：没有日线候选股，跳过。")
        return pd.DataFrame()

    df = daily_signal_df.copy()
    df["代码"] = df["代码"].astype(str).str.zfill(6)
    if max_stocks > 0:
        df = df.head(max_stocks).copy()

    total = len(df)
    if total <= 0:
        print("分钟级确认：没有日线候选股，跳过。")
        return pd.DataFrame()

    max_workers = max(1, int(max_workers or 1))
    max_workers = min(max_workers, total)
    minute_days = int(minute_days or DEFAULT_MINUTE_DAYS)

    update_mode = "强制请求rt_min" if force_update_minute else "优先使用本地缓存，盘中用rt_min增量更新"
    print(
        f"\n开始分钟级B点确认：候选股票 {total} 只，"
        f"分钟数据范围：最近 {minute_days} 天，并发数：{max_workers}，"
        f"更新模式：{update_mode}"
    )

    result_list = []
    failed_count = 0
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}

        for _, row in df.iterrows():
            future = executor.submit(
                scan_one_minute_candidate,
                row.copy(),
                minute_days,
                enable_1m_buy,
                force_update_minute,
            )
            future_map[future] = str(row["代码"]).zfill(6)

        finished = 0

        for future in as_completed(future_map):
            finished += 1

            try:
                item = future.result()
            except Exception as e:
                failed_count += 1
                item = {
                    "success": False,
                    "hit": False,
                    "code": future_map.get(future, ""),
                    "name": "",
                    "result": None,
                    "error": str(e),
                }

            if item.get("success") is False:
                failed_count += 1

            if item.get("hit") and item.get("result"):
                result_list.append(item["result"])

            if finished % 5 == 0 or finished == total:
                elapsed = time.time() - start_time
                avg = elapsed / finished if finished else 0
                remain = avg * (total - finished)
                print_same_line(
                    f"分钟级确认进度：{finished}/{total} | "
                    f"B点数：{len(result_list)} | "
                    f"失败数：{failed_count} | "
                    f"预计剩余：{remain:.1f} 秒"
                )

    print()

    if not result_list:
        print("分钟级确认完成：本轮没有发现B点。")
        return pd.DataFrame()

    result_df = pd.DataFrame(result_list)
    output_file = save_minute_buy_points(result_df)
    print(f"分钟级B点结果已保存：{output_file}")

    print("\n分钟级B点预览：")
    print_minute_buy_point_table(result_df, max_rows=30)

    return result_df
