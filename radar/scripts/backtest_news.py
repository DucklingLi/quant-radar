# -*- coding: utf-8 -*-
"""
backtest_news.py · 消息面(业绩预告事件)研究回测
=====================================================================
口径与量化模型一致：公告日后首个交易日收盘买入 → 持有 20 个交易日收盘卖出。
胜率 = P20 > P0 占比；超额 = 个股 20 日收益 − 沪深300 同区间收益。

事件源：东方财富业绩预告中心 datacenter (RPT_PUBLIC_OP_PREDICT)
   NOTICE_DATE 2024-01-01 ~ 2026-07-31，全市场 A 股，可历史翻页。
K线源：腾讯 newfqkline（与 scan 一致），个股拉 800 根（前复权）。

检验问题：
  Q1 消息方向是否有效 —— 预增/扭亏 vs 预减/首亏 的未来 20 日区分度
  Q2 相对大盘超额 —— 消息能否战胜"公告日买入 hs300"
  Q3 低位 + 利好叠加 —— 预增公告时股价处一年低/中/高位，未来表现
      （直接回答：消息因子能否加成我们的"低位启动"模型）

用法: python scripts/backtest_news.py
产物: raw/news_events.json（事件表，可断点）+ raw/news_k.json（K线缓存）
"""
import json, os, sys, math, time, urllib.request, urllib.parse

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
os.makedirs(RAW, exist_ok=True)
EVT = os.path.join(RAW, "news_events.json")
KLS = os.path.join(RAW, "news_k.json")
HOLD = 20            # 持有交易日
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
      "Referer": "https://data.eastmoney.com/"}

def gj(u):
    req = urllib.request.Request(u, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore"))

# ---------- 阶段 A：拉取历史事件 ----------
def fetch_events():
    evs = []
    page = 1
    while True:
        flt = urllib.parse.quote("(NOTICE_DATE>='2024-01-01')(NOTICE_DATE<='2026-07-31')")
        u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_PUBLIC_OP_PREDICT"
             "&columns=ALL&pageNumber=%d&pageSize=500&sortColumns=NOTICE_DATE&sortTypes=1&filter=%s" % (page, flt))
        rows = ((gj(u).get("result") or {}).get("data")) or []
        if not rows:
            break
        for x in rows:
            nm = x.get("SECURITY_NAME_ABBR") or ""
            if "ST" in nm.upper():
                continue
            evs.append({"code": x["SECURITY_CODE"], "name": nm, "date": (x.get("NOTICE_DATE") or "")[:10],
                        "type": x.get("FORECASTTYPE") or "", "incL": x.get("INCREASEL"), "incT": x.get("INCREASET"),
                        "amtL": x.get("FORECASTL"), "amtT": x.get("FORECASTT")})
        if len(rows) < 500:
            break
        page += 1
        if page > 60:
            break
        time.sleep(0.4)
    json.dump(evs, open(EVT, "w", encoding="utf-8"), ensure_ascii=False)
    print("[A] events:", len(evs), "->", EVT)
    return evs

def tf(code):
    c = code.strip()
    if c[0] in "69" or c.startswith("5"):
        return "sh" + c
    if c[0] in "03" or c.startswith("1") or c.startswith("2"):
        return "sz" + c
    if c[0] in "48":
        return "bj" + c
    return None

def fetch_k(full):
    u = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=%s,day,,,800,qfq" % full)
    try:
        d = gj(u)
    except Exception:
        return None
    node = d.get("data", {}).get(full, {})
    arr = node.get("day") or node.get("qfqday") or []
    return [(r[0], float(r[2])) for r in arr]

# ---------- 阶段 B：K线缓存（断点续传） ----------
def ensure_klines(evs):
    cache = {}
    if os.path.exists(KLS):
        cache = json.load(open(KLS, encoding="utf-8"))
    need = {}
    for e in evs:
        f = tf(e["code"])
        if not f:
            continue
        need.setdefault(f, e["code"])
    todo = [f for f in need if f not in cache]
    print("[B] stocks:", len(need), "cached:", len(cache), "todo:", len(todo))
    for i, f in enumerate(todo):
        kl = fetch_k(f)
        if kl and len(kl) > 30:
            cache[f] = kl
        if (i + 1) % 120 == 0:
            json.dump(cache, open(KLS, "w", encoding="utf-8"))
            print("   ...%d/%d cached" % (i + 1, len(todo)))
        time.sleep(0.12)
    json.dump(cache, open(KLS, "w", encoding="utf-8"))
    print("[B] done. cache:", len(cache))
    return cache

# ---------- 阶段 C：事件对齐与分组统计 ----------
def align(ev, cache):
    f = tf(ev["code"])
    kl = cache.get(f)
    if not kl:
        return None
    n = len(kl)
    # 公告日后第一个交易日
    i = 0
    while i < n and kl[i][0] <= ev["date"]:
        i += 1
    if i + HOLD >= n:
        return None                      # 数据不足
    p0 = kl[i][1]
    p20 = kl[i + HOLD][1]
    if p0 <= 0:
        return None
    ret = (p20 / p0 - 1) * 100
    # 一年位置：买入日在前 ~250 交易日的区间位置（0=贴一年低点）
    w = kl[max(0, i - 250):i + 1]
    lo = min(x[1] for x in w)
    hi = max(x[1] for x in w)
    pos = (p0 - lo) / (hi - lo) if hi > lo else 0.5
    return {"ret": ret, "pos": pos, "buy": kl[i][0], "sell": kl[i + HOLD][0]}

def idx_ret(kl, buy, sell):
    """hs300 同区间收益 %"""
    i = 0
    n = len(kl)
    while i < n and kl[i][0] <= buy:
        i += 1
    j = i
    while j < n and kl[j][0] <= sell:
        j += 1
    if i >= n or j >= n or i + 20 > n:
        # 无法对齐时用最近邻 j=i+20
        j = min(i + HOLD, n - 1)
    p0, p1 = kl[i][1], kl[j][1]
    return (p1 / p0 - 1) * 100 if p0 else 0.0

def stat(rows):
    if not rows:
        return (0, 0.0, 0.0, 0.0)
    n = len(rows)
    avg = sum(r["ret"] for r in rows) / n
    win = sum(1 for r in rows if r["ret"] > 0) / n * 100
    ex = sum(r["ex"] for r in rows) / n
    return (n, avg, win, ex)

def main():
    evs = fetch_events() if not os.path.exists(EVT) else json.load(open(EVT, encoding="utf-8"))
    cache = ensure_klines(evs)
    # hs300 指数 K线
    idx = fetch_k("sh000300") or []
    print("[C] index bars:", len(idx))

    grp = {"预增": [], "扭亏": [], "略增": [], "续盈": [], "减亏": [],
           "预减": [], "首亏": [], "略减": [], "续亏": [], "增亏": [], "不确定": []}
    pos_pre = {k: [] for k in ("低(<0.33)", "中(0.33~0.67)", "高(>0.67)")}
    hit = miss = 0
    for e in evs:
        r = align(e, cache)
        if r is None:
            miss += 1
            continue
        hit += 1
        r["ex"] = r["ret"] - idx_ret(idx, r["buy"], r["sell"])
        t = e["type"] or "不确定"
        if t not in grp:
            t = "不确定"
        grp[t].append(r)
        if t in ("预增", "扭亏", "略增", "续盈", "减亏"):
            p = "低(<0.33)" if r["pos"] < 0.33 else ("高(>0.67)" if r["pos"] > 0.67 else "中(0.33~0.67)")
            pos_pre[p].append(r)
    print("[C] aligned:", hit, "skip:", miss)

    print("\n===== Q1 消息方向有效性 · 未来20日（公告次日收盘买入） =====")
    print("  %-8s %8s %10s %9s %12s" % ("预告类型", "样本", "平均收益", "胜率(涨占)", "平均超额"))
    order = ["预增", "扭亏", "略增", "续盈", "减亏", "预减", "首亏", "略减", "续亏", "增亏", "不确定"]
    for k in order:
        n, avg, win, ex = stat(grp[k])
        print("  %-8s %8d %+9.2f%% %8.1f%% %+11.2f%%" % (k, n, avg, win, ex))

    print("\n===== Q2 多空对照（事件方向的有效性） =====")
    pos_v = grp["预增"] + grp["扭亏"] + grp["减亏"]
    neg_v = grp["预减"] + grp["首亏"]
    for lab, g in (("正向(预增+扭亏+减亏)", pos_v), ("负向(预减+首亏)", neg_v)):
        n, avg, win, ex = stat(g)
        print("  %-20s n=%-5d avg=%+7.2f%%  win=%5.1f%%  ex=%+7.2f%%" % (lab, n, avg, win, ex))

    print("\n===== Q3 低位+利好叠加（正向事件按公告日股价一年位置分桶） =====")
    for k in ("低(<0.33)", "中(0.33~0.67)", "高(>0.67)"):
        n, avg, win, ex = stat(pos_pre[k])
        print("  %-18s n=%-5d avg=%+7.2f%%  win=%5.1f%%  ex=%+7.2f%%" % (k, n, avg, win, ex))

    # 全样本基准
    allr = [r for g in grp.values() for r in g]
    n, avg, win, ex = stat(allr)
    print("\n  [对照] 全部预告事件: n=%d avg=%+.2f%% win=%.1f%% ex=%+.2f%%" % (n, avg, win, ex))

if __name__ == "__main__":
    main()
