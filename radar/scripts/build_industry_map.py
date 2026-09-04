# -*- coding: utf-8 -*-
"""
构建 股票code -> 申万二级行业 映射（一次性/低频，行业分类每季度调整）
用法: python build_industry_map.py
产物: data/industry_map.json   {code: "白酒Ⅱ", ...}
数据源: 腾讯 hs getBoardRankList board_code=ptXXXX (124 个申万二级板块成分)
"""
import json, time, os, urllib.request, sys

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0"
OUT = os.path.join(HERE, "data", "industry_map.json")
BOARDS = json.load(open(os.path.join(HERE, "data", "sw2_boards.json"), encoding="utf-8"))
RETRY = 3


def get_json(url):
    for i in range(RETRY):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Referer": "https://gu.qq.com/"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:
            if i == RETRY - 1:
                raise
            time.sleep(1.2 * (i + 1))


def fetch_board(code):
    """返回该板块全部成分 [code,...]（分页直到 total）"""
    out = []
    url = ("https://proxy.finance.qq.com/cgi/cgi-bin/rank/hs/getBoardRankList"
           "?board_code=%s&board_type=HY&sort_type=MarketValue&direct=down" % code)
    offset = 0
    while True:
        d = get_json(url + "&offset=%d&count=200" % offset)
        data = d.get("data") or {}
        rl = data.get("rank_list") or []
        total = data.get("total") or 0
        for x in rl:
            out.append(x.get("code", "").lower())
        offset += len(rl)
        if offset >= total or not rl:
            break
        time.sleep(0.1)
    return out


def main():
    mp, dup, missing = {}, {}, 0
    t0 = time.time()
    for i, b in enumerate(BOARDS):
        try:
            codes = fetch_board(b["code"])
        except Exception as e:
            print("[WARN] %s %s fetch fail: %s" % (b["code"], b["name"], e), file=sys.stderr)
            continue
        for c in codes:
            if c in mp:
                dup.setdefault(c, [mp[c], b["name"]])
            else:
                mp[c] = b["name"]
        if (i + 1) % 20 == 0 or i == len(BOARDS) - 1:
            print("board %d/%d, mapped=%d, %.0fs"
                  % (i + 1, len(BOARDS), len(mp), time.time() - t0))
        time.sleep(0.08)
    json.dump(mp, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("saved %s: %d stocks, %d boards" % (OUT, len(mp), len(BOARDS)))
    print("duplicated (belong to 2+ boards): %d" % len(dup))
    for c, (a, b) in list(dup.items())[:10]:
        print("  ", c, a, "<->", b)


if __name__ == "__main__":
    main()
