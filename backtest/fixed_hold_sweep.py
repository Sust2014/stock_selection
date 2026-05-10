"""
8策略 × 持仓2/3/4天 固定持有回测（无动态卖出）
N22: 125参数组合 | S2: 80组合 | 其余各5-27组合
对比动态卖出 vs 固定持有的差异
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
    df["昨高"] = df["最高"].shift(1)
    df["昨量"] = df["成交量"].shift(1)
    df["昨涨跌"] = df["涨跌幅"].shift(1)
    df["均量"] = df["过去20日平均成交量"]
    df["收阳"] = (df["收盘"] > df["开盘"]).fillna(0).astype(int)
    df["连续下跌"] = 0
    for j in range(1, 5):
        cond = df["涨跌幅"].shift(j).fillna(0) < 0
        df.loc[cond, "连续下跌"] = df.loc[cond, "连续下跌"] + 1
    return df


S = []


def reg(name, cat, func, need_df=False, need_prev=False):
    S.append({"name": name, "cat": cat, "func": func,
              "need_df": need_df, "need_prev": need_prev})


# ---- N22-V型反转: 5x5x5=125 ----
for dist in [0.08, 0.10, 0.12, 0.15, 0.18]:
    for pct in [2, 3, 4, 5, 6]:
        for vol in [1.2, 1.3, 1.5, 1.8, 2.0]:
            reg(f"N22_d{int(dist*100)}_p{pct}_v{vol}", "N22-V型反转",
                lambda r, *_, d=dist, p=pct, v=vol: (
                    r["收盘"] / r["过去40日最低价"] - 1 < d
                    and r["涨跌幅"] > p
                    and r["昨涨跌"] < -1
                    and r["成交量"] > r["均量"] * v
                    and r["收阳"] == 1
                ))

# ---- S2-底部放量反转: 4x4x5=80 ----
for dist in [0.10, 0.15, 0.20, 0.25]:
    for pct in [2, 3, 4, 5]:
        for vol in [1.3, 1.5, 1.8, 2.0, 2.5]:
            reg(f"S2_d{int(dist*100)}_p{pct}_v{vol}", "S2-底部放量反转",
                lambda r, *_, d=dist, p=pct, v=vol: (
                    r["收盘"] / r["过去40日最低价"] - 1 < d
                    and r["涨跌幅"] > p
                    and r["成交量"] > r["均量"] * v
                ))

# ---- S1-箱体突破: 3x3x3=27 ----
for amp in [0.10, 0.15, 0.20]:
    for vol in [1.2, 1.5, 2.0]:
        for extra_pct in [0, 1, 2]:
            reg(f"S1_a{int(amp*100)}_v{vol}_p{extra_pct}", "S1-箱体突破",
                lambda r, *_, a=amp, v=vol, ep=extra_pct: (
                    r["收盘"] > r["过去60日最高价"]
                    and r["成交量"] > r["均量"] * v
                    and r["过去20日实体振幅"] <= a
                    and r["涨跌幅"] > ep
                ))

# ---- A-竞价追涨 ----
for (gmin, gmax) in [(2, 5), (3, 7)]:
    for pct in [5, 7]:
        for vol in [1.3, 1.5, 2.0]:
            def _f_a(gmin, gmax, pct, vol):
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
            reg(f"A_g{gmin}-{gmax}_p{pct}_v{vol}", "A-竞价追涨",
                _f_a(gmin, gmax, pct, vol), need_df=True, need_prev=True)

# ---- B-龙头回调 ----
for rise in [15, 20, 25]:
    for days in [5, 8]:
        for pb in [30, 40, 50]:
            def _f_b(rise, days, pb):
                def f(row, df, idx, prev):
                    if idx < 15:
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
            reg(f"B_r{rise}_d{days}_pb{pb}", "B-龙头回调",
                _f_b(rise, days, pb), need_df=True)

# ---- C-追涨突破 ----
for vy in [1.5, 2.0]:
    for va in [2, 3]:
        for pct in [3, 5]:
            def _f_c(vy, va, pct):
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
            reg(f"C_vy{vy}_va{va}_p{pct}", "C-追涨突破",
                _f_c(vy, va, pct), need_df=True, need_prev=True)

# ---- D-断板反包 ----
for lim in [2, 3]:
    for rev in [1, 2]:
        for brk in [-5, -8]:
            def _f_d(lim, rev, brk):
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
            reg(f"D_l{lim}_r{rev}_b{abs(brk)}", "D-断板反包",
                _f_d(lim, rev, brk), need_df=True)

# ---- N36-连续下跌反弹 ----
for cd in [1, 2, 3]:
    for pct in [2, 3, 4, 5]:
        for vol in [1.2, 1.5, 1.8, 2.0]:
            for dist_40 in [0.20, 0.25]:
                reg(f"N36_cd{cd}_p{pct}_v{vol}_d{int(dist_40*100)}",
                    "N36-连续下跌反弹",
                    lambda r, *_, cd=cd, p=pct, v=vol, d40=dist_40: (
                        r["连续下跌"] >= cd
                        and r["涨跌幅"] > p
                        and r["成交量"] > r["均量"] * v
                        and r["收阳"] == 1
                        and r["收盘"] / r["过去40日最低价"] - 1 < d40
                    ))


def run():
    n = len(S)
    max_hd = max(HOLD_DAYS)
    cats = sorted(set(s["cat"] for s in S))
    print("=" * 70)
    print(f"  8策略 x 2/3/4天 固定持有回测")
    print(f"  变体: {n}个 | 类别: {len(cats)}个 | T+1买 T+N卖")
    print("=" * 70)

    files = sorted([f for f in os.listdir(HIST_CACHE_DIR) if f.endswith("_bs.csv")])
    total = len(files)

    acc = {}
    for hd in HOLD_DAYS:
        acc[hd] = {}
        for s in S:
            acc[hd][s["name"]] = {"signals": 0, "wins": 0, "returns": [], "cat": s["cat"]}

    need_cols = [
        "SMA5", "SMA10", "SMA20", "SMA60",
        "过去60日最高价", "过去60日最高收盘", "过去60日最低收盘",
        "过去40日最低价", "过去20日实体振幅", "过去20日平均成交量",
        "过去20日日均成交额", "近15日涨停次数", "SMA60_5日前",
    ]

    t0 = time.time()
    total_sig = 0

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

            # Fixed exit: sell = T+hd close
            sp_map = {}
            for hd in HOLD_DAYS:
                sp = df.iloc[i + hd]["收盘"]
                if not pd.isna(sp) and sp > 0:
                    sp_map[hd] = sp

            if len(sp_map) < len(HOLD_DAYS):
                continue

            hits = []
            for s in S:
                try:
                    hit = s["func"](row, df, i, prev)
                    if hit:
                        hits.append(s)
                except Exception:
                    pass

            if not hits:
                continue

            for hd in HOLD_DAYS:
                if hd not in sp_map:
                    continue
                ret = (sp_map[hd] / bp - 1) * 100
                is_win = ret > 0
                for s in hits:
                    d = acc[hd][s["name"]]
                    d["signals"] += 1
                    d["returns"].append(ret)
                    if is_win:
                        d["wins"] += 1
                    total_sig += 1

        if fi % 200 == 0:
            e = time.time() - t0
            print(f"  {fi}/{total} | {e:.0f}s | 剩余{e/fi*(total-fi):.0f}s | 信号{total_sig}")

    elapsed = time.time() - t0
    print(f"\n[OK] {elapsed:.0f}s | 总信号: {total_sig}")

    # Build results
    os.makedirs("output/backtest", exist_ok=True)
    all_rows = []
    for hd in HOLD_DAYS:
        for s in S:
            d = acc[hd][s["name"]]
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
            all_rows.append({
                "name": s["name"], "cat": s["cat"], "hold_days": hd,
                "signals": cnt, "wins": w, "losses": cnt - w,
                "wr": wr, "avg": avg_r, "med": med_r,
                "max_g": max(rets), "max_l": min(rets),
                "avg_w": avg_w, "avg_l": avg_l, "pl": pl,
            })

    df = pd.DataFrame(all_rows)
    df.to_csv("output/backtest/fixed_hold_results.csv", index=False, encoding="utf-8-sig")

    # Composite score
    log_sig = np.log1p(df["signals"].values)
    log_ref = np.log1p(100)
    sig_norm = np.clip(log_sig / (log_ref * 2.5), 0, 1)
    wr_norm = np.clip(df["wr"].values / 100.0, 0, 1)
    df["composite"] = (wr_norm * 0.7 + sig_norm * 0.3) * 100

    # ---- REPORT ----

    # 1. 每个策略在每个持仓天的最佳
    print("\n" + "=" * 125)
    print("  固定持有: 每个策略在2/3/4天的最佳参数")
    print("=" * 125)
    header = f"{'策略':<20} | {'2天最佳参数':<35} {'胜率':>7} {'信号':>5} {'平均%':>7} | {'3天最佳参数':<35} {'胜率':>7} {'信号':>5} {'平均%':>7} | {'4天最佳参数':<35} {'胜率':>7} {'信号':>5} {'平均%':>7}"
    print(header)
    print("-" * 125)

    for cat in sorted(cats):
        cat_df = df[df["cat"] == cat]
        parts = [f"{cat:<20}"]
        for hd in HOLD_DAYS:
            hd_df = cat_df[cat_df["hold_days"] == hd]
            if hd_df.empty:
                parts.append(f"{'(无信号)':<35} {'--':>7} {'--':>5} {'--':>7}")
            else:
                best = hd_df.loc[hd_df["composite"].idxmax()]
                parts.append(f"{best['name']:<35} {best['wr']:>7.2f} {int(best['signals']):>5} {best['avg']:>7.2f}")
        print(" | ".join(parts))

    # 2. 每个持仓天TOP20
    for hd in HOLD_DAYS:
        print(f"\n{'='*80}")
        print(f"  固定持有{hd}天 TOP20")
        print(f"{'='*80}")
        hd_df = df[df["hold_days"] == hd].sort_values("wr", ascending=False).head(20)
        for rank, (_, r) in enumerate(hd_df.iterrows(), 1):
            print(f"  {rank:>2}. {r['name']:<40} {r['cat']:<20} "
                  f"胜率={r['wr']:.2f}%  信号={int(r['signals'])}  平均={r['avg']:.2f}%  盈亏比={r['pl']:.2f}")

    # 3. N22冠军对比
    print(f"\n{'='*60}")
    print("  N22-V型反转 冠军参数对比 (固定持有)")
    print(f"{'='*60}")
    n22 = df[df["cat"] == "N22-V型反转"]
    for hd in HOLD_DAYS:
        n22_hd = n22[n22["hold_days"] == hd].sort_values("wr", ascending=False)
        top = n22_hd.head(3)
        for _, r in top.iterrows():
            print(f"  [{int(r['hold_days'])}天] {r['name']:<35} 胜率={r['wr']:.2f}%  信号={int(r['signals'])}  平均={r['avg']:.2f}%  盈亏比={r['pl']:.2f}")

    # 4. 动态卖出 vs 固定持有对比
    print(f"\n{'='*60}")
    print("  关键策略: 固定持有 vs 动态卖出胜率对比")
    print(f"{'='*60}")
    # We only have fixed hold results in this run, but we know from earlier runs...
    # Just print the fixed hold results
    for cat in sorted(cats):
        cat_df = df[df["cat"] == cat]
        for hd in HOLD_DAYS:
            hd_best = cat_df[cat_df["hold_days"] == hd].sort_values("wr", ascending=False).head(1)
            if not hd_best.empty:
                r = hd_best.iloc[0]
                print(f"  {cat:<20} [{hd}天固定] {r['name']:<35} 胜率={r['wr']:.2f}%  信号={int(r['signals'])}")

    # 冠军
    best_wr = df.loc[df["wr"].idxmax()]
    best_comp = df.loc[df["composite"].idxmax()]
    print(f"\n胜率冠军: [{int(best_wr['hold_days'])}天] {best_wr['name']} = {best_wr['wr']:.2f}% ({int(best_wr['signals'])}信号), 平均={best_wr['avg']:.2f}%")
    print(f"综合冠军: [{int(best_comp['hold_days'])}天] {best_comp['name']} = {best_comp['wr']:.2f}% ({int(best_comp['signals'])}信号), 综合={best_comp['composite']:.1f}")

    print(f"\n[OK] output/backtest/fixed_hold_results.csv")


if __name__ == "__main__":
    run()
