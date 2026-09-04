# quant-radar · 量化研究工具台

一个 GitHub Pages 站点，收纳两只每日自动更新的 A 股量化研究工具。

| 子路径 | 系统 | 说明 |
|---|---|---|
| `/`（首页） | 导航入口 | 显示两个系统的数据更新时间与摘要 |
| `/radar/` | **多因子雷达 · 每日交易闭环** | 全市场八因子扫描 → Top50 执行卡（买点/卖出区间/止损）；`MODEL.md` 为完整数学公式 |
| `/industry/` | **行业低位雷达 · 量化行业筛选** | 申万二级 19 大类 / 57 细分，指数走势 / 估值 / 低位评估 / 龙头与基金 |

## 目录结构

```
index.html                        导航首页
.github/workflows/refresh-data.yml 合并版自动更新 workflow
industry/                         行业低位雷达（页面三件套 + industries.json + scripts/update.py）
radar/                            多因子雷达（页面三件套 + MODEL.md + scripts/scan.py）
```

## 自动更新

GitHub Actions 每交易日 **16:30（北京）** 依次执行：
1. `industry/scripts/update.py`（行业数据，约 1~2 分钟）
2. `radar/scripts/scan.py`（多因子全市场扫描，约 6~8 分钟）
3. 数据有变化则提交回 main → 站点整体发布到 `gh-pages` 分支

手动触发：仓库 Actions → daily-scan → Run workflow。

## 本地运行

两个脚本都基于自身路径定位输出，在任何目录运行均可：

```bash
cd industry && python scripts/update.py     # 产出 industry/data.json
cd radar    && python scripts/scan.py       # 产出 radar/data.json + fallback-data.js
```

本地预览：在仓库根目录起静态服务后访问首页/radar//industry/。

> 数据来源：腾讯财经公开行情接口。研究工具，不构成投资建议。
