# 行业低位雷达 · 量化行业筛选（GitHub Pages 版）

申万二级细分行业的量化筛选页面：指数走势、低位自动评估（近 3 年 / 近 5 年点位分位）、关联 A 股龙头与行业基金 / QDII 基金。**数据每个交易日自动更新**，打开页面即可看到当天最新行情。

在线示例：`https://<你的用户名>.github.io/<仓库名>/`

---

## 一、首次部署（3 分钟，一次性）

1. **新建仓库**：在 GitHub 网页右上角 `+` → New repository
   - Repository name：如 `quant-radar`（下面统一用这个名字举例，可自行改）
   - 设为 **Public**（GitHub Pages 免费要求），不要勾选 "Add a README"（避免冲突）
2. **上传本目录全部文件**：进入新仓库 → `Add file` → `Upload files` → 把本目录里所有文件和文件夹（含 `.github` 隐藏文件夹，需把网页拖拽区域外的 `.github` 文件夹一起拖入）拖进去 → Commit。也可用命令行 `git clone / push`。
3. **启用 GitHub Pages**：仓库 `Settings` → 左侧 `Pages` → Build and deployment 的 **Source 选 `Deploy from a branch`** → Branch 选 **`gh-pages`** + `/ (root)` → Save。
4. 等约 1 分钟，访问 `https://ducklingli.github.io/quant-radar/`（把用户名和仓库名换成你自己的）。

> 说明：首次推送后，仓库里的 Actions 会立即自动跑一次并生成 `gh-pages` 分支，因此第 3 步要在 Actions 跑完后设置才看得到 gh-pages 分支；若看不到，先到 `Actions` 标签页手动 `Run workflow` 一次。

---

## 二、之后：每次打开页面都是最新数据（全自动）

- **每交易日 16:30（北京时间）**，仓库内置的 GitHub Actions（`.github/workflows/refresh-data.yml`）自动运行：
  1. `python scripts/update.py` 抓取 19 大类 / 57 个细分板块的最新行情与 K 线，重算分位；
  2. 提交更新后的 `data.json` 到 main 分支；
  3. 把 `index.html + data.json + fallback-data.js` 部署到 `gh-pages` → GitHub Pages 立即生效。
- 页面顶部会显示**数据更新时间戳**，一眼可确认是否为当天数据。
- **想要立刻刷新**：仓库 `Actions` 标签 → 左侧 `refresh-data` → `Run workflow` → 绿色按钮，几分钟内完成。

---

## 三、文件结构

```
index.html          页面（纯静态，无第三方依赖）
data.json           最新数据（update.py 生成）
fallback-data.js    数据兜底（fetch 失败时页面自动使用）
industries.json     行业配置（大类→细分→指数代码/龙头股/基金清单）
scripts/update.py   数据更新器（纯 Python 标准库，无第三方依赖）
.github/workflows/refresh-data.yml   GitHub Actions 自动更新+部署
README.md           本说明
```

## 四、本地手动更新（可选）

```bash
python scripts/update.py
```
运行后生成新的 `data.json`，配合本地静态服务器预览：
```bash
python -m http.server 8000    # 浏览器打开 http://localhost:8000
```
直接双击 index.html 也能看（走 fallback-data.js）。

## 五、调整行业 / 龙头 / 基金

编辑 `industries.json` 后提交推送即可：
- `subs[].code`：申万二级板块代码（pt 前缀，腾讯行情口径）
- `subs[].leaders`：龙头股（A股代码，主板优先排列）
- `funds[]`：关联基金，`type` 决定页面分类标注（`ETF` / `LOF` / `QDII-ETF` / `QDII-LOF`）

## 六、口径说明

- 分位 = 当前板块点位在近 N 年全部交易日收盘点位中的百分位（≤10% 显著低估 / ≤25% 低估 / ≤50% 适中 / ≤75% 偏高 / >75% 高估）
- PE(TTM)、PB 为板块整体法快照；点位分位为价格相对位置，不等同于估值分位
- 数据来源：腾讯财经公开行情接口 · 申万二级行业分类
- 本页仅为数据整理与量化参考，不构成投资建议
