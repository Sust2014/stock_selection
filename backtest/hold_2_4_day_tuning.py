"""
聚焦持仓2-4天的高胜率策略参数调优回测。

设计原则:
- 单次遍历数据，同时计算2/3/4天三个持仓周期
- N22-V型反转细粒度参数网格（180组合）
- 新增4个策略（N36-N39）
- 保留原有TOP策略精选参数
- 综合评分 = 胜率70% + 信号量30%

所有单日策略统一签名: func(row, df, idx, prev)
简单策略用 *args 忽略不需要的参数
"""

import os, sys, time

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
sys.path.insert(0, PROJECT_ROOT)

import pandas as pd
import numpy as np
from strategy import HIST_CACHE_DIR, prepare_hist_data, check_secondary_filters

HOLD_DAYS = [2, 3, 4]
MIN_SIGNALS = 15

# ============================================================
# 数据加载与指标增强
# ============================================================

def load_stock(fp):
    df = pd.read_csv(fp, dtype={"代码": str})
    if df.empty or len(df) < 80:
        return None
    for c in ["日期", "开盘", "最高", "最低", "收盘", "成交量", "成交额", "涨跌幅"]:
        if c not in df.columns:
            return None
    df["日期"] = pd.to_datetime(df["日期"])
    for c in ["开盘", "最高", "最低", "收盘", "成交量", "成交额", "涨跌幅"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["开盘", "最高", "最低", "收盘"])
    return df.sort_values("日期").reset_index(drop=True)


def enrich(df):
    """One-shot compute all extra indicators."""
    df = df.copy()
    df["昨收"] = df["收盘"].shift(1)
    df["昨开"] = df["开盘"].shift(1)
    df["昨低"] = df["最低"].shift(1)
    df["昨高"] = df["最高"].shift(1)
    df["昨量"] = df["成交量"].shift(1)
    df["昨涨跌"] = df["涨跌幅"].shift(1)
    df["前涨跌"] = df["涨跌幅"].shift(2)
    df["大前涨跌"] = df["涨跌幅"].shift(3)
    df["SMA10"] = df["收盘"].rolling(10).mean()
    df["SMA5昨"] = df["SMA5"].shift(1)
    df["SMA10昨"] = df["SMA10"].shift(1)
    df["SMA20昨"] = df["SMA20"].shift(1)
    df["10日高"] = df["最高"].shift(1).rolling(10).max()
    df["10日低"] = df["最低"].rolling(10).min()
    df["10日最高收"] = df["收盘"].shift(1).rolling(10).max()
    df["20日高"] = df["最高"].shift(1).rolling(20).max()
    df["20日低"] = df["最低"].rolling(20).min()
    df["5日高"] = df["最高"].shift(1).rolling(5).max()
    df["5日低"] = df["最低"].rolling(5).min()
    df["实体上沿"] = df[["开盘", "收盘"]].max(axis=1)
    df["实体下沿"] = df[["开盘", "收盘"]].min(axis=1)
    df["昨实体上沿"] = df["实体上沿"].shift(1)
    df["昨实体下沿"] = df["实体下沿"].shift(1)
    df["量比昨"] = df["成交量"] / df["昨量"].replace(0, np.nan)
    df["均量"] = df["过去20日平均成交量"]
    df["昨均量比"] = df["昨量"] / df["均量"]
    df["收阳"] = (df["收盘"] > df["开盘"]).fillna(0).astype(int)
    df["昨收阳"] = df["收阳"].shift(1).fillna(0).astype(int)
    df["前收阳"] = df["收阳"].shift(2).fillna(0).astype(int)
    df["收阴"] = (df["收盘"] < df["开盘"]).fillna(0).astype(int)
    df["昨收阴"] = df["收阴"].shift(1).fillna(0).astype(int)
    df["SMA5上穿SMA20"] = ((df["SMA5"] > df["SMA20"]) & (df["SMA5昨"] <= df["SMA20昨"])).fillna(0).astype(int)
    df["昨开缺口"] = ((df["昨开"] - df["收盘"].shift(2)) / df["收盘"].shift(2).replace(0, np.nan) * 100)
    # 连续下跌天数
    df["连续下跌"] = 0
    for j in range(1, 6):
        cond = df["涨跌幅"].shift(j).fillna(0) < 0
        df.loc[cond, "连续下跌"] = df.loc[cond, "连续下跌"] + 1
    return df


# ============================================================
# 策略注册表
# ============================================================

S = []  # (name, category, func)


def reg(name, cat, func):
    S.append((name, cat, func))


# ============================================================
# N22-V型反转 细粒度参数网格 (6×5×6 = 180)
# 签名约定: f(row, *args, dist, pct, vol) → *args 吸收 df,idx,prev
# ============================================================
for dist in [0.08, 0.10, 0.12, 0.15, 0.18, 0.20]:
    for pct in [2, 3, 4, 5, 6]:
        for vol in [1.2, 1.3, 1.5, 1.8, 2.0, 2.5]:
            reg(
                f"N22_d{int(dist*100):02d}_p{pct}_v{vol}",
                "N22-V型反转",
                lambda r, *_, _d=dist, _p=pct, _v=vol: (
                    r["收盘"] / r["过去40日最低价"] - 1 < _d
                    and r["涨跌幅"] > _p
                    and r["昨涨跌"] < -1
                    and r["成交量"] > r["均量"] * _v
                    and r["收阳"] == 1
                ),
            )

# ============================================================
# N1-双底放量反转 (精选参数)
# ============================================================
for d40, d60, pct, vol in [
    (0.15, 0.20, 3.0, 1.8),
    (0.12, 0.18, 2.0, 2.0),
    (0.15, 0.25, 3.0, 1.5),
    (0.10, 0.15, 2.0, 1.5),
    (0.12, 0.20, 2.5, 1.8),
]:
    reg(
        f"N1_d40_{int(d40*100)}_d60_{int(d60*100)}_p{int(pct)}_v{vol}",
        "N1-双底放量反转",
        lambda r, *_, _a=d40, _b=d60, _p=pct, _v=vol: (
            r["收盘"] / r["过去40日最低价"] - 1 < _a
            and r["收盘"] / r["过去60日最低收盘"] - 1 < _b
            and r["涨跌幅"] > _p
            and r["成交量"] > r["均量"] * _v
            and r["收阳"] == 1
        ),
    )

# ============================================================
# N2-缩量回踩反击 (精选参数)
# ============================================================
for yv, tv, pct in [(0.5, 1.8, 1), (0.6, 1.5, 2), (0.4, 2.0, 1)]:
    reg(
        f"N2_y{yv}_t{tv}_p{int(pct)}",
        "N2-缩量回踩反击",
        lambda r, *_, _yv=yv, _tv=tv, _p=pct: (
            r["昨量"] < r["均量"] * _yv
            and r["成交量"] > r["均量"] * _tv
            and r["量比昨"] > 1.3
            and r["涨跌幅"] > _p
            and r["收盘"] > r["SMA5"]
            and r["SMA20"] > r["SMA60"]
        ),
    )

# ============================================================
# M2-主升底部反转
# ============================================================
for dist, pct, vol in [(0.15, 5, 1.8), (0.20, 4, 1.8), (0.18, 4, 1.5)]:
    reg(
        f"M2_d{int(dist*100)}_p{int(pct)}_v{vol}",
        "M2-主升底部反转",
        lambda r, *_, _d=dist, _p=pct, _v=vol: (
            r["收盘"] / r["过去60日最低收盘"] - 1 < _d
            and r["涨跌幅"] > _p
            and r["成交量"] > r["均量"] * _v
        ),
    )

# ============================================================
# N5-均线粘合突破 (高信号量)
# ============================================================
for vol, pct in [(1.5, 3), (1.8, 2), (1.3, 2)]:
    reg(
        f"N5_v{vol}_p{int(pct)}",
        "N5-均线粘合突破",
        lambda r, *_, _v=vol, _p=pct: (
            abs(r["SMA5"] / r["SMA10"] - 1) < 0.03
            and abs(r["SMA10"] / r["SMA20"] - 1) < 0.05
            and r["收盘"] > max(r["SMA5"], r["SMA10"], r["SMA20"])
            and r["成交量"] > r["均量"] * _v
            and r["涨跌幅"] > _p
        ),
    )

# ============================================================
# N24-涨停缩量再放
# ============================================================
for yv, vb, pct in [(0.7, 1.5, 2), (0.6, 1.8, 2), (0.8, 1.3, 1)]:
    reg(
        f"N24_y{yv}_vb{vb}_p{int(pct)}",
        "N24-涨停缩量再放",
        lambda r, *_, _yv=yv, _vb=vb, _p=pct: (
            r["近15日涨停次数"] >= 1
            and r["昨量"] < r["均量"] * _yv
            and r["量比昨"] > _vb
            and r["涨跌幅"] > _p
            and r["收盘"] > r["SMA10"]
            and r["SMA20"] > r["SMA60"]
        ),
    )

# ============================================================
# N11-地量倍量
# ============================================================
for dry, boom, pct in [(0.5, 2.0, 2), (0.4, 2.5, 1), (0.6, 1.8, 3)]:
    reg(
        f"N11_d{int(dry*10)}_b{int(boom*10)}_p{int(pct)}",
        "N11-地量倍量",
        lambda r, *_, _d=dry, _b=boom, _p=pct: (
            r["昨量"] < r["均量"] * _d
            and r["量比昨"] > _b
            and r["涨跌幅"] > _p
            and r["收盘"] > r["SMA5"]
        ),
    )

# ============================================================
# N9-跳空不补
# ============================================================
for gap_pct, vol in [(2.0, 1.3), (3.0, 1.5)]:
    reg(
        f"N9_g{int(gap_pct*10)}_v{vol}",
        "N9-跳空不补",
        lambda r, *_, _g=gap_pct, _v=vol: (
            r["昨开缺口"] > _g
            and r["昨收阳"] == 1
            and r["最低"] > r["昨开"]
            and r["成交量"] > r["均量"] * _v
            and r["收阳"] == 1
        ),
    )

# ============================================================
# N15-均线金叉
# ============================================================
for vol, pct in [(1.8, 1), (1.5, 2)]:
    reg(
        f"N15_v{vol}_p{int(pct)}",
        "N15-均线金叉",
        lambda r, *_, _v=vol, _p=pct: (
            r["SMA5上穿SMA20"] == 1
            and r["成交量"] > r["均量"] * _v
            and r["涨跌幅"] > _p
        ),
    )

# ============================================================
# N23-倍量突破前高
# ============================================================
for vm, lb in [(2.0, 20), (1.8, 10)]:
    reg(
        f"N23_vm{vm}_lb{lb}",
        "N23-倍量突破前高",
        lambda r, *_, _vm=vm, _lb=lb: (
            r["量比昨"] > _vm
            and r["成交量"] > r["均量"] * 1.5
            and r["收盘"] > (r["10日最高收"] if _lb <= 10 else r["过去60日最高收盘"])
            and r["涨跌幅"] > 1.5
            and r["收阳"] == 1
        ),
    )

# ============================================================
# N26-强势突破连阳
# ============================================================
for vol, pct in [(1.5, 2), (1.8, 2)]:
    reg(
        f"N26_v{vol}_p{int(pct)}",
        "N26-强势突破连阳",
        lambda r, *_, _v=vol, _p=pct: (
            r["收盘"] > r["5日高"]
            and r["昨收阳"] == 1
            and r["成交量"] > r["均量"] * _v
            and r["涨跌幅"] > _p
            and r["收阳"] == 1
        ),
    )

# ============================================================
# N27-涨停接力 (需要df/idx/prev上下文，定制签名)
# ============================================================
for gmin, gmax, vol in [(1, 3, 2.0), (1, 4, 1.8)]:
    def _make_n27(gmin, gmax, vol):
        def f(row, df, idx, prev):
            if prev is None:
                return False
            if pd.isna(prev["涨跌幅"]) or prev["涨跌幅"] < 9.9:
                return False
            yc, to = prev["收盘"], row["开盘"]
            if pd.isna(yc) or pd.isna(to) or yc <= 0:
                return False
            gap = (to / yc - 1) * 100
            if gap < gmin or gap > gmax:
                return False
            if pd.isna(row["涨跌幅"]) or row["涨跌幅"] < 2:
                return False
            av = row["均量"]
            return not (pd.isna(av) or av <= 0) and row["成交量"] >= av * vol and row["收阳"] == 1
        return f
    reg(f"N27_g{gmin}-{gmax}_v{vol}", "N27-涨停接力", _make_n27(gmin, gmax, vol))

# ============================================================
# N35-急跌反弹
# ============================================================
for drop, rise, vol in [(-5, 3, 1.8), (-4, 3, 1.5)]:
    reg(
        f"N35_d{abs(drop)}_r{int(rise)}_v{vol}",
        "N35-急跌反弹",
        lambda r, *_, _d=drop, _ri=rise, _v=vol: (
            r["昨涨跌"] < _d
            and r["涨跌幅"] > _ri
            and r["成交量"] > r["均量"] * _v
            and r["收阳"] == 1
            and r["收盘"] > r["昨实体上沿"]
        ),
    )

# ============================================================
# * NEW N36——连续下跌反弹
# 急跌后放量反弹，比N22更宽（不限制昨跌幅度）
# ============================================================
for con_days in [1, 2, 3]:
    for pct in [2, 3, 4, 5]:
        for vol in [1.3, 1.5, 1.8]:
            reg(
                f"N36_cd{con_days}_p{pct}_v{vol}",
                "N36-连续下跌反弹",
                lambda r, *_, _cd=con_days, _p=pct, _v=vol: (
                    r["连续下跌"] >= _cd
                    and r["涨跌幅"] > _p
                    and r["成交量"] > r["均量"] * _v
                    and r["收阳"] == 1
                    and r["收盘"] / r["过去40日最低价"] - 1 < 0.25
                ),
            )

# ============================================================
# * NEW N37——缩量末阳突破
# 昨缩量洗盘 + 今放量突破昨日最高（结合N2与N23的优点）
# ============================================================
for yv in [0.5, 0.6, 0.7]:
    for tv in [1.3, 1.5, 1.8, 2.0]:
        for pct in [1, 2, 3]:
            reg(
                f"N37_y{yv}_t{tv}_p{pct}",
                "N37-缩量末阳突破",
                lambda r, *_, _yv=yv, _tv=tv, _p=pct: (
                    r["昨量"] < r["均量"] * _yv
                    and r["成交量"] > r["均量"] * _tv
                    and r["收盘"] > r["昨高"]
                    and r["涨跌幅"] > _p
                    and r["收阳"] == 1
                ),
            )

# ============================================================
# * NEW N38——强势连板回调
# 多次涨停基因 + 回调缩量 + 再放量（N24增强版）
# ============================================================
for yv in [0.6, 0.7, 0.8]:
    for tv in [1.3, 1.5]:
        for pct in [2, 3]:
            reg(
                f"N38_y{yv}_t{tv}_p{pct}",
                "N38-强势连板回调",
                lambda r, *_, _ml=2, _yv=yv, _tv=tv, _p=pct: (
                    r["近15日涨停次数"] >= _ml
                    and r["昨量"] < r["均量"] * _yv
                    and r["成交量"] > r["均量"] * _tv
                    and r["涨跌幅"] > _p
                    and r["收盘"] > r["SMA10"]
                    and r["收阳"] == 1
                ),
            )

# ============================================================
# * NEW N39——均线支撑反弹
# 价格回落到SMA20附近获得支撑 + 放量反弹（改良N5）
# ============================================================
for dist_ma in [0.01, 0.02, 0.03]:
    for pct in [1, 2, 3]:
        for vol in [1.2, 1.5, 1.8]:
            reg(
                f"N39_dm{int(dist_ma*100)}_p{pct}_v{vol}",
                "N39-均线支撑反弹",
                lambda r, *_, _dm=dist_ma, _p=pct, _v=vol: (
                    abs(r["收盘"] / r["SMA20"] - 1) < _dm
                    and r["收盘"] > r["SMA20"]
                    and r["涨跌幅"] > _p
                    and r["成交量"] > r["均量"] * _v
                    and r["收阳"] == 1
                    and r["SMA20"] > r["SMA60"]
                ),
            )

# ============================================================
# 主回测逻辑
# ============================================================

def run():
    n = len(S)
    max_hd = max(HOLD_DAYS)
    print("=" * 65)
    print(f"  持仓2-4天策略参数调优回测")
    print(f"  策略变体: {n}个 | 持仓: {HOLD_DAYS}天 | 1136只股票")
    print("=" * 65)

    files = sorted([f for f in os.listdir(HIST_CACHE_DIR) if f.endswith("_bs.csv")])
    total = len(files)

    acc = {}
    for hd in HOLD_DAYS:
        acc[hd] = {}
        for name, cat, _ in S:
            acc[hd][name] = {"signals": 0, "wins": 0, "returns": [], "category": cat}

    need_cols = [
        "SMA5", "SMA10", "SMA20", "SMA60",
        "过去60日最高价", "过去60日最高收盘", "过去60日最低收盘",
        "过去40日最低价", "过去20日实体振幅", "过去20日平均成交量",
        "过去20日日均成交额", "近15日涨停次数", "SMA60_5日前",
    ]

    t0 = time.time()
    total_signals = 0

    for fi, fname in enumerate(files, 1):
        raw = load_stock(os.path.join(HIST_CACHE_DIR, fname))
        if raw is None:
            continue

        df = prepare_hist_data(raw.copy())
        df = enrich(df)
        df = df.sort_values("日期").reset_index(drop=True)

        max_i = len(df) - max_hd - 1
        for i in range(65, max_i):
            row = df.iloc[i]
            if row[need_cols].isna().any():
                continue
            if not check_secondary_filters(row):
                continue

            prev = df.iloc[i - 1] if i >= 1 else None
            bp = df.iloc[i + 1]["开盘"]
            if pd.isna(bp) or bp <= 0:
                continue

            # Compute sell prices for all needed hold periods
            sell_prices = []
            valid = True
            for hd in range(1, max_hd + 1):
                sp = df.iloc[i + hd]["收盘"]
                if pd.isna(sp):
                    valid = False
                    break
                sell_prices.append(sp)
            if not valid:
                continue

            returns = [(sp / bp - 1) * 100 for sp in sell_prices]
            wins = [r > 0 for r in returns]

            # Evaluate all strategies with unified signature: func(row, df, idx, prev)
            for name, cat, func in S:
                try:
                    hit = func(row, df, i, prev)
                    if hit:
                        for hd_val in HOLD_DAYS:
                            d = acc[hd_val][name]
                            d["signals"] += 1
                            ret_idx = hd_val - 1
                            d["returns"].append(returns[ret_idx])
                            if wins[ret_idx]:
                                d["wins"] += 1
                        total_signals += 1
                except Exception:
                    pass

        if fi % 200 == 0:
            e = time.time() - t0
            print(f"  进度: {fi}/{total} | {e:.0f}s | 剩余{e/fi*(total-fi):.0f}s | 信号{total_signals}")

    elapsed = time.time() - t0
    print(f"\n[OK] 回测完成: {elapsed:.0f}s | 总信号触发: {total_signals}")

    # ============================================================
    # 结果汇总
    # ============================================================
    os.makedirs("output/backtest", exist_ok=True)

    all_results = []

    for hd in HOLD_DAYS:
        results = []
        for name, d in acc[hd].items():
            cnt = d["signals"]
            if cnt < MIN_SIGNALS:
                continue
            rets = d["returns"]
            w = d["wins"]
            wr = w / cnt * 100
            avg_r = sum(rets) / len(rets)
            med_r = sorted(rets)[len(rets) // 2]
            avg_w = sum(r for r in rets if r > 0) / max(1, sum(1 for r in rets if r > 0))
            avg_l = sum(r for r in rets if r <= 0) / max(1, sum(1 for r in rets if r <= 0))
            pl = abs(avg_w / avg_l) if avg_l != 0 else 99
            row_data = {
                "name": name, "cat": d["category"], "hold_days": hd,
                "signals": cnt, "wins": w, "losses": cnt - w,
                "wr": wr, "avg": avg_r, "med": med_r,
                "max_g": max(rets), "max_l": min(rets),
                "avg_w": avg_w, "avg_l": avg_l, "pl": pl,
            }
            results.append(row_data)
            all_results.append(row_data)

        results.sort(key=lambda r: r["wr"], reverse=True)
        df_r = pd.DataFrame(results)
        df_r.to_csv(f"output/backtest/tuning_hold{hd}d.csv", index=False, encoding="utf-8-sig")

    df_all = pd.DataFrame(all_results)

    # Wilson confidence lower bound
    def wilson_lower(wr_pct, n, z=1.96):
        if n <= 0:
            return 0
        p = wr_pct / 100.0
        denom = 1 + z**2 / n
        center = (p + z**2 / (2 * n)) / denom
        margin = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n * n)) / denom
        return (center - margin) * 100

    # Composite score: 70% win rate + 30% normalized signal count
    log_sig_all = np.log1p(df_all["signals"].values)
    log_ref = np.log1p(150)
    sig_norm = np.clip(log_sig_all / (log_ref * 2.5), 0, 1)
    wr_norm = np.clip(df_all["wr"].values / 100.0, 0, 1)
    df_all["composite"] = (wr_norm * 0.7 + sig_norm * 0.3) * 100
    df_all["wilson_lower"] = df_all.apply(lambda r: wilson_lower(r["wr"], r["signals"]), axis=1)

    df_all.to_csv("output/backtest/tuning_all_results.csv", index=False, encoding="utf-8-sig")

    # ============================================================
    # 报告输出
    # ============================================================

    # 视图1: 按胜率排序 TOP50
    print("\n" + "=" * 120)
    print("  持股2-4天 按胜率排序 TOP 50")
    print("=" * 120)
    top_by_wr = df_all.sort_values("wr", ascending=False).head(50).reset_index(drop=True)
    for rank, (_, r) in enumerate(top_by_wr.iterrows(), 1):
        print(f"  {rank:>3}. [{int(r['hold_days'])}天] {r['name']:<42} {r['cat']:<22} "
              f"胜率={r['wr']:.2f}%  信号={int(r['signals'])}  平均={r['avg']:.2f}%  "
              f"Wilson={r['wilson_lower']:.2f}%  盈亏比={r['pl']:.2f}")

    # 视图2: 按综合评分排序 TOP50
    print("\n" + "=" * 120)
    print("  按综合评分排序 TOP 50")
    print("=" * 120)
    top_by_comp = df_all.sort_values("composite", ascending=False).head(50).reset_index(drop=True)
    for rank, (_, r) in enumerate(top_by_comp.iterrows(), 1):
        print(f"  {rank:>3}. [{int(r['hold_days'])}天] {r['name']:<42} {r['cat']:<22} "
              f"胜率={r['wr']:.2f}%  信号={int(r['signals'])}  综合={r['composite']:.1f}  盈亏比={r['pl']:.2f}")

    # 视图3: 每个持仓天数 TOP15
    for hd in HOLD_DAYS:
        print(f"\n{'='*100}")
        print(f"  持仓{hd}天 — TOP 15 (按胜率)")
        print(f"{'='*100}")
        hd_df = df_all[df_all["hold_days"] == hd].sort_values("wr", ascending=False).head(15)
        for rank, (_, r) in enumerate(hd_df.iterrows(), 1):
            print(f"  {rank:>3}. {r['name']:<42} {r['cat']:<22} "
                  f"胜率={r['wr']:.2f}%  信号={int(r['signals'])}  平均={r['avg']:.2f}%  盈亏比={r['pl']:.2f}")

    # 视图4: 各类别最佳（跨持仓）
    print("\n" + "=" * 100)
    print("  各类别最佳策略（按综合评分）")
    print("=" * 100)
    best_cat = {}
    for cat in df_all["cat"].unique():
        cat_df = df_all[df_all["cat"] == cat]
        best_cat[cat] = cat_df.loc[cat_df["composite"].idxmax()]
    for cat, r in sorted(best_cat.items(), key=lambda x: x[1]["composite"], reverse=True):
        hd = int(r["hold_days"])
        print(f"  {cat:<24} 持仓{hd}天  {r['name']:<42} 胜率={r['wr']:.2f}%  信号={int(r['signals'])}  综合={r['composite']:.1f}")

    # 视图5: 新增策略表现
    print("\n" + "=" * 100)
    print("  新增策略 N36-N39 表现（TOP 5 per category）")
    print("=" * 100)
    for new_cat in ["N36-连续下跌反弹", "N37-缩量末阳突破", "N38-强势连板回调", "N39-均线支撑反弹"]:
        cat_df = df_all[df_all["cat"] == new_cat].sort_values("composite", ascending=False).head(5)
        if cat_df.empty:
            print(f"\n  {new_cat}: 无满足条件的策略（信号<{MIN_SIGNALS}）")
            continue
        best = cat_df.iloc[0]
        print(f"\n  {new_cat} 最佳: [{int(best['hold_days'])}天] {best['name']}")
        print(f"    胜率={best['wr']:.2f}%  信号={int(best['signals'])}  平均收益={best['avg']:.2f}%  盈亏比={best['pl']:.2f}  综合={best['composite']:.1f}")
        for rank, (_, r) in enumerate(cat_df.iterrows(), 1):
            print(f"    {rank}. [{int(r['hold_days'])}天] {r['name']:<42} 胜率={r['wr']:.2f}%  信号={int(r['signals'])}")

    # 全局冠军
    best_wr = df_all.loc[df_all["wr"].idxmax()]
    best_comp = df_all.loc[df_all["composite"].idxmax()]
    print("\n")
    print("=" * 80)
    print(f"  * 胜率冠军: [{int(best_wr['hold_days'])}天] {best_wr['name']} ({best_wr['cat']})")
    print(f"  * 胜率={best_wr['wr']:.2f}%  信号={int(best_wr['signals'])}  平均收益={best_wr['avg']:.2f}%  盈亏比={best_wr['pl']:.2f}")
    print(f"  * 综合冠军: [{int(best_comp['hold_days'])}天] {best_comp['name']} ({best_comp['cat']})")
    print(f"  * 胜率={best_comp['wr']:.2f}%  信号={int(best_comp['signals'])}  综合={best_comp['composite']:.1f}  盈亏比={best_comp['pl']:.2f}")
    print("=" * 80)

    # 保存MD报告
    report_path = "output/backtest/hold_2_4_day_tuning_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(f"# 持仓2-4天策略参数调优回测报告\n\n")
        f.write(f"回测时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"策略变体: {n}个 | 持仓: {HOLD_DAYS}天 | 股票数: {total}\n")
        f.write(f"最低信号要求: {MIN_SIGNALS}\n\n")

        f.write("## 胜率冠军\n\n")
        f.write(f"- **[{int(best_wr['hold_days'])}天] {best_wr['name']}** ({best_wr['cat']})\n")
        f.write(f"- 胜率: **{best_wr['wr']:.2f}%** | 信号: {int(best_wr['signals'])} | 平均收益: {best_wr['avg']:.2f}% | 盈亏比: {best_wr['pl']:.2f}\n\n")

        f.write("## 综合冠军\n\n")
        f.write(f"- **[{int(best_comp['hold_days'])}天] {best_comp['name']}** ({best_comp['cat']})\n")
        f.write(f"- 胜率: {best_comp['wr']:.2f}% | 信号: {int(best_comp['signals'])} | 综合评分: {best_comp['composite']:.1f}\n\n")

        f.write("## 新增策略表现\n\n")
        for new_cat in ["N36-连续下跌反弹", "N37-缩量末阳突破", "N38-强势连板回调", "N39-均线支撑反弹"]:
            cat_df = df_all[df_all["cat"] == new_cat].sort_values("composite", ascending=False).head(3)
            if not cat_df.empty:
                best = cat_df.iloc[0]
                f.write(f"- **{new_cat}**: [{int(best['hold_days'])}天] {best['name']} — 胜率{best['wr']:.2f}%, 信号{int(best['signals'])}\n")

        f.write(f"\n## 各持仓天数TOP10\n\n")
        for hd in HOLD_DAYS:
            f.write(f"### 持仓{hd}天\n\n")
            hd_df = df_all[df_all["hold_days"] == hd].sort_values("wr", ascending=False).head(10)
            f.write("| 排名 | 策略 | 分类 | 胜率% | 信号 | 平均收益% | 盈亏比 |\n")
            f.write("|:--:|------|------|:---:|:---:|:---:|:---:|\n")
            for rank, (_, r) in enumerate(hd_df.iterrows(), 1):
                f.write(f"| {rank} | {r['name']} | {r['cat']} | {r['wr']:.2f} | {int(r['signals'])} | {r['avg']:.2f} | {r['pl']:.2f} |\n")
            f.write("\n")

    print(f"\n[OK] 报告已保存: {report_path}")
    print(f"[OK] 详细数据: output/backtest/tuning_hold*d.csv")
    print(f"[OK] 全部汇总: output/backtest/tuning_all_results.csv")


if __name__ == "__main__":
    run()
