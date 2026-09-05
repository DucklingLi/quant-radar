# -*- coding: utf-8 -*-
"""
backtest_quality.py · 质量因子（ROE/营收增速）对"低位金叉启动"的增量检验
=============================================================================
口径与量化模型/历史验证一致：低位(一年位置≤0.5)金叉(MA5×MA20,≤7日)启动事件 →
未来 20 日收益；胜率 = 上涨占比。

问题：质量维度（盈利能力/成长）在已获"技术低位启动"信号的股票里，
是否还能进一步区分未来表现？→ 决定质量因子是否值得加入 scan.py 打分。

数据：
  K线缓存  raw/news_k.json（backtest_news 产物：4184 只 × 800 根前复权日K）
  财务     东财业绩报表 RPT_LICO_FN_CPD（最近完整报告期）
           WEIGHTAVG_ROE 加权ROE / YSTZ 营收同比 / SJLTZ 净利同比 / XSMLL 毛利率

用法: python scripts/backtest_quality.py
"""
import json, os, math, time, urllib.request, urllib.parse, datetime

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
      "Referer": "https://data.eastmoney.com/"}

def gj(u):
    req = urllib.request.Request(u, headers=UA)
    return json.loads(urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore"))

def fetch_finance(report):
    """拉某报告期全市场业绩报表（RPT_LICO_FN_CPD）。返回 {code: row}"""
    out, page = {}, 1
    while True:
        flt = urllib.parse.quote("(REPORTDATE='%s')" % report)
        u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LICO_FN_CPD"
             "&columns=SECURITY_CODE,SECURITY_NAME_ABBR,REPORTDATE,TOTAL_OPERATE_INCOME,PARENT_NETPROFIT,"
             "WEIGHTAVG_ROE,YSTZ,SJLTZ,XSMLL&pageNumber=%d&pageSize=500&sortColumns=SECURITY_CODE&sortTypes=1&filter=%s" % (page, flt))
        d = gj(u)
        res = d.get("result") or {}
        rows = res.get("data") or []
        for r in rows:
            out[r["SECURITY_CODE"]] = r
        if len(rows) < 500 or page >= 12:
            break
        page += 1
        time.sleep(0.3)
    return out

def ma(vals, n, i):
    if i + 1 < n:
        return None
    s = sum(vals[i - n + 1:i + 1])
    return s / n

def is_launch(closes, i):
    """i 处是否刚发生低位金叉(MA5 上穿 MA20, ≤i-1 起 7 日内、金叉时一年位≤0.6)"""
    if i < 40:
        return None
    for j in range(max(30, i - 6), i + 1):
        if j < 34:
            continue
        a5, a20 = ma(closes, 5, j), ma(closes, 20, j)
        if a5 is None or a20 is None:
            continue
        a5p = ma(closes, 5, j - 1) if j >= 6 else None
        a20p = ma(closes, 20, j - 1) if j >= 20 else None
        if a5p is None or a20p is None:
            continue
        if a5 > a20 and a5p <= a20p:
            w = closes[max(0, j - 250):j + 1]
            lo, hi = min(w), max(w)
            pos = (closes[j] - lo) / (hi - lo) if hi > lo else 0.5
            if pos <= 0.60:
                return j
    return None

def main():
    kl = json.load(open(os.path.join(RAW, "news_k.json"), encoding="utf-8"))
    print("[1] kline cache stocks:", len(kl))
    # 财务：先取 2026-06-30 中报（8/31 前披露完毕），覆盖不足则用 2025-12-31 年报
    fin = None
    for rep in ("2026-06-30", "2025-12-31"):
        fin = fetch_finance(rep)
        print("[2] finance %s rows: %d" % (rep, len(fin)))
        if len(fin) > 3500:
            print("    use report:", rep)
            break
    # 事件提取
    from datetime import date
    d0 = date(2025, 9, 1).toordinal()
    d1 = date(2026, 8, 15).toordinal()
    evs = []
    for full, bars in kl.items():
        code = full[2:]
        if len(bars) < 120:
            continue
        closes = [c for _, c in bars]
        # 只对范围内有数据的股票推进
        i0 = None
        for k in range(len(bars)):
            if d0 <= date.fromisoformat(bars[k][0]).toordinal() <= d1:
                i0 = k
                break
        if i0 is None:
            continue
        seen = set()
        for i in range(i0, min(len(bars) - 21, i0 + 260)):
            j = is_launch(closes, i)
            if j is not None and j not in seen:
                seen.add(j)
                p0 = closes[j]
                p20 = closes[j + 20] if j + 20 < len(closes) else None
                if not p20:
                    continue
                w = closes[max(0, j - 250):j + 1]
                lo, hi = min(w), max(w)
                pos = (p0 - lo) / (hi - lo) if hi > lo else 0.5
                if pos > 0.60:
                    continue
                f = fin.get(code)
                evs.append({"code": code, "ret": (p20 / p0 - 1) * 100, "pos": pos,
                            "roe": f.get("WEIGHTAVG_ROE") if f else None,
                            "ystz": f.get("YSTZ") if f else None,
                            "sjltz": f.get("SJLTZ") if f else None,
                            "xsmll": f.get("XSMLL") if f else None})
    print("[3] launch events:", len(evs))

    def stat(rows):
        if not rows:
            return (0, 0.0, 0.0)
        n = len(rows)
        return (n, sum(r["ret"] for r in rows) / n,
                sum(1 for r in rows if r["ret"] > 0) / n * 100)

    has_fin = [e for e in evs if e["roe"] is not None or e["ystz"] is not None]
    print("\n===== 质量 × 低位金叉启动 · 未来20日（事件数 %d / 有财务 %d） =====" % (len(evs), len(has_fin)))
    print("  %-22s %8s %9s %8s" % ("分组", "样本", "平均收益", "胜率"))
    # ROE 分桶（亏损股另计）
    def bucket(evs, key, edges, labels):
        from collections import defaultdict
        g = defaultdict(list)
        for e in evs:
            v = e[key]
            if v is None:
                g["无财务"].append(e)
            else:
                lab = "亏损/NA" 
                for i, ed in enumerate(edges):
                    if v < ed:
                        lab = labels[i]; break
                else:
                    lab = labels[-1]
                g[lab].append(e)
        return g
    print("\n--- A) 按 ROE(加权,%) ---")
    g = bucket(evs, "roe", [0, 5, 10, 15], ["亏损", "0~5", "5~10", "10~15", ">15"])
    for k in ["亏损", "0~5", "5~10", "10~15", ">15", "无财务"]:
        n, avg, win = stat(g.get(k, []))
        print("   %-12s n=%-6d avg=%+7.2f%%  win=%5.1f%%" % (k, n, avg, win))
    print("\n--- B) 按营收增速 YSTZ(%) ---")
    g = bucket(evs, "ystz", [-10, 0, 20], ["<-10", "-10~0", "0~20", ">20"])
    for k in ["<-10", "-10~0", "0~20", ">20", "无财务"]:
        n, avg, win = stat(g.get(k, []))
        print("   %-12s n=%-6d avg=%+7.2f%%  win=%5.1f%%" % (k, n, avg, win))
    print("\n--- C) 质量合成（ROE>10 且 营收>0 = 优；ROE<5 或亏损 = 弱；其余=中） ---")
    q = {"优": [], "中": [], "弱": [], "无财务": []}
    for e in evs:
        r, y = e["roe"], e["ystz"]
        if r is None:
            q["无财务"].append(e)
        elif r > 10 and (y is None or y > 0):
            q["优"].append(e)
        elif r < 5:
            q["弱"].append(e)
        else:
            q["中"].append(e)
    for k in ["优", "中", "弱", "无财务"]:
        n, avg, win = stat(q[k])
        print("   %-8s n=%-6d avg=%+7.2f%%  win=%5.1f%%" % (k, n, avg, win))
    # 低位内再切（pos≤0.33 的深低位样本内质量桶）
    print("\n--- D) 深低位(位置≤0.33)样本内质量桶 ---")
    qd = {"优": [], "中": [], "弱": []}
    for e in evs:
        if e["pos"] > 0.33:
            continue
        r = e["roe"]
        if r is None:
            continue
        if r > 10 and (e["ystz"] is None or e["ystz"] > 0):
            qd["优"].append(e)
        elif r < 5:
            qd["弱"].append(e)
        else:
            qd["中"].append(e)
    for k in ["优", "中", "弱"]:
        n, avg, win = stat(qd[k])
        print("   %-8s n=%-6d avg=%+7.2f%%  win=%5.1f%%" % (k, n, avg, win))

if __name__ == "__main__":
    main()
