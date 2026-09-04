# -*- coding: utf-8 -*-
"""
v3 逻辑验证回测：市场温度分桶 + 板块因子增量
============================================
评估日按「市场温度」(连续 proxy：沪深300一年位置×0.64 + 近60日动量映射×0.36)
分 冷(<34)/温(34~66)/热(>66) 三桶，验证统一低位逻辑在**各种热度**下的区分度（
而非牛熊开关）；同时对比 不含板块因子(A) 与 含板块因子(B) 的增量。
板块因子 proxy：个股所属行业(池内同行)60日收益中位数 = 板块动量；
个股相对强度 = 个股60日收益 - 板块中位。权重形状对齐 scan.py v3。
局限：样本为今日市值Top池（幸存者偏差）、广度维度用中性分（无历史全A快照）。
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


def fx_at(day, idx):
    """T 处：pos_raw/fix_raw/ret60/vol_raw；None=历史不足"""
    seg = day[:idx + 1]
    if len(seg) < 300:
        return None
    c = [x[1] for x in seg]
    last = c[-1]
    hi, lo = max(c[-260:]), min(c[-260:])
    pos = (last - lo) / (hi - lo) if hi > lo else 0.5
    mom20 = (last / c[-21] - 1) * 100
    ret60 = (last / c[-61] - 1) * 100
    rets = [c[i] / c[i - 1] - 1 for i in range(len(c) - 20, len(c))]
    mu = sum(rets) / 20
    var = sum((r - mu) ** 2 for r in rets) / 19
    vol = math.sqrt(var) * math.sqrt(252) * 100 if var >= 0 else 30
    return {"pos_raw": max(0.0, min(1.0, 1.0 - pos)), "fix_raw": mom20,
            "ret60": ret60, "vol_raw": vol}


def fix_score(m):
    if m is None:
        return 50.0
    if m < -18 or m > 25:
        return 5.0
    if m < -5:
        return 20.0
    if m < 2:
        return 55.0
    if m <= 12:
        return 95.0
    return 70.0


def pctl(vals, v):
    if not vals:
        return 50.0
    return (sum(1 for p in vals if p < v) + sum(1 for p in vals if p == v) * 0.5) / len(vals) * 100


def heat_at(hs, idx):
    """T 处市场温度 proxy（0-100）"""
    seg = hs[:idx + 1]
    if len(seg) < 300:
        return 50.0
    c = [x[1] for x in seg]
    last = c[-1]
    hi, lo = max(c[-260:]), min(c[-260:])
    idxp = (last - lo) / (hi - lo) * 100 if hi > lo else 50.0
    m60 = (last / c[-61] - 1) * 100
    mscore = max(0.0, min(100.0, (m60 + 25) / 50 * 100))
    return 0.64 * idxp + 0.36 * mscore


def main():
    cur = json.load(open(os.path.join(ROOT, "data.json"), encoding="utf-8"))
    pool = cur.get("pool") or []
    ind_of = {p["code"]: p.get("ind", "其他") for p in pool}
    print("pool=%d" % len(pool))
    days = {}
    for i, p in enumerate(pool):
        try:
            d = fetch_day(p["code"], 800)
            if len(d) >= 520:
                days[p["code"]] = d
        except Exception:
            pass
        if (i + 1) % 120 == 0:
            print("  kline %d/%d usable=%d" % (i + 1, len(pool), len(days)))
        time.sleep(0.06)
    print("usable=%d" % len(days))
    hs = fetch_day("sh000300", 800)
    last_idx = min([len(d) for d in days.values()] + [len(hs)])
    Ts = list(range(300, last_idx - 1 - HORIZON, STEP))
    print("evaluation days=%d (T %d~%d)" % (len(Ts), Ts[0], Ts[-1]))

    stat = {k: {"A": {"d": [], "hi_pos": []}, "B": {"d": [], "hi_pos": []}}
            for k in ("all", "cold", "warm", "hot")}

    for T in Ts:
        rows = []
        for c, d in days.items():
            f = fx_at(d, T)
            if f is None:
                continue
            p_t = d[T][1]
            p_fut = d[T + HORIZON][1]
            rows.append({"code": c, "ind": ind_of.get(c, "其他"),
                         "fut": (p_fut / p_t - 1) * 100, **f})
        if len(rows) < 80:
            continue
        # 行业 proxy：同行 60 日收益中位
        peer = {}
        for r in rows:
            peer.setdefault(r["ind"], []).append(r["ret60"])
        for ind in peer:
            peer[ind].sort()
        for r in rows:
            arr = peer[r["ind"]]
            r["ind_med"] = arr[len(arr) // 2]
            r["rel"] = r["ret60"] - r["ind_med"]
        ps = [r["pos_raw"] for r in rows]
        vs = [r["vol_raw"] for r in rows]
        ims = [r["ind_med"] for r in rows]
        rls = [r["rel"] for r in rows]
        for r in rows:
            posP = pctl(ps, r["pos_raw"])
            fix = fix_score(r["fix_raw"])
            volP = 100 - pctl(vs, r["vol_raw"])
            indP = 0.5 * pctl(ims, r["ind_med"]) + 0.5 * pctl(rls, r["rel"])
            r["sA"] = 0.45 * posP + 0.35 * fix + 0.20 * volP            # 无板块
            r["sB"] = 0.36 * posP + 0.28 * fix + 0.16 * volP + 0.20 * indP  # 含板块20%
        n = len(rows)
        h = heat_at(hs, T)
        bucket = "cold" if h < 34 else ("hot" if h > 66 else "warm")
        for key, lbl in (("A", "低位(无板块)"), ("B", "低位+板块20%")):
            rs = sorted(rows, key=lambda x: -x["s" + key])
            hi = rs[: n // 3]
            lo = rs[-n // 3:]
            diff = (sum(x["fut"] for x in hi) / len(hi)) - (sum(x["fut"] for x in lo) / len(lo))
            hi_pos = sum(1 for x in hi if x["pos_raw"] <= 0.10) / len(hi) * 100
            stat["all"][key]["d"].append(diff)
            stat["all"][key]["hi_pos"].append(hi_pos)
            stat[bucket][key]["d"].append(diff)
            stat[bucket][key]["hi_pos"].append(hi_pos)

    def show(st, title, bname=""):
        print("\n== %s %s ==" % (title, bname))
        for key, label in (("A", "低位(无板块)"), ("B", "低位+板块20%")):
            ds = st[key]["d"]
            if not ds:
                print("  无样本")
                continue
            avg = sum(ds) / len(ds)
            med = sorted(ds)[len(ds) // 2]
            win = sum(1 for x in ds if x > 0) / len(ds) * 100
            hp = sum(st[key]["hi_pos"]) / len(st[key]["hi_pos"])
            print("  %-14s 截面%3d 高-低 %+5.2fpp(中位%+5.2f) 胜率%4.1f%% | 高分组贴顶占比%4.1f%%"
                  % (label, len(ds), avg, med, win, hp))

    show(stat["all"], "全样本（统一低位逻辑，不分 regime）")
    for k in ("cold", "warm", "hot"):
        show(stat[k], "市场温度", "=" + k)


if __name__ == "__main__":
    main()
