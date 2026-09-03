# -*- coding: utf-8 -*-
"""
量化行业筛选 · 数据更新器
========================
从腾讯财经公开接口抓取申万二级行业板块行情，生成 data.json（含走势、PE/PB、
点位分位、龙头股与基金行情），供 index.html 前端加载。

特性：
  - 纯 Python 标准库，无第三方依赖，本地 / 服务器 / GitHub Actions 均可运行
  - 幂等可重复：每次全量重建 data.json，失败自动重试
  - 输出 data.json + fallback-data.js（前端加载失败时的兜底数据）

用法：
  python update.py            # 在脚本所在目录运行，产物写入上级目录
"""
import json
import time
import urllib.request
import urllib.error
import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CFG = os.path.join(ROOT, "industries.json")
OUT_JSON = os.path.join(ROOT, "data.json")
OUT_FALLBACK = os.path.join(ROOT, "fallback-data.js")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
DAY_BARS = 1300     # 约5年日线
MON_BARS = 320      # 月线全历史
RETRIES = 4
SLEEP = 0.12


def http_get(url, timeout=15):
    last_err = None
    for i in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Referer": "https://gu.qq.com/",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa
            last_err = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError("GET failed after %d retries: %s (%s)" % (RETRIES, url, last_err))


def fetch_kline(code, period, count):
    """拉取K线。period: day / month。返回 [[yyyymmdd(int), close(float)], ...] 升序。"""
    url = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
           "?param=%s,%s,,,%d,qfq" % (code, period, count))
    raw = http_get(url).decode("utf-8", "ignore")
    d = json.loads(raw)
    node = d.get("data", {}).get(code, {})
    arr = node.get(period) or node.get("qfq" + period) or []
    out = []
    for row in arr:
        try:
            ymd = int(row[0].replace("-", ""))
            out.append([ymd, round(float(row[2]), 4)])  # row[2]=收盘
        except Exception:  # noqa
            continue
    out.sort(key=lambda x: x[0])
    return out


def fetch_quotes(codes):
    """批量行情。返回 {code: {name, price, chg, pe, pb}}。"""
    res = {}
    for i in range(0, len(codes), 28):
        chunk = codes[i:i + 28]
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        raw = http_get(url).decode("gbk", "ignore")
        for line in raw.strip().split(";"):
            line = line.strip()
            if "=" not in line:
                continue
            head, body = line.split("=", 1)
            code = head.replace("v_", "").strip()
            v = body.strip().strip('"').split("~")
            if len(v) < 40 or not v[3]:
                continue
            try:
                price = float(v[3])
            except Exception:  # noqa
                continue

            def _f(idx):
                try:
                    x = float(v[idx])
                    return round(x, 3)
                except Exception:  # noqa
                    return None

            res[code] = {
                "name": (v[1].replace(" ", "") if len(v) > 1 and not v[1].isdigit() else code),
                "price": round(price, 3),
                "chg": _f(32),
                "pe": _f(39),
                "pb": _f(46),
            }
        time.sleep(SLEEP)
    return res


def percentile(day_arr, years):
    """当前点位在最近 years 年日线收盘中的分位（0~100）。数据不足时返回 None。"""
    if not day_arr:
        return None
    last_d, cur = day_arr[-1]
    cutoff = (last_d // 10000 - years) * 10000 + (last_d % 10000)
    vals = [c for d, c in day_arr if d >= cutoff]
    if len(vals) < 60:  # 样本过少视为不可用
        return None
    n = sum(1 for c in vals if c <= cur)
    return round(n * 100.0 / len(vals), 1)


def chg_over(day_arr, days):
    """近 N 个交易日涨跌幅（%）。"""
    if len(day_arr) < 2:
        return None
    seg = day_arr[-days:] if len(day_arr) >= days else day_arr
    if seg[0][1] == 0:
        return None
    return round((seg[-1][1] / seg[0][1] - 1) * 100, 2)


def main():
    t0 = time.time()
    with open(CFG, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 1) 板块K线（含大盘指数）
    all_codes = []
    for g in cfg["groups"]:
        for s in g["subs"]:
            all_codes.append(s["code"])
    idx_codes = [m["code"] for m in cfg["market"]["indices"]]
    kline = {}
    total = len(all_codes) + len(idx_codes)
    done = 0
    for code in all_codes + idx_codes:
        try:
            day = fetch_kline(code, "day", DAY_BARS)
            mon = fetch_kline(code, "month", MON_BARS)
            kline[code] = {"day": day, "mon": mon}
        except Exception as e:  # noqa
            print("[WARN] kline %s failed: %s" % (code, e), file=sys.stderr)
            kline[code] = {"day": [], "mon": []}
        done += 1
        if done % 10 == 0 or done == total:
            print("kline %d/%d" % (done, total))
        time.sleep(SLEEP)

    # 2) 行情：板块 + 龙头股 + 基金 + 大盘指数
    leader_codes, fund_codes = [], []
    for g in cfg["groups"]:
        for s in g["subs"]:
            for l in s.get("leaders", []):
                leader_codes.append(l["code"])
        for fnd in g.get("funds", []):
            fund_codes.append(fnd["code"])
    quotes = fetch_quotes(all_codes + idx_codes + leader_codes + fund_codes)

    # 3) 组装
    market = []
    for m in cfg["market"]["indices"]:
        q = quotes.get(m["code"], {})
        k = kline.get(m["code"], {"day": [], "mon": []})
        market.append({
            "name": m["name"], "code": m["code"],
            "price": q.get("price"), "chg": q.get("chg"),
            "pe": q.get("pe"), "pb": q.get("pb"),
            "day": k["day"], "mon": k["mon"],
        })

    groups = []
    for g in cfg["groups"]:
        subs = []
        for s in g["subs"]:
            k = kline.get(s["code"], {"day": [], "mon": []})
            q = quotes.get(s["code"], {})
            leaders = []
            for l in s.get("leaders", []):
                lq = quotes.get(l["code"], {})
                leaders.append({
                    "code": l["code"],
                    "name": lq.get("name") or l["name"],
                    "price": lq.get("price"),
                    "chg": lq.get("chg"),
                    "pe": lq.get("pe"),
                })
            subs.append({
                "name": s["name"], "code": s["code"],
                "pe": q.get("pe"), "pb": q.get("pb"),
                "price": q.get("price"), "chg": q.get("chg"),
                "p3y": percentile(k["day"], 3),
                "p5y": percentile(k["day"], 5),
                "chg1y": chg_over(k["day"], 244),
                "day": k["day"], "mon": k["mon"],
                "leaders": leaders,
            })
        funds = []
        for fnd in g.get("funds", []):
            fq = quotes.get(fnd["code"], {})
            funds.append({
                "code": fnd["code"],
                "name": fnd.get("name") or fq.get("name") or fnd["code"],
                "type": fnd["type"],
                "price": fq.get("price"),
                "chg": fq.get("chg"),
            })
        groups.append({"name": g["name"], "subs": subs, "funds": funds})

    data = {
        "generatedAt": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "腾讯财经公开行情接口（申万二级行业分类）",
        "dayBars": DAY_BARS,
        "market": market,
        "groups": groups,
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_FALLBACK, "w", encoding="utf-8") as f:
        f.write("window.__FALLBACK_DATA__=")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";")

    n_sub = sum(len(g["subs"]) for g in groups)
    size = os.path.getsize(OUT_JSON) / 1024
    print("OK: %d groups / %d subs, data.json %.0f KB, %.1fs"
          % (len(groups), n_sub, size, time.time() - t0))


if __name__ == "__main__":
    main()
