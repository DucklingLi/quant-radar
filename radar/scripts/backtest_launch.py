# -*- coding: utf-8 -*-
"""
金叉新鲜度验证回测（验证用户假设："已启动数日可能转跌，刚金叉首启更优"）
=========================================================================
每 5 个交易日取截面 T，对池内每只股票计算截止 T 的"最近一次低位金叉(MA5×MA20 或
MACD DIF×DEA，金叉日价格在一年低位区<0.70)距今的天数 d_T"，分组：
  首启组   : d≤3（刚金叉 1~3 日）
  早启组   : 4≤d≤7
  已启动组 : 8≤d≤30（已启动一段时间）
  未启动组 : 无低位金叉
比较各组未来 20 日平均收益与上涨占比。
局限：池按今日市值（幸存者偏差）；不含成本。用于检验"启动新鲜度"是否影响期望收益。
"""
import json
import math
import os
import time
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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
    """截止 idx 的最近一次低位金叉距 idx 的天数；无则 None。"""
    if idx < 70:
        return None
    seg = closes[:idx + 1]
    hi, lo = max(seg[-260:]), min(seg[-260:])
    ma5 = sma(seg, 5)
    ma20 = sma(seg, 20)
    ema12 = ema(seg, 12)
    ema26 = ema(seg, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = ema(dif, 9)
    res = None
    for a, b, name in ((ma5, ma20, "ma"), (dif, dea, "macd")):
        n = len(a)
        for i in range(n - 1, max(n - 120, 1), -1):
            if a[i] > b[i] and a[i - 1] <= b[i - 1]:
                c = seg[i]
                px = (c - lo) / (hi - lo) if hi > lo else 0.5
                if px < 0.70:
                    days = n - 1 - i
                    res = days if res is None else min(res, days)
                break
    return res if (res is None or res <= 120) else None


def main():
    cur = json.load(open(os.path.join(ROOT, "data.json"), encoding="utf-8"))
    pool = cur.get("pool") or []
    print("pool=%d" % len(pool))
    days = {}
    for i, p in enumerate(pool):
        try:
            d = fetch_day(p["code"], 800)
            if len(d) >= 550:
                days[p["code"]] = d
        except Exception:
            pass
        if (i + 1) % 120 == 0:
            print("  kline %d/%d usable=%d" % (i + 1, len(pool), len(days)))
        time.sleep(0.06)
    print("usable=%d" % len(days))
    last_idx = min(len(d) for d in days.values())
    Ts = list(range(260, last_idx - 1 - HORIZON, STEP))
    print("sections=%d" % len(Ts))

    groups = {"首启(金叉≤3日)": [], "早启(4~7日)": [], "已启动(8~30日)": [], "未启动(无金叉)": []}
    for T in Ts:
        rows = []
        for c, d in days.items():
            closes = [x[1] for x in d]
            if T + HORIZON >= len(closes):
                continue
            ld = launch_days_at(closes, T)
            fut = (closes[T + HORIZON] / closes[T] - 1) * 100
            rows.append((ld, fut))
        for ld, fut in rows:
            if ld is not None and ld <= 3:
                groups["首启(金叉≤3日)"].append(fut)
            elif ld is not None and ld <= 7:
                groups["早启(4~7日)"].append(fut)
            elif ld is not None and ld <= 30:
                groups["已启动(8~30日)"].append(fut)
            else:
                groups["未启动(无金叉)"].append(fut)
    print("\n=== 各组 未来20日平均收益（截面数 %d）===" % len(Ts))
    for k, arr in groups.items():
        if not arr:
            print("  %-18s 无样本" % k)
            continue
        avg = sum(arr) / len(arr)
        win = sum(1 for x in arr if x > 0) / len(arr) * 100
        print("  %-18s 样本%6d  平均 %+5.2f%%  上涨占比 %4.1f%%" % (k, len(arr), avg, win))


if __name__ == "__main__":
    main()
