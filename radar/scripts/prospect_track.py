# -*- coding: utf-8 -*-
"""
prospect_track.py · 前瞻绩效追踪（无前视偏差的真实样本验证）
=============================================================================
机制：GitHub Actions 每日 scan 后，data.json 会被 commit 进 main —— 每次 commit
的 data.json 就是当天的"榜单快照档案"（含当时 Top 的 code 与当日收盘价）。

本脚本（每次 workflow 运行自动调用）：
  1) git log 找出所有含 radar/data.json 的历史快照及其日期；
  2) 对距今 ≥ 28 自然日(≈20 交易日)与 ≥ 88 自然日(≈60 交易日)且**尚未评估过**
     的旧快照：用腾讯实时接口取现价，计算当时 Top15 的平均收益 / 上涨占比，
     并对沪深300 做同区间超额（指数起点取快照后最近交易日）；
  3) 幂等追加结果到 radar/prospect.json（随站点发布，页面读取展示）。

这才是"当时名单等待未来"——而历史回测是"今日名单回看过去"（幸存者偏差）。
依赖：git 完整历史（workflow 已配 fetch-depth: 0）。

用法: python scripts/prospect_track.py
"""
import json, os, subprocess, time, urllib.request, datetime

def find_root():
    """定位 quant-radar 仓库根（git cwd）。兼容 根目录 / radar/ 两种调用位置。"""
    wd = os.getcwd()
    if os.path.exists(os.path.join(wd, "data.json")) and os.path.isdir(os.path.join(wd, "scripts")):
        return os.path.dirname(wd)          # cwd = radar/
    if os.path.exists(os.path.join(wd, "radar", "data.json")):
        return wd                            # cwd = 仓库根
    return wd

ROOT = find_root()
os.chdir(ROOT)
RADAR = os.path.join(ROOT, "radar")
HERE = os.path.join(RADAR, "scripts")
OUT = os.path.join(RADAR, "prospect.json")
TOP_N = 15
WIN_DAYS = [("20", 28), ("60", 88)]    # 持有标签 -> 自然日阈值（≈20/60 交易日）
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0",
      "Referer": "https://gu.qq.com/"}

def git(*a):
    r = subprocess.run(["git"] + list(a), capture_output=True, text=True)
    return r.stdout.strip()

def snapshots():
    """[(date,rev)] 按时间旧→新，rev 可 git show 出 radar/data.json"""
    out = []
    for line in git("log", "--format=%H|%ci", "--", "radar/data.json").splitlines():
        if "|" not in line:
            continue
        rev, ci = line.split("|", 1)
        d = ci[:10]
        try:
            snap = json.loads(git("show", rev + ":radar/data.json")) if False else None
        except Exception:
            snap = None
        out.append((d, rev))
    out.sort()
    return out

def snap_data(rev):
    try:
        t = git("show", "%s:radar/data.json" % rev)
        return json.loads(t)
    except Exception:
        return None

def qt(codes):
    """批量实时行情 {code:price}"""
    out = {}
    for i in range(0, len(codes), 40):
        chunk = codes[i:i + 40]
        u = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        try:
            req = urllib.request.Request(u, headers=UA)
            raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", "ignore")
        except Exception:
            time.sleep(0.4)
            continue
        for line in raw.strip().split(";"):
            if "=" not in line:
                continue
            code = line.split("=")[0].replace("v_", "").strip()
            v = line.split("=")[1].strip('"').split("~")
            if len(v) > 4 and v[3]:
                try:
                    out[code] = float(v[3])
                except ValueError:
                    pass
        time.sleep(0.15)
    return out

def idx_base(date):
    """沪深300：date 之后最近交易日的收盘指数 —— 用日K找"""
    u = ("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get?param=sh000300,day,,,800,qfq")
    try:
        req = urllib.request.Request(u, headers=UA)
        d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode("utf-8", "ignore"))
        arr = d.get("data", {}).get("sh000300", {})
        bars = arr.get("day") or arr.get("qfqday") or []
        for b in bars:
            if b[0] >= date:
                return float(b[2])
    except Exception:
        pass
    return None

def main():
    snaps = snapshots()
    print("[snapshots]", len(snaps), "->", snaps[0][0] if snaps else None, "..", snaps[-1][0] if snaps else None)
    if not snaps:
        print("NO HISTORY: 尚无 radar/data.json 的历史提交（部署后每个交易日自动累积）")
        return
    pros = {}
    if os.path.exists(OUT):
        try:
            pros = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            pros = {}
    pros.setdefault("started", snaps[0][0])
    pros.setdefault("evals", [])
    done = set((e["snap"], str(e["hold"])) for e in pros["evals"])
    today = datetime.date.today()
    # 收集候选：取每档最近一个到期未评估快照（避免一次评估过多历史批次）
    new_evs = []
    for d, rev in snaps:
        age = (today - datetime.date.fromisoformat(d)).days
        if age < 0:
            continue
        for hold, thr in WIN_DAYS:
            if age >= thr and (d, hold) not in done:
                snap = snap_data(rev)
                if not snap or not snap.get("top"):
                    continue
                px = snap["top"][:TOP_N]
                codes = [x["code"] if x["code"].startswith(("sh", "sz", "bj")) else ("sh" + x["code"] if x["code"][0] in "69" else "sz" + x["code"]) for x in px]
                cur = qt(codes)
                rets, ok = [], 0
                for i, x in enumerate(px):
                    nowp = cur.get(codes[i])
                    if not nowp or not x.get("price"):
                        continue
                    r = (nowp / x["price"] - 1) * 100
                    rets.append(r)
                if not rets:
                    continue
                ex = None
                ib = idx_base(d)
                if ib:
                    iq = qt(["sh000300"]).get("sh000300")
                    if iq:
                        ex = sum(rets) / len(rets) - (iq / ib - 1) * 100
                ev = {"snap": d, "ev": today.isoformat(), "hold": hold, "n": len(rets),
                      "avg": round(sum(rets) / len(rets), 2),
                      "win": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
                      "ex": round(ex, 2) if ex is not None else None}
                new_evs.append(ev)
                done.add((d, hold))
                print("  eval", ev)
    pros["evals"].extend(new_evs)
    pros["lastRun"] = today.isoformat()
    json.dump(pros, open(OUT, "w", encoding="utf-8"), ensure_ascii=False)
    print("saved", OUT, "| started", pros["started"], "| evals total", len(pros["evals"]))

if __name__ == "__main__":
    main()
