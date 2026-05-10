"""
按回测要求.txt 实现的8策略回测
- 6个需求策略 (S1/S2/A/B/C/D) + N22-V型反转 + N36-连续下跌反弹
- 动态卖出规则: MA5止损 / 5MA上方放量阴线止盈 / 到期
- 持有2-4天，参数可调
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
# 数据加载
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
    df = df.copy()
    df["昨收"] = df["收盘"].shift(1)
    df["昨开"] = df["开盘"].shift(1)
    df["昨低"] = df["最低"].shift(1)
    df["昨高"] = df["最高"].shift(1)
    df["昨量"] = df["成交量"].shift(1)
    df["昨涨跌"] = df["涨跌幅"].shift(1)
    df["前涨跌"] = df["涨跌幅"].shift(2)
    df["SMA5"] = df["收盘"].rolling(5).mean()
    df["SMA10"] = df["收盘"].rolling(10).mean()
    df["SMA20"] = df["收盘"].rolling(20).mean()
    df["SMA10昨"] = df["SMA10"].shift(1)
    df["20日高"] = df["最高"].shift(1).rolling(20).max()
    df["5日高"] = df["最高"].shift(1).rolling(5).max()
    df["实体上沿"] = df[["开盘", "收盘"]].max(axis=1)
    df["实体下沿"] = df[["开盘", "收盘"]].min(axis=1)
    df["昨实体上沿"] = df["实体上沿"].shift(1)
    df["量比昨"] = df["成交量"] / df["昨量"].replace(0, np.nan)
    df["均量"] = df["过去20日平均成交量"]
    df["收阳"] = (df["收盘"] > df["开盘"]).fillna(0).astype(int)
    df["昨收阳"] = df["收阳"].shift(1).fillna(0).astype(int)
    df["收阴"] = (df["收盘"] < df["开盘"]).fillna(0).astype(int)
    # 连续下跌天数
    df["连续下跌"] = 0
    for j in range(1, 5):
        cond = df["涨跌幅"].shift(j).fillna(0) < 0
        df.loc[cond, "连续下跌"] = df.loc[cond, "连续下跌"] + 1
    return df


# ============================================================
# 动态卖出逻辑（回测要求 3.1-3.3）
# ============================================================

def find_exit(df, entry_idx, max_hold):
    """
    逐日检查卖出条件.
    max_hold: 允许检查的天数（不含买入日）
    返回 (exit_idx, exit_reason)
    """
    for hd in range(1, max_hold + 1):
        exit_idx = entry_idx + hd
        if exit_idx >= len(df):
            return exit_idx - 1, "数据不足"

        r = df.iloc[exit_idx]
        close = r["收盘"]
        if pd.isna(close) or close <= 0:
            return exit_idx - 1, "数据异常"

        sma5 = r.get("SMA5")
        avg_vol = r.get("均量")
        vol = r["成交量"]
        open_p = r["开盘"]

        # 3.1 跌破5日线止损（先检查）
        if sma5 is not None and not pd.isna(sma5) and close < sma5:
            return exit_idx, f"止损(破5MA)第{hd+1}日"

        # 3.2 5MA上方+放量+收阴线 止盈
        if sma5 is not None and avg_vol is not None:
            if (not pd.isna(sma5) and not pd.isna(avg_vol)
                    and close > sma5
                    and vol >= avg_vol
                    and close < open_p):
                return exit_idx, f"止盈(5MA上+放量阴)第{hd+1}日"

        # 3.3 到期必须卖出（最后一天，条件已检查）
        if hd == max_hold:
            return exit_idx, f"到期(持{max_hold+1}日)"

    return exit_idx, f"到期(持{max_hold+1}日)"


# ============================================================
# 策略定义 (max 8 strategies)
# ============================================================

class StrategyDef:
    def __init__(self, name, cat, func, need_prev=False, need_df=False):
        self.name = name
        self.cat = cat
        self.func = func
        self.need_prev = need_prev
        self.need_df = need_df


def make_strategies():
    """返回策略列表，每个策略可能有多个参数变体"""
    strategies = []

    # --- 1.1 箱体突破 ---
    for amp, vol in [(0.20, 1.3), (0.20, 1.5), (0.15, 1.5)]:
        strategies.append(StrategyDef(
            f"S1_箱体_a{int(amp*100)}_v{vol}",
            "S1-箱体突破",
            lambda r, *_, _a=amp, _v=vol: (
                r["收盘"] > r["过去60日最高价"]
                and r["成交量"] > r["均量"] * _v
                and r["过去20日实体振幅"] <= _a
            )
        ))

    # --- 1.2 底部放量反转 ---
    for dist, pct, vol in [(0.20, 5, 2.0), (0.15, 5, 2.0), (0.20, 4, 1.8)]:
        strategies.append(StrategyDef(
            f"S2_底部_d{int(dist*100)}_p{int(pct)}_v{vol}",
            "S2-底部放量反转",
            lambda r, *_, _d=dist, _p=pct, _v=vol: (
                r["收盘"] / r["过去40日最低价"] - 1 < _d
                and r["涨跌幅"] > _p
                and r["成交量"] > r["均量"] * _v
            )
        ))

    # --- 1.3 竞价追涨 ---
    for (gmin, gmax, pct, vol) in [(3, 6, 7, 1.5), (3, 7, 5, 1.5)]:
        def _make_a(gmin, gmax, pct, vol):
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
                if pd.isna(row["涨跌幅"]) or row["涨跌幅"] < pct:
                    return False
                av = row["均量"]
                return not (pd.isna(av) or av <= 0) and row["成交量"] >= av * vol
            return f
        strategies.append(StrategyDef(
            f"A_竞价_g{gmin}-{gmax}_p{pct}_v{vol}",
            "A-竞价追涨",
            _make_a(gmin, gmax, pct, vol),
            need_prev=True, need_df=True
        ))

    # --- 1.4 龙头回调 ---
    for rise, days, pb in [(20, 8, 50), (15, 5, 30), (20, 5, 40)]:
        def _make_b(rise, days, pb):
            def f(row, df, idx, prev):
                if idx < 20:
                    return False
                tc = row["收盘"]
                if pd.isna(tc) or tc <= 0:
                    return False
                seg = df.iloc[max(0, idx - 13):idx + 1]
                closes = seg["收盘"].values
                if len(closes) < 5:
                    return False
                lo_i, hi_i = int(np.argmin(closes)), int(np.argmax(closes))
                lp, hp = closes[lo_i], closes[hi_i]
                if lp <= 0 or hp <= 0:
                    return False
                if (hp / lp - 1) * 100 < rise:
                    return False
                if hi_i <= lo_i:
                    return False
                if tc >= hp * 0.99:
                    return False
                pd_ = len(seg) - 1 - hi_i
                if pd_ < 2 or pd_ > days:
                    return False
                if (hp - tc) / (hp - lp) * 100 > pb:
                    return False
                return True
            return f
        strategies.append(StrategyDef(
            f"B_龙头_r{rise}_d{days}_pb{pb}",
            "B-龙头回调",
            _make_b(rise, days, pb),
            need_df=True
        ))

    # --- 1.5 追涨突破 ---
    for vy, va, pct in [(1.5, 3, 5), (2, 3, 5), (1.5, 2, 5)]:
        def _make_c(vy, va, pct):
            def f(row, df, idx, prev):
                if prev is None:
                    return False
                yv, tv_ = prev["成交量"], row["成交量"]
                if pd.isna(yv) or pd.isna(tv_) or yv <= 0:
                    return False
                if tv_ < yv * vy:
                    return False
                av = row["均量"]
                if pd.isna(av) or av <= 0 or tv_ < av * va:
                    return False
                if pd.isna(row.get("过去20日日均成交额")) or row["过去20日日均成交额"] < 50_000_000:
                    return False
                if pd.isna(row["涨跌幅"]) or row["涨跌幅"] < pct:
                    return False
                high_13d = df.iloc[max(0, idx - 13):idx]["最高"].max()
                return row["收盘"] > high_13d
            return f
        strategies.append(StrategyDef(
            f"C_追涨_vy{vy}_va{va}_p{pct}",
            "C-追涨突破",
            _make_c(vy, va, pct),
            need_prev=True, need_df=True
        ))

    # --- 1.6 断板反包 ---
    for lim, rev, brk in [(2, 2, -8), (3, 2, -5)]:
        def _make_d(lim, rev, brk):
            def f(row, df, idx, prev):
                if idx < 10:
                    return False
                t = df.iloc[idx]
                if pd.isna(t["收盘"]) or t["收盘"] <= 0:
                    return False
                b = df.iloc[idx - 1]
                if pd.isna(b["涨跌幅"]) or b["涨跌幅"] >= 9.95:
                    return False
                clim = 0
                for j in range(2, 10):
                    ci = idx - j
                    if ci < 0:
                        break
                    if df.iloc[ci]["涨跌幅"] >= 9.95:
                        clim += 1
                    else:
                        break
                if clim < lim:
                    return False
                bh = max(b["开盘"], b["收盘"])
                if t["收盘"] <= bh:
                    return False
                if pd.isna(t["涨跌幅"]) or t["涨跌幅"] < rev:
                    return False
                if b["涨跌幅"] < brk:
                    return False
                av = t.get("均量")
                return not (av is None or pd.isna(av) or av <= 0) and t["成交量"] >= av * 0.8
            return f
        strategies.append(StrategyDef(
            f"D_断板_l{lim}_r{rev}_b{brk}",
            "D-断板反包",
            _make_d(lim, rev, brk),
            need_df=True
        ))

    # --- N22-V型反转 (已验证最佳) ---
    for dist, pct, vol in [
        (0.15, 5, 1.5),   # 原冠军
        (0.15, 4, 1.2),   # 高信号版 68.42%/95信号
        (0.15, 4, 1.3),   # 均衡版 68.24%/85信号
        (0.12, 4, 1.2),   # 短距离版
        (0.15, 6, 1.5),   # 紧版
    ]:
        strategies.append(StrategyDef(
            f"N22_d{int(dist*100)}_p{pct}_v{vol}",
            "N22-V型反转",
            lambda r, *_, _d=dist, _p=pct, _v=vol: (
                r["收盘"] / r["过去40日最低价"] - 1 < _d
                and r["涨跌幅"] > _p
                and r["昨涨跌"] < -1
                and r["成交量"] > r["均量"] * _v
                and r["收阳"] == 1
            )
        ))

    # --- N36-连续下跌反弹 (新增最佳) ---
    for cd, pct, vol in [
        (2, 4, 1.3),   # 最佳
        (1, 4, 1.3),   # 更多信号
        (2, 5, 1.5),   # 更紧
        (2, 3, 1.3),   # 更宽
    ]:
        strategies.append(StrategyDef(
            f"N36_cd{cd}_p{pct}_v{vol}",
            "N36-连续下跌反弹",
            lambda r, *_, _cd=cd, _p=pct, _v=vol: (
                r["连续下跌"] >= _cd
                and r["涨跌幅"] > _p
                and r["成交量"] > r["均量"] * _v
                and r["收阳"] == 1
                and r["收盘"] / r["过去40日最低价"] - 1 < 0.25
            )
        ))

    return strategies


# ============================================================
# 主回测
# ============================================================

def run():
    strategies = make_strategies()
    n = len(strategies)
    max_hd = max(HOLD_DAYS)

    print("=" * 70)
    print(f"  按回测要求的8策略回测 (含动态卖出)")
    print(f"  策略变体: {n} | 持股: {HOLD_DAYS}天 | 动态卖出: 止损/止盈/到期")
    print("=" * 70)

    cats = sorted(set(s.cat for s in strategies))
    print(f"  策略类别: {len(cats)}个 => {', '.join(cats)}")
    print()

    files = sorted([f for f in os.listdir(HIST_CACHE_DIR) if f.endswith("_bs.csv")])
    total = len(files)

    # acc[hold_day][strat_name] = {...}
    acc = {}
    for hd in HOLD_DAYS:
        acc[hd] = {}
        for s in strategies:
            acc[hd][s.name] = {"signals": 0, "wins": 0, "returns": [],
                               "cat": s.cat, "exit_reasons": {}}

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

        max_i = len(df) - max_hd - 2
        for i in range(65, max_i):
            row = df.iloc[i]
            if row[need_cols].isna().any():
                continue
            if not check_secondary_filters(row):
                continue

            prev = df.iloc[i - 1] if i >= 1 else None
            entry_idx = i + 1
            bp = df.iloc[entry_idx]["开盘"]
            if pd.isna(bp) or bp <= 0:
                continue

            # Evaluate all strategies
            hits = []
            for s in strategies:
                try:
                    if s.need_df and s.need_prev:
                        hit = s.func(row, df, i, prev)
                    elif s.need_df:
                        hit = s.func(row, df, i, prev)
                    elif s.need_prev:
                        hit = s.func(row, prev)
                    else:
                        hit = s.func(row, df, i, prev)  # unified call
                    if hit:
                        hits.append(s)
                except Exception:
                    pass

            if not hits:
                continue

            # For each holding period, simulate dynamic exit
            # max_hold_dynamic = hd - 1: exit checks fire within the holding period,
            # final mandatory exit at T+hd close (same as fixed hold semantics)
            for hd in HOLD_DAYS:
                exit_idx, reason = find_exit(df, entry_idx, max(1, hd - 1))
                sp = df.iloc[exit_idx]["收盘"]
                if pd.isna(sp) or sp <= 0:
                    continue

                ret = (sp / bp - 1) * 100
                is_win = ret > 0

                for s in hits:
                    d = acc[hd][s.name]
                    d["signals"] += 1
                    d["returns"].append(ret)
                    if is_win:
                        d["wins"] += 1
                    d["exit_reasons"][reason] = d["exit_reasons"].get(reason, 0) + 1
                    total_signals += 1

        if fi % 200 == 0:
            e = time.time() - t0
            print(f"  {fi}/{total} | {e:.0f}s | 剩余{e/fi*(total-fi):.0f}s | 信号{total_signals}")

    elapsed = time.time() - t0
    print(f"\n[OK] {elapsed:.0f}s | 总信号触发: {total_signals}")

    # ============================================================
    # 汇总结果
    # ============================================================
    os.makedirs("output/backtest", exist_ok=True)

    all_rows = []
    for hd in HOLD_DAYS:
        for s in strategies:
            d = acc[hd][s.name]
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
            # Count exit reason distribution
            exit_dist = d.get("exit_reasons", {})
            stop_loss_cnt = sum(v for k, v in exit_dist.items() if "止损" in k)
            take_profit_cnt = sum(v for k, v in exit_dist.items() if "止盈" in k)
            expire_cnt = sum(v for k, v in exit_dist.items() if "到期" in k)
            all_rows.append({
                "name": s.name, "cat": s.cat, "hold_days": hd,
                "signals": cnt, "wins": w, "losses": cnt - w,
                "wr": wr, "avg": avg_r, "med": med_r,
                "max_g": max(rets), "max_l": min(rets),
                "avg_w": avg_w, "avg_l": avg_l, "pl": pl,
                "stop_cnt": stop_loss_cnt, "tp_cnt": take_profit_cnt, "exp_cnt": expire_cnt,
            })

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("无满足最小信号要求的策略！")
        return

    # Composite: 70% WR + 30% signals
    log_sig = np.log1p(df["signals"].values)
    log_ref = np.log1p(100)
    sig_norm = np.clip(log_sig / (log_ref * 2.5), 0, 1)
    wr_norm = np.clip(df["wr"].values / 100.0, 0, 1)
    df["composite"] = (wr_norm * 0.7 + sig_norm * 0.3) * 100

    df.to_csv("output/backtest/final8_results.csv", index=False, encoding="utf-8-sig")

    # ============================================================
    # 报告
    # ============================================================

    # 1. 按胜率 TOP 30
    print("\n" + "=" * 100)
    print("  按胜率排序 TOP 30")
    print("=" * 100)
    top_wr = df.sort_values("wr", ascending=False).head(30).reset_index(drop=True)
    for rank, (_, r) in enumerate(top_wr.iterrows(), 1):
        exit_str = f"止损{r['stop_cnt']}/止盈{r['tp_cnt']}/到期{r['exp_cnt']}"
        print(f"  {rank:>2}. [{int(r['hold_days'])}天] {r['name']:<30} {r['cat']:<16} "
              f"胜率={r['wr']:.2f}%  信号={int(r['signals'])}  平均={r['avg']:.2f}%  "
              f"盈亏比={r['pl']:.2f}  卖出[{exit_str}]")

    # 2. 按综合评分 TOP 30
    print("\n" + "=" * 100)
    print("  按综合评分 TOP 30")
    print("=" * 100)
    top_comp = df.sort_values("composite", ascending=False).head(30).reset_index(drop=True)
    for rank, (_, r) in enumerate(top_comp.iterrows(), 1):
        print(f"  {rank:>2}. [{int(r['hold_days'])}天] {r['name']:<30} {r['cat']:<16} "
              f"胜率={r['wr']:.2f}%  信号={int(r['signals'])}  综合={r['composite']:.1f}  盈亏比={r['pl']:.2f}")

    # 3. 各类别最佳
    print("\n" + "=" * 80)
    print("  8个策略类别 - 最佳参数及表现")
    print("=" * 80)
    for cat in sorted(df["cat"].unique()):
        cat_df = df[df["cat"] == cat].sort_values("composite", ascending=False)
        best = cat_df.iloc[0]
        print(f"  {cat:<20} [{int(best['hold_days'])}天] {best['name']:<30} "
              f"胜率={best['wr']:.2f}%  信号={int(best['signals'])}  平均收益={best['avg']:.2f}%  盈亏比={best['pl']:.2f}")

    # 4. 冠军
    best_wr = df.loc[df["wr"].idxmax()]
    best_comp = df.loc[df["composite"].idxmax()]
    print("\n" + "=" * 60)
    print(f"  胜率冠军: [{int(best_wr['hold_days'])}天] {best_wr['name']} ({best_wr['cat']})")
    print(f"  胜率={best_wr['wr']:.2f}%  信号={int(best_wr['signals'])}  平均={best_wr['avg']:.2f}%  盈亏比={best_wr['pl']:.2f}")
    print(f"  综合冠军: [{int(best_comp['hold_days'])}天] {best_comp['name']} ({best_comp['cat']})")
    print(f"  胜率={best_comp['wr']:.2f}%  信号={int(best_comp['signals'])}  综合={best_comp['composite']:.1f}  盈亏比={best_comp['pl']:.2f}")
    print("=" * 60)

    # 保存MD报告
    report_path = "output/backtest/final8_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 按回测要求的8策略回测报告\n\n")
        f.write(f"回测时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"策略类别: {len(cats)}个 | 变体: {n}个 | 持股: {HOLD_DAYS}天\n")
        f.write("卖出规则: 止损(破5MA) | 止盈(5MA上+放量阴) | 到期\n\n")

        f.write("## 胜率冠军\n\n")
        f.write(f"- [{int(best_wr['hold_days'])}天] **{best_wr['name']}** ({best_wr['cat']})\n")
        f.write(f"- 胜率: **{best_wr['wr']:.2f}%** | 信号: {int(best_wr['signals'])} | 平均收益: {best_wr['avg']:.2f}% | 盈亏比: {best_wr['pl']:.2f}\n\n")

        f.write("## 8策略类别最佳\n\n")
        f.write("| 类别 | 最佳参数 | 持仓 | 胜率% | 信号 | 平均收益% | 盈亏比 |\n")
        f.write("|------|------|:--:|:--:|:--:|:--:|:--:|\n")
        for cat in sorted(df["cat"].unique()):
            cat_df = df[df["cat"] == cat].sort_values("composite", ascending=False)
            best = cat_df.iloc[0]
            f.write(f"| {best['cat']} | {best['name']} | {int(best['hold_days'])}天 | {best['wr']:.2f} | {int(best['signals'])} | {best['avg']:.2f} | {best['pl']:.2f} |\n")

        f.write(f"\n## 各持仓天数TOP5\n\n")
        for hd in HOLD_DAYS:
            f.write(f"### 持股{hd}天（含动态卖出）\n\n")
            hd_df = df[df["hold_days"] == hd].sort_values("wr", ascending=False).head(5)
            f.write("| 排名 | 策略 | 类别 | 胜率% | 信号 | 平均收益% | 盈亏比 | 卖出分布 |\n")
            f.write("|:--:|------|------|:--:|:--:|:--:|:--:|------|\n")
            for rank, (_, r) in enumerate(hd_df.iterrows(), 1):
                exit_str = f"止损{r['stop_cnt']}/止盈{r['tp_cnt']}/到期{r['exp_cnt']}"
                f.write(f"| {rank} | {r['name']} | {r['cat']} | {r['wr']:.2f} | {int(r['signals'])} | {r['avg']:.2f} | {r['pl']:.2f} | {exit_str} |\n")
            f.write("\n")

    print(f"\n[OK] 报告: {report_path}")
    print(f"[OK] 数据: output/backtest/final8_results.csv")


if __name__ == "__main__":
    run()
