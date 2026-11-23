# L-0026 · 仓库身份用 git remote 定案，别信目录名与简称

- **日期**：2026-10-25
- **发现场景**：判分器推广需同步到 Claw / QTS / StockInsight，按目录名找不到 "QTS"
- **性质**：跨项目通用（任何"按简称找仓库"的操作）★ 升级候选 〔🔁auto-promote-2026-11-22·✅已评估·同族覆盖→「仓库身份用git remote定案」〕

## 现象

```
find ... -type d -name "QTS"        → 只命中 WorkBuddy_OLD/.../audits/QTS（审计产出目录，不是仓库）
find ... -name "qts_client.py"      → 命中 clawverify.2549/.workbuddy/scripts/qts_client.py
```

第一反应：`clawverify.2549` 就是 QTS。翻 README 才发现——**它是 Claw 的副本**
（首行 `# Claw — A股投顾自动化助手`）。险些把判分器推到错误的仓库。

## 为什么目录名不可信

| 假设 | 实际 |
|---|---|
| 仓库目录名 = 项目简称 | QTS 的实名是 `QuantTradingSystem`，磁盘上没有叫 QTS 的目录 |
| 含某项目特征文件的目录 = 该项目 | `clawverify.2549` 含 `qts_client.py`，但它是 Claw（调用方，不是 QTS 本身） |
| 深一层就能找到 | 真身就在 `WorkBuddy/QuantTradingSystem`，与简称零字面重合 |

## 定案方法（唯一可靠）

用 **git remote URL** 反查，而不是目录名：

```bash
find ... -name "config" -path "*/.git/config" | while read f; do
  repo=$(dirname $(dirname "$f"))
  grep -qiE "qts|quant|量化" "$f" && echo "$repo"
done
# → /Volumes/ZHITAI/WorkBuddy/QuantTradingSystem (url=.../QuantTradingSystem.git)
```

**判据**：`git remote` 里的 URL 是仓库的自述身份，目录名只是落地时的随手命名。

## 落地动作

- 简称→实名映射写进调用方注释（`sync_score.sh` 已注明 `QTS = QuantTradingSystem`），
  避免下次重蹈；
- 任何"给某项目同步/推送"的动作，**先 git remote 确认身份再写盘**。
