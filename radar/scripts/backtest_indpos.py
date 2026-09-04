# -*- coding: utf-8 -*-
"""
行业位置共振回测（检验：选股时参考"行业一年位置"是否提升低位启动个股的胜率）
==========================================================================
方法（与 backtest_launch 一致的历史截面法，静态池含幸存者偏差）：
  每 5 个交易日取截面 T；对池内每只股票算截止 T 的"低位金叉距今天数 d"
  （MA5×MA20 或 MACD DIF×DEA 上穿，金叉日价格处于该股一年区间低位<0.70）。
  关注组 = 刚启动样本 d≤7（本榜执行清单的核心群体）；
  再按 T 时点该股所属申万二级行业的一年位置分桶：
    行业低位(位置<0.33) / 行业中性(0.33~0.67) / 行业高位(>0.67)
  比较各组未来 20 日平均收益与上涨占比。

检验问题（决定 v7 融合规则）：
  Q1 低行业位共振：行业处于一年低位区时，个股低位金叉的期望收益是否更高？
  Q2 高行业位警惕：行业已处一年高位区时，该行业内个股启动是否更易失败？
对照组：同时点无低位金叉的样本（市场基数）。
局限：池按今日市值；不含成本；行业一年位置取板块指数近 250 交易日分位。
"""
import bisect
import json
import os
import time
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HORIZON = 20
STEP = 5


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://gu.qq.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def fetch_day(code, n=800):
    u = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
         "?param=%s,day,,,%d,qfq" % (code, min(n, 800)))
    d = json.loads(get(u))
    node = d.get("data", {}).get(code, {})
    arr = node.get("day") or node.get("qfqday") or []
    out = []
    for row in arr:
        try:
            out.append([int(row[0].replace("-", "")), float(row[2])])
        except Exception:
            continue
    out.sort(key=lambda x: x[0])
    return out


def ema(vals, n):
    k = 2.0 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def sma(vals, n):
    return [sum(vals[max(0, i - n + 1):i + 1]) / min(n, i + 1) for i in range(len(vals))]


def launch_days_at(closes, idx):
    """截止 idx 最近一次低位金叉距 idx 的天数；无则 None。"""
    if idx < 70:
        return None
    seg = closes[:idx + 1]
    hi, lo = max(seg[-260:]), min(seg[-260:])
    ma5 = sma(seg, 5)
    ma20 = sma(seg, 20)
    e12 = ema(seg, 12)
    e26 = ema(seg, 26)
    dif = [a - b for a, b in zip(e12, e26)]
    dea = ema(dif, 9)
    res = None
    for a, b in ((ma5, ma20), (dif, dea)):
        n = len(a)
        for i in range(n - 1, max(n - 120, 1), -1):
            if a[i] > b[i] and a[i - 1] <= b[i - 1]:
                c = seg[i]
                px = (c - lo) / (hi - lo) if hi > lo else 0.5
                if px < 0.70:
                    res = i
                break
    if res is None:
        return None
    days = idx - res                      # 金叉发生在距截面 idx 多少日前（seg=closes[:idx+1]）
    return days if days <= 120 else None


def main():
    cur = json.load(open(os.path.join(ROOT, "data.json"), encoding="utf-8"))
    pool = cur.get("pool") or []
    mp = json.load(open(os.path.join(HERE, "data", "industry_map.json"), encoding="utf-8"))
    boards = json.load(open(os.path.join(HERE, "data", "sw2_boards.json"), encoding="utf-8"))
    name2code = {b["name"]: b["code"] for b in boards}
    print("pool=%d" % len(pool))

    # ---- 个股 + 行业归属 ----
    stocks = []          # {code, dates, closes, bcode}
    bneed = set()
    for i, p in enumerate(pool):
        code = p["code"]
        ind = mp.get(code)
        bcode = name2code.get(ind) if ind else None
        try:
            d = fetch_day(code, 800)
            if len(d) >= 560 and bcode:
                stocks.append({"code": code, "dates": [x[0] for x in d],
                               "closes": [x[1] for x in d], "bcode": bcode})
                bneed.add(bcode)
        except Exception:
            pass
        if (i + 1) % 120 == 0:
            print("  stock kline %d/%d usable=%d" % (i + 1, len(pool), len(stocks)))
        time.sleep(0.05)
    print("usable stocks=%d boards=%d" % (len(stocks), len(bneed)))

    # ---- 板块日K（一年位置窗口） ----
    boards_day = {}
    for i, b in enumerate(sorted(bneed)):
        try:
            d = fetch_day(b, 800)
            if len(d) >= 560:
                boards_day[b] = d
        except Exception:
            pass
        if (i + 1) % 20 == 0:
            print("  board kline %d/%d ok=%d" % (i + 1, len(bneed), len(boards_day)))
        time.sleep(0.05)
    print("usable boards=%d" % len(boards_day))

    # ---- 截面 ----
    last_idx = min(len(s["closes"]) for s in stocks)
    Ts = list(range(300, last_idx - 1 - HORIZON, STEP))
    print("sections=%d" % len(Ts))

    # 结果容器
    ind_buckets = {"低(<0.33)": [], "中(0.33~0.67)": [], "高(>0.67)": []}
    no_launch_all = []
    launch_all = []
    n_skip = 0
    for T in Ts:
        for s in stocks:
            closes = s["closes"]
            if T + HORIZON >= len(closes):
                continue
            ld = launch_days_at(closes, T)
            fut = (closes[T + HORIZON] / closes[T] - 1) * 100
            if ld is None:
                no_launch_all.append(fut)
                continue
            if ld > 7:
                continue
            launch_all.append(fut)
            # 行业一年位置：以个股 T 日对应板块指数的 250 日窗口分位
            bd = boards_day.get(s["bcode"])
            if not bd:
                continue
            bdates = [x[0] for x in bd]
            bcl = [x[1] for x in bd]
            j = bisect.bisect_left(bdates, s["dates"][T])
            if j >= len(bdates) or bdates[j] != s["dates"][T] or j < 250:
                n_skip += 1
                continue
            seg = bcl[j - 249:j + 1]
            lo, hi = min(seg), max(seg)
            pos = (bcl[j] - lo) / (hi - lo) if hi > lo else 0.5
            key = "低(<0.33)" if pos < 0.33 else ("高(>0.67)" if pos > 0.67 else "中(0.33~0.67)")
            ind_buckets[key].append(fut)
    print("skip(board对齐)样本=%d" % n_skip)

    def stat(arr):
        if not arr:
            return None
        return len(arr), sum(arr) / len(arr), sum(1 for x in arr if x > 0) / len(arr) * 100

    print("\n=== 行业一年位置 × 低位启动(金叉≤7日) · 未来20日 ===")
    print("  %-16s %8s %9s %8s" % ("分组", "样本", "平均", "上涨占比"))
    for k in ("低(<0.33)", "中(0.33~0.67)", "高(>0.67)"):
        r = stat(ind_buckets[k])
        if not r:
            print("  %-16s %8s %9s %8s" % (k, "无样本", "—", "—"))
        else:
            print("  %-16s %8d %+8.2f%% %7.1f%%" % (k, r[0], r[1], r[2]))
    ra = stat(launch_all)
    rn = stat(no_launch_all)
    print("  %-16s %8d %+8.2f%% %7.1f%%   <- 全部低位启动(不分行业)" % (ra[0], ra[0], ra[1], ra[2]))
    print("  %-16s %8d %+8.2f%% %7.1f%%   <- 无金叉对照组" % (rn[0], rn[0], rn[1], rn[2]))
    lo_, md_, hi_ = ind_buckets["低(<0.33)"], ind_buckets["中(0.33~0.67)"], ind_buckets["高(>0.67)"]
    if lo_ and hi_:
        d = sum(lo_) / len(lo_) - sum(hi_) / len(hi_)
        print("\n低行业位 vs 高行业位 平均收益差 = %+.2fpp" % d)


if __name__ == "__main__":
    main()
