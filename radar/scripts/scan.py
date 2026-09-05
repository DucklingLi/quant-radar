# -*- coding: utf-8 -*-
"""
A股多因子雷达 · 每日全市场扫描器（v7：行业联动——每票输出行业一年位置 + 本池行业画像 + 行业共振修正）
==============================================================================
全流程：
  全A快照(4599) → hs300日K→算市场温度 heat
    → 粗筛(ST/退市/低成交/亏损剔除) → 个股320根日K(pos/fix/vol/ret60)
    → 行业归属(124申万二级) + 行业整体法PE估值中性
    → 板块330根日K画像(一年位置 pos / 20日·60日动量) + 快照因子(换手/主力资金/成交额)
    → 温度调制打分 + 行业共振修正(可选) → Top50 输出(含 indPos/inds 行业联动字段)

打分公式见 MODEL.md：
  Score = Σ_i w_i(heat)·S_i + Adj(ind) ，i ∈ {位置,估值,启动,板块,换手,资金,波动,流动}
  S_i 均为候选池内 0~100 分（低波动/低估值取反分位；启动为分段函数）
  板块权重 w_ind = 0.14·I(heat<34)；非冷区板块不参与打分
  Adj(ind)：行业一年位置共振修正（RES_IND_LOW/RES_IND_HIGH，由回测校准）

用法: python scan.py
产物: data.json + fallback-data.js
"""
import json
import os
import time
import math
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_JSON = os.path.join(ROOT, "data.json")
OUT_JS = os.path.join(ROOT, "fallback-data.js")
MAP = os.path.join(HERE, "data", "industry_map.json")
BOARDS = os.path.join(HERE, "data", "sw2_boards.json")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0"
CANDIDATES = 420
TOPN = 50
RETRY = 4
SLEEP = 0.1

# 基准权重（板块冷区激活时按比例收缩；数值供 MODEL.md 引用）
W_BASE = {"pos": 0.20, "value": 0.12, "fix": 0.18,
          "turn": 0.09, "fund": 0.13, "vol": 0.08, "liquid": 0.20}
W_IND_COLD = 0.14          # 冷区板块激活权重

# 行业位置共振修正（v7，数值由 backtest_indpos.py 校准：
#   行业低位+低位启动 +15.08%/80.6% vs 行业高位 +3.11%/55.2%，差 +11.97pp(54截面/4587样本)）
RES_IND_LOW = 5.0           # 行业处一年低位(<35%) + 个股近期低位金叉(<=7日)：加分
RES_IND_HIGH = -4.0         # 行业处一年高位(>65%)：减分（高位行业内个股启动胜率显著偏低）
# 质量修正（v9：backtest_quality.py 校准——ROE 单调 亏损+0.48%/48% → >15% +3.62%/58.6%；
# 深低位样本内 优质(ROE>10且营收不降) +6.61%/62.8% vs 弱 +1.25%/54%；与行业共振同一加法层）
QUAL_GOOD = 3.0             # ROE>15 或 (ROE>10 且营收增速>=0)：质量背书
QUAL_BAD = -2.0             # 微利(0<ROE<5) 或 营收增速<-10：弱质折价
QUAL_REPORT = "auto"        # auto=按日期选最近完整披露报告期


def get(url, timeout=15, enc="utf-8"):
    last = None
    for i in range(RETRY):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://gu.qq.com/"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(enc, "ignore")
        except Exception as e:
            last = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError("GET fail: %s (%s)" % (url, last))


def get_json(url):
    return json.loads(get(url))


def tof(v):
    try:
        return float(v)
    except Exception:
        return None


def fetch_day(code, n=320):
    """日K（单次上限800）升序 [(ymd, close), ...]"""
    u = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
         "?param=%s,day,,,%d,qfq" % (code, min(n, 800)))
    d = get_json(u)
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


# ---------------- 因子 ----------------
def ema(vals, n):
    k = 2.0 / (n + 1)
    out, e = [], None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def sma(vals, n):
    return [sum(vals[max(0, i - n + 1):i + 1]) / min(n, i + 1) for i in range(len(vals))]


def last_cross_info(a, b, hi_low_window, closes, limit=120):
    """最近一次 a 上穿 b（金叉）发生在多少天前；金叉日价格在当年区间位置。
    返回 (days_ago, pos_at_cross) 或 None（limit 天内无金叉）"""
    n = len(a)
    for i in range(n - 1, max(n - limit, 1), -1):
        if a[i] > b[i] and a[i - 1] <= b[i - 1]:
            days = n - 1 - i
            c = closes[i]
            hi, lo = hi_low_window
            posx = (c - lo) / (hi - lo) if hi > lo else 0.5
            return days, posx, c
    return None


def calc_factors(day):
    if len(day) < 245:
        return None
    closes = [c for _, c in day]
    last = closes[-1]
    win260 = closes[-260:]
    hi260, lo260 = max(win260), min(win260)
    pos = (last - lo260) / (hi260 - lo260) if hi260 > lo260 else 0.5
    mom20 = (last / closes[-21] - 1) * 100
    mom60 = (last / closes[-61] - 1) * 100
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - 20, len(closes))]
    mu = sum(rets) / 20
    varr = sum((r - mu) ** 2 for r in rets) / 19
    vol = math.sqrt(varr) * math.sqrt(252) * 100 if varr >= 0 else 30.0
    # ---- 启动确认：均线金叉(MA5×MA20) 与 MACD金叉(DIF×DEA)，取最近者 ----
    ma5 = sma(closes, 5)
    ma20 = sma(closes, 20)
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    dif = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    dea = ema(dif, 9)
    c1 = last_cross_info(ma5, ma20, (hi260, lo260), closes)
    c2 = last_cross_info(dif, dea, (hi260, lo260), closes)
    candidates = [c for c in (c1, c2) if c and c[1] < 0.70]   # 要求金叉发生在一年内相对低位区
    best = min(candidates, key=lambda c: c[0]) if candidates else None
    launch_d = best[0] if best else None                       # 距最近一次低位金叉的天数
    launch_px = best[2] if best else None                      # 金叉启动日收盘价（成本密集区）
    if launch_d is not None and launch_d > 120:
        launch_d, launch_px = None, None
    return {"pos_raw": max(0.0, min(1.0, 1.0 - pos)), "fix_raw": mom20,
            "ret60": mom60, "vol_raw": vol, "launch_d": launch_d,
            "launch_px": launch_px, "ma20v": sma(closes, 20)[-1],
            "hi260": hi260, "lo260": lo260}


def launch_score(r20, d):
    """启动确认分段（2026-09-04 由修复因子升级；用户提出：已启动数日可能临下跌，
    偏好"低位刚金叉首启"）。既要求金叉时点新鲜（距低位金叉天数 d），
    又保留不追过热/不接飞刀的约束（r20 为近20日涨幅%）：
      d<=3   首启（甜点）：刚金叉 1~3 日
      d<=7   早启
      d<=15  启动确认期
      d<=30  早期偏晚
      无近期金叉但温和企稳 → 基础分；过热/崩跌一律低分
    """
    if r20 is not None and (r20 > 25 or r20 < -18):
        return 5.0                       # 过热或崩跌未止，仍不碰
    if d is not None and d <= 3:
        return 98.0
    if d is not None and d <= 7:
        return 86.0
    if d is not None and d <= 15:
        return 64.0
    if d is not None and d <= 30:
        return 48.0
    if r20 is not None and -5 <= r20 <= 12:
        return 42.0                      # 无近期金叉，仅企稳
    return 18.0


def turnover_score(hsl):
    """换手率关注度分段：0.5%~6% 舒适区高分；过低无人问津/过高投机过热低分"""
    if hsl is None:
        return 50.0
    if hsl <= 0.2 or hsl > 12:
        return 10.0
    if hsl < 0.5:
        return 40.0
    if hsl <= 6:
        return 90.0
    return 55.0


def ind_res_adj(it):
    """行业位置共振修正（v7）。在综合分上做小幅度加减：
      行业处一年低位区 + 个股近期低位金叉 → 行业底部共振确认（左侧更安全）；
      行业处一年高位区 → 谨防行业见顶回落拖累（无论个股分位）。
    数值取 backtest_indpos 的结论（RES_IND_LOW / RES_IND_HIGH），0 即不启用。"""
    ip = it["f"].get("ind_pos")
    if ip is None:
        return 0.0
    if ip <= 0.35 and it["f"].get("launch_d") is not None and it["f"]["launch_d"] <= 7:
        return RES_IND_LOW
    if ip >= 0.65:
        return RES_IND_HIGH
    return 0.0


# ---------------- 质量因子（v9：ROE/营收增速，backtest_quality 校准） ----------------
def latest_report(today):
    """最近一个已过法定披露截止的完整报告期（YYYY-MM-DD）。
    披露截止：年报与一季报 4/30、中报 8/31、三季报 10/31。"""
    import datetime as _dt
    y = today.year
    out = None
    for rep, dl in ((_dt.date(y - 1, 12, 31), _dt.date(y, 4, 30)),
                    (_dt.date(y, 3, 31), _dt.date(y, 4, 30)),
                    (_dt.date(y, 6, 30), _dt.date(y, 8, 31)),
                    (_dt.date(y, 9, 30), _dt.date(y, 10, 31))):
        if today >= dl:
            out = rep
    return (out or _dt.date(y - 1, 12, 31)).isoformat()


def fetch_quality(report):
    """拉某报告期全市场业绩报表（ROE/营收增速等），落盘 raw/quality_<rep>.json 供复用。"""
    import urllib.parse
    cache = os.path.join(ROOT, "scripts", "raw", "quality_%s.json" % report)
    if os.path.exists(cache):
        return json.load(open(cache, encoding="utf-8"))
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    out, page = {}, 1
    while page <= 14:
        flt = urllib.parse.quote("(REPORTDATE='%s')" % report)
        u = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LICO_FN_CPD"
             "&columns=SECURITY_CODE,WEIGHTAVG_ROE,YSTZ,SJLTZ,XSMLL"
             "&pageNumber=%d&pageSize=500&sortColumns=SECURITY_CODE&sortTypes=1&filter=%s" % (page, flt))
        try:
            raw = get(u)
            d = json.loads(raw)
            rows = ((d.get("result") or {}).get("data")) or []
            for r in rows:
                out[r["SECURITY_CODE"]] = {"roe": r.get("WEIGHTAVG_ROE"), "ystz": r.get("YSTZ")}
            if len(rows) < 500:
                break
        except Exception:
            break
        page += 1
        time.sleep(0.3)
    json.dump(out, open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return out


def qual_score(it, qmap):
    """质量修正与明细。返回 (adj, q) —— q=None 表示无财务覆盖（不给修正）。"""
    code = it["x"].get("code", "")
    q = qmap.get(code[2:]) if len(code) > 2 else None
    if not q or q.get("roe") is None:
        return 0.0, q
    roe, ystz = q["roe"], q.get("ystz")
    good = roe > 15 or (roe > 10 and (ystz is None or ystz >= 0))
    bad = (0 < roe < 5) or (ystz is not None and ystz < -10)
    adj = QUAL_GOOD if good else (QUAL_BAD if bad else 0.0)
    return adj, q


def pct_score(vals):
    """0-100 分位分；缺失->50"""
    pts = [v for v in vals if v is not None]
    if not pts:
        return [50.0] * len(vals)

    def one(v):
        if v is None:
            return 50.0
        less = sum(1 for p in pts if p < v)
        eq = sum(1 for p in pts if p == v)
        return round((less + eq * 0.5) / len(pts) * 100, 1)
    return [one(v) for v in vals]


def invert(vals):
    return [round(100 - v, 1) if v is not None else 50.0 for v in vals]


# ---------------- 市场温度 ----------------
def market_heat(uni, hs_day):
    closes = [c for _, c in hs_day]
    last = closes[-1]
    hi, lo = max(closes[-260:]), min(closes[-260:])
    idxpos = (last - lo) / (hi - lo) * 100 if hi > lo else 50.0
    m60 = (last / closes[-61] - 1) * 100
    up60 = sum(1 for x in uni if (tof(x.get("zdf_d60")) or -999) > 0)
    breadth = up60 / max(1, len(uni)) * 100
    mom_score = max(0.0, min(100.0, (m60 + 25) / 50 * 100))
    heat = round(0.45 * idxpos + 0.30 * breadth + 0.25 * mom_score, 1)
    return {"heat": heat, "idxpos": round(idxpos, 1), "breadth": round(breadth, 1),
            "hs60": round(m60, 1)}


def heat_label(h):
    if h < 34:
        return "cold", "市场偏冷：冰点区低位企稳信号历史区分度最高（回测+4.5pp/胜率100%），板块轮动因子在本区激活；适合左侧分批、仓位从严"
    if h > 66:
        return "hot", "市场偏热：历史回测提示追涨与板块动量在此区易被反噬，本榜板块因子不激活、信号档位从严；建议控制仓位不追高"
    return "warm", "市场温度适中：常规节奏，板块动量因子保持观察（不激活），参考信号分批操作"


def suggest_pos(heat):
    return round(20 + 45 * heat / 100)


# ---------------- 主流程 ----------------
def main():
    t0 = time.time()
    print("[1/8] fetch universe snapshot ...")
    url = ("https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
           "?board_code=aStock&sort_type=MarketValue&direct=down")
    uni, offset = [], 0
    while True:
        d = get_json(url + "&offset=%d&count=200" % offset)
        data = d.get("data") or {}
        rl = data.get("rank_list") or []
        total = data.get("total") or 0
        uni.extend(rl)
        offset += len(rl)
        if offset >= total or not rl:
            break
        time.sleep(SLEEP)
    print("      universe=%d  %.0fs" % (len(uni), time.time() - t0))

    # ---- 市场温度（提前，供打分权重） ----
    hs_day = []
    try:
        hs_day = fetch_day("sh000300")
    except Exception:
        pass
    temp = market_heat(uni, hs_day) if len(hs_day) >= 260 else {"heat": 50.0, "idxpos": 50.0,
                                                                "breadth": 50.0, "hs60": 0.0}
    hl, htip = heat_label(temp["heat"])
    cold = hl == "cold"
    print("      heat=%s(%s)" % (temp["heat"], hl))

    # ---- 粗筛候选 ----
    cand = []
    for x in uni:
        name = x.get("name", "")
        code = (x.get("code") or "").lower()
        if "st" in name.lower() or "退" in name or not code.startswith(("sh6", "sz0", "sz3")):
            continue
        if tof(x.get("pe_ttm")) is None or tof(x.get("pe_ttm")) <= 0:
            continue
        if tof(x.get("turnover")) is None or tof(x.get("turnover")) < 3000:
            continue
        cand.append(x)
        if len(cand) >= CANDIDATES + 40:
            break
    print("      candidates=%d" % len(cand))

    # ---- 个股日K + 基础因子 ----
    print("[2/8] stock klines ...")
    pool = []
    for i, x in enumerate(cand):
        code = x["code"].lower()
        try:
            f = calc_factors(fetch_day(code))
            if f is None:
                continue
            if f["fix_raw"] < -18:
                continue
            pool.append({"x": x, "f": f})
        except Exception as e:
            print("      [warn] %s %s" % (code, e))
        if (i + 1) % 100 == 0:
            print("      kline %d/%d  %.0fs" % (i + 1, len(cand), time.time() - t0))
        time.sleep(SLEEP)
    print("      pool=%d" % len(pool))

    # ---- 行业归属 / 估值中性化 ----
    # 估值锚：候选池内同行业成分股的正 PE 中位数（板块整体法 PE 受亏损成分污染会虚高，
    # 用同行中位更稳健、且与打分/回测自洽）；同行样本<2 时回退全池中位。
    print("[3/8] industry map & neutralization ...")
    ind_map, name2code = {}, {}
    try:
        ind_map = json.load(open(MAP, encoding="utf-8"))
        name2code = {b["name"]: b["code"] for b in json.load(open(BOARDS, encoding="utf-8"))}
    except Exception:
        print("      [warn] map missing")
    need = set()
    for it in pool:
        c = it["x"]["code"].lower()
        ind = ind_map.get(c)
        bcode = name2code.get(ind) if ind else None
        it["ind"] = ind or "其他"
        it["board_code"] = bcode
        if bcode:
            need.add(bcode)
    ind_pes = {}
    for it in pool:
        pe = tof(it["x"].get("pe_ttm"))
        if pe and pe > 0:
            ind_pes.setdefault(it["ind"], []).append(pe)


    def clean_median(pes):
        """行业估值中枢：剔除微利/困境伪高PE(>80，均值回归不适用)后的下中位。
        样本>=2 才返回，否则 None（无参照→按'合理'处理，走短线卖出逻辑，避免误估）。"""
        f = sorted(p for p in pes if p and 0 < p <= 80)
        if len(f) >= 2:
            return f[(len(f) - 1) // 2]
        return None

    med_pe = clean_median([p for p in (tof(it["x"].get("pe_ttm")) for it in pool) if p])
    for it in pool:
        pe = tof(it["x"].get("pe_ttm"))
        base = clean_median(ind_pes.get(it["ind"]) or [])
        it["bpe"] = base
        it["f"]["val_raw"] = math.log(pe / base) if pe and pe > 0 and base else None
        it["price"] = tof(it["x"].get("zxj")) or 0
        # 快照因子
        it["liquid_raw"] = tof(it["x"].get("turnover"))
        it["hsl_raw"] = tof(it["x"].get("hsl"))            # 换手率 %
        ltsz = tof(it["x"].get("ltsz"))                    # 流通市值(亿)
        f5in = tof(it["x"].get("zllr_d5"))                 # 5日主力流入(万)
        f5out = tof(it["x"].get("zllc_d5"))                # 5日主力流出(万)
        if f5in is not None and f5out is not None and ltsz:
            it["fund_raw"] = (f5in - f5out) / (ltsz * 1e4) * 100   # 5日主力净流入占流通市值 %
        else:
            it["fund_raw"] = None

    # ---- 板块画像（一年位置 + 20/60日动量；动量仅冷区参与打分，画像始终计算供展示/联动） ----
    nl = sorted(need)
    print("[4/8] board profile (%d boards) ..." % len(nl))
    board_info = {}
    for b in nl:
        try:
            d = fetch_day(b, 330)
            if len(d) >= 270:
                closes = [c for _, c in d]
                win = closes[-260:]
                hi, lo = max(win), min(win)
                last = closes[-1]
                pos = (last - lo) / (hi - lo) if hi > lo else 0.5   # 行业一年位置 0~1（0=接近一年低点）
                board_info[b] = {
                    "m60": (last / closes[-61] - 1) * 100,
                    "m20": (last / closes[-21] - 1) * 100,
                    "pos": pos}
        except Exception:
            pass
        time.sleep(SLEEP)
    for it in pool:
        bi = board_info.get(it["board_code"])
        if bi:
            it["f"]["ind_raw"] = bi["m60"]
            it["f"]["rel_raw"] = it["f"]["ret60"] - bi["m60"]
            it["f"]["ind_pos"] = bi["pos"]          # 行业一年位置（个股所属行业）
            it["f"]["ind20"] = bi["m20"]            # 行业近20日动量
        else:
            it["f"]["ind_raw"] = 0.0
            it["f"]["rel_raw"] = 0.0
            it["f"]["ind_pos"] = None
            it["f"]["ind20"] = None

    # ---- 质量层（v9）：拉最近完整报告期财务 → 质量修正 ----
    import datetime as _dt
    qrep = QUAL_REPORT if QUAL_REPORT != "auto" else latest_report(_dt.date.today())
    qmap = fetch_quality(qrep)
    for it in pool:
        qadj, q = qual_score(it, qmap)
        it["qadj"] = round(qadj, 1)
        it["q"] = {"roe": (round(q["roe"], 2) if q and q["roe"] is not None else None),
                   "ystz": (round(q["ystz"], 1) if q and q["ystz"] is not None else None),
                   "adj": it["qadj"]} if q else None
    # ---- 打分（权重随温度） ----
    print("[5/8] scoring (cold=%s) ..." % cold)
    n = len(pool)
    sp = pct_score([it["f"]["pos_raw"] for it in pool])
    sv = invert(pct_score([it["f"]["val_raw"] for it in pool]))
    sl = pct_score([it["liquid_raw"] for it in pool])
    svo = invert(pct_score([it["f"]["vol_raw"] for it in pool]))
    sturn = [turnover_score(it["hsl_raw"]) for it in pool]
    sfund = pct_score([it["fund_raw"] for it in pool])
    sind = pct_score([it["f"]["ind_raw"] for it in pool])
    srel = pct_score([it["f"]["rel_raw"] for it in pool])
    # 有效权重：冷区激活板块 W_IND_COLD 并把基准权重等比收缩；非冷区板块权重=0
    if cold:
        shrink = 1.0 - W_IND_COLD
        w = {k: round(v * shrink, 4) for k, v in W_BASE.items()}
        w["ind"] = W_IND_COLD
    else:
        w = dict(W_BASE)
        w["ind"] = 0.0
    for i, it in enumerate(pool):
        it["score_p"] = sp[i]
        it["score_v"] = sv[i]
        it["score_l"] = sl[i]
        it["score_o"] = svo[i]
        it["score_f"] = launch_score(it["f"]["fix_raw"], it["f"]["launch_d"])
        it["score_t"] = sturn[i]
        it["score_g"] = sfund[i]
        it["score_ind"] = round(0.5 * sind[i] + 0.5 * srel[i], 1)
        base = (w["pos"] * sp[i] + w["value"] * sv[i] + w["fix"] * it["score_f"]
                + w["ind"] * it["score_ind"] + w["turn"] * sturn[i]
                + w["fund"] * sfund[i] + w["vol"] * svo[i] + w["liquid"] * sl[i])
        it["adj"] = round(ind_res_adj(it), 1)
        it["score"] = round(base + it["adj"] + it.get("qadj", 0.0), 1)  # 综合分 = 八因子 + 行业共振 + 质量修正
    pool.sort(key=lambda a: -a["score"])

    # ---- 输出 ----
    print("[6/8] write output ...")
    items, pool_codes = [], []
    for it in pool:
        x = it["x"]
        nm = x.get("name", "").replace(" ", "")
        pool_codes.append({"code": x["code"].lower(), "name": nm, "ind": it["ind"],
                           "score": it["score"]})
    for it in pool[:TOPN]:
        x = it["x"]
        nm = x.get("name", "").replace(" ", "")
        pe = tof(x.get("pe_ttm"))
        zdf = tof(x.get("zdf"))
        pos_pct = round((1.0 - it["f"]["pos_raw"]) * 100)
        sc = it["score"]
        if hl == "warm":
            tag = "重点关注" if sc >= 75 else "关注" if sc >= 60 else "观察"
        else:
            tag = "关注" if sc >= 78 else "观察"
        # ============ 卖出参考（价值回归 + 短线离场纪律） ============
        price = it["price"]
        bpe = it.get("bpe")
        hi260 = it["f"]["hi260"] or price
        ma20v = it["f"]["ma20v"]
        launch_px = it["f"]["launch_px"]
        sell = {"est": "合理", "peR": None, "valC": None, "zoneL": None,
                "zoneH": None, "stop": None, "launchPx": launch_px}
        if pe and bpe and bpe > 0 and price:
            peR = pe / bpe
            sell["peR"] = round(peR, 2)
            # 估值状态：peR<0.60 = 极低（多为周期底部/困境，PE均值回归不适用，走短线）
            # 0.60~0.85 低估修复中（给估值回归带）；<=1.15 合理；>1.15 偏高
            sell["est"] = ("极低·周期特征" if peR < 0.60 else
                           "低估修复中" if peR < 0.85 else
                           "合理" if peR <= 1.15 else "偏高")
            val_c = price / peR if peR > 0 else None          # 行业中位PE回归理论价
            sell["valC"] = round(val_c, 2) if val_c else None
            # 只有温和低估（0.60~0.85）才启用估值回归兑现带；修复带受一年高点阻力钳制
            if (0.60 <= peR < 0.85 and val_c and val_c > price * 1.15 and hi260 > price):
                fix_up = val_c - price
                zl = price + fix_up * 0.55
                zh = min(price + fix_up * 1.05, hi260)
                sell["zoneL"] = round(zl, 2)
                sell["zoneH"] = round(max(zh, zl), 2)
            else:
                # 合理/偏高/极低/无参照：短线分批止盈带 +8%~+30%，一年高点前兑现
                zh = min(price * 1.30, hi260)
                sell["zoneL"] = round(price * 1.08, 2)
                sell["zoneH"] = round(max(zh, price * 1.08), 2)
        # 短线离场线：跌破最近低位金叉启动平台×0.97，或跌破 MA20（取两者较低者保护本金）
        if launch_px and price:
            stop = min(launch_px, ma20v if ma20v else 1e9) * 0.97
        elif ma20v:
            stop = ma20v * 0.97
        else:
            stop = None
        sell["stop"] = round(stop, 2) if stop else None
        items.append({
            "rank": len(items) + 1, "code": x["code"].lower(), "name": nm,
            "ind": it["ind"], "price": price, "zdf": zdf,
            "pe": pe, "liquid": it["liquid_raw"], "pos": pos_pct,
            "hsl": it["hsl_raw"], "fund": it["fund_raw"],
            "launch": it["f"]["launch_d"],
            "ind60": round(it["f"]["ind_raw"], 1),
            "indPos": round(it["f"]["ind_pos"] * 100, 1) if it["f"]["ind_pos"] is not None else None,
            "ind20": round(it["f"]["ind20"], 1) if it["f"]["ind20"] is not None else None,
            "score": sc, "adj": it["adj"], "qadj": it.get("qadj", 0.0),
            "q": it.get("q"),
            "f": {"pos": it["score_p"], "value": it["score_v"], "fix": it["score_f"],
                  "ind": it["score_ind"], "turn": it["score_t"], "fund": it["score_g"],
                  "vol": it["score_o"], "liquid": it["score_l"]},
            "sell": sell,
            "tag": tag,
        })
    # ---- 本池行业画像（供页面"行业↔个股联动"） ----
    inds = {}
    for it in pool:
        name = it["ind"]
        if name in ("其他", "?"):
            continue
        d = inds.setdefault(name, {"name": name, "n": 0, "pos": 0.0, "m60": 0.0,
                                   "m20": 0.0, "nLaunch": 0, "scores": []})
        d["n"] += 1
        p = it["f"].get("ind_pos")
        if p is not None:
            d["pos"] += p
        m = it["f"].get("ind_raw")
        if m is not None:
            d["m60"] += m
        ld = it["f"].get("launch_d")
        if ld is not None and ld <= 7:
            d["nLaunch"] += 1
        d["scores"].append(it["score"])
    ind_list = []
    for name, d in inds.items():
        if d["n"] < 2:
            continue
        ind_list.append({
            "name": name,
            "n": d["n"],
            "pos": round(d["pos"] / d["n"] * 100, 1),        # 行业一年位置 %（越低=行业越近一年低点）
            "m60": round(d["m60"] / d["n"], 1),
            "nLaunch": d["nLaunch"],
            "avgScore": round(sum(d["scores"]) / len(d["scores"]), 1),
        })
    ind_list.sort(key=lambda a: a["pos"])
    data = {
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": "腾讯财经公开行情接口（A股全市场快照 + 日K + 申万二级板块）",
        "candidates": len(pool), "universe": len(uni),
        "heat": temp["heat"], "heatLabel": hl, "heatTip": htip,
        "suggestPos": suggest_pos(temp["heat"]),
        "breadth": temp["breadth"], "idxpos": temp["idxpos"], "hs60": temp["hs60"],
        "indActive": cold, "weights": w, "weightsBase": W_BASE,
        "indRes": {"low": RES_IND_LOW, "high": RES_IND_HIGH},
        "qualRep": qrep, "qualRes": {"good": QUAL_GOOD, "bad": QUAL_BAD},
        "inds": ind_list,
        "top": items, "pool": pool_codes,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    with open(OUT_JS, "w", encoding="utf-8") as f:
        f.write("window.__FALLBACK_DATA__=")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write(";")
    print("OK: universe=%d pool=%d heat=%s(%s) indActive=%s  %.0fs" %
          (len(uni), len(pool), temp["heat"], hl, cold, time.time() - t0))
    for it in items[:10]:
        ld = it["launch"]
        print("   %2d %-8s %-6s score=%5.1f pos=%3d%% 距低位金叉=%s tag=%s" %
              (it["rank"], it["name"], it["ind"], it["score"], it["pos"],
               ("%d日" % ld) if ld is not None else "无", it["tag"]))


if __name__ == "__main__":
    main()
