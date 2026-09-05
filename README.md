# quant-radar · 量化研究工具台（单页三页签版）

一个 GitHub Pages 站点、**一个入口页面、三个页签**承载全部功能：**行业×个股联动**（行业位置全景 + 个股执行清单同页联动）、**实盘交易平台**（账本/现金/日志/每日参考意见）、**实时盯盘** 均以页签内嵌于根页 `index.html`，功能零损失、单一入口。

| 路径 | 内容 |
|---|---|
| `/`（页签①） | **行业×个股 联动台**：市场温度+建议仓位 · 今日行动 · 行业低估概况 · 57 细分全景列表（3/5 年分位条/信号/榜内计数，19 大类 + 宽基过滤）· 行业详情（走势图 日/周/月/年 + 区间/自定义回溯、低位评估、龙头股、关联基金）· 今日执行清单（行业位置徽章 + 买/卖/损全字段，行业行/行业徽章/分档 双向筛选） |
| `/`（页签②） | **实盘交易平台**（内嵌 `portfolio.html?embed=1`）：账本=持仓+现金+操作日志，加/减/清仓加权成本自动演算，导入导出；每日参考意见（温度仓位/持仓指令/持仓行业状态/新开仓候选带预算） |
| `/`（页签③） | **实时盯盘**（内嵌 `radar/live.html?embed=1`）：交易时段 5s 轮询 + 阈值触发提醒 + 自选 |
| `/radar/MODEL.md` | 完整数学公式（含行业共振修正 §3g、卖出模块 §5b、验证边界 §7） |
| `/portfolio.html`、`/radar/live.html` | 独立直链访问亦完整可用（带自身顶栏） |
| `/industry/`、`/radar/` | 自动跳转回整合页（原独立工具页已合并；数据文件仍在原目录供整合页读取） |

## 目录结构

```
index.html                        行业×个股 联动台（唯一入口页）
.github/workflows/refresh-data.yml 每日自动更新 workflow
industry/                         行业数据层（data.json + scripts/update.py + industries.json；index 为跳转壳）
radar/                            个股数据层（data.json + live.html + MODEL.md + scripts/scan.py；index 为跳转壳）
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

本地预览（完整功能需 http 服务，以便跨目录加载双源数据）：

```bash
python -m http.server 8000     # 访问 http://localhost:8000/
```

> 数据来源：腾讯财经公开行情接口。研究工具，不构成投资建议。
