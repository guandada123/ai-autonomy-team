# 外部素材来源与用途（2026-11-01）

按用户指定范围取材：**engineering-audit-kit（EAK）＋ Claw / QuantTradingSystem / StockInsight 的真实 CI 片段**。
所有素材**按 commit 取字节**（`git show HEAD:<path>`），落盘后 sha256 与源逐一校验一致（见 `MATERIALS-MANIFEST.json`）。

> **2026-11-01 修订（3 件）**：`ci-snippets/quanttrading/ci.yml`、`audit-kit/workflows/caller-example.yml`
> 的 `quality-gate.yml@v3` 已齐到 `v8`（v4–v8 修了 pytest 假绿灯、新增 `run-pytest` 委托输入、
> 把 bandit / npm audit 的吞错改成真闸门）；`ci-snippets/quanttrading/security-scan.yml` 的提示文案
> 由 `@v4` 改为 `@v5`（与实际 pin 一致）。三件的 bytes 与 sha256 已在 `MATERIALS-MANIFEST.json`
> 按修改后重算，其余 11 件仍与源 commit 逐字节一致。**照抄 v3 会漏掉上述修复。**

**当前状态：只导入，不启用。** `.github/workflows/` 未新增任何文件；未安装 pre-commit 钩子；CI 行为未改。

---

## 一、找的是什么类型的资源（先说结论，再说为什么）

| 资源类型 | 是否本项需要 | 依据（缺口比对） |
|---|---|---|
| **配置** | ✅ 需要 | 本项目有 `.editorconfig`、`pyproject.toml`，但**无** `.pre-commit-config.yaml`、**无** `.gitleaks.toml`、**无** CODEOWNERS |
| **CI 模板 / 工作流** | ✅ 需要 | `pyproject.toml` 里配了 `[tool.ruff]` 与 `[tool.ruff.lint]`，但 `.github/workflows/selftest.yml` **完全没跑 ruff** —— 配置在睡觉（"已配置 ≠ 被读取"） |
| **文档模板** | ✅ 需要 | 本项目 10 份 `RESULT-*` 各写各的格式，缺统一审计报告骨架 |
| **依赖声明** | ✅ 参考 | 需要知道同类仓库把 ruff / bandit / gitleaks 怎么声明（**仅参考，不覆盖本项目 `pyproject.toml`**） |
| **代码示例** | ⚠️ 有限 | 本项目 280 个 `.py` 已有自己的闸门（`audit/kit/*`），不需要从外部搬实现；只需可复用的**扫描规则**与**接线方式** |

## 二、实际导入（14 件，全部字节校验通过）

| # | 来源仓库 | commit | 源路径 | 落盘位置 | 字节 | 用途 |
|---|---|---|---|---|---|---|
| 1 | engineering-audit-kit | `368f4d8` | `.pre-commit-config.yaml` | `.pre-commit-config.yaml` | 1019 | 填 pre-commit 缺口：标准钩子 + ruff + 本地 gitleaks |
| 2 | engineering-audit-kit | `368f4d8` | `.gitleaks.toml` | `.gitleaks.toml` | 840 | 填密钥扫描缺口：allowlist + 高熵 API key 规则 |
| 3 | engineering-audit-kit | `368f4d8` | `configs/CODEOWNERS.sample` | `CODEOWNERS.sample` | 358 | 填 CODEOWNERS 缺口（保留 `.sample`，见 §四） |
| 4 | engineering-audit-kit | `368f4d8` | `.github/workflows/eak-ci.yml` | `docs/templates/audit-kit/workflows/eak-ci.yml` | 3746 | 参考：ruff + bandit + gitleaks 如何接 CI |
| 5 | engineering-audit-kit | `368f4d8` | `.github/workflows/quality-gate.yml` | `docs/templates/audit-kit/workflows/quality-gate.yml` | 4274 | 参考：质量门禁工作流编排 |
| 6 | engineering-audit-kit | `368f4d8` | `.github/workflows/caller-example.yml` | `docs/templates/audit-kit/workflows/caller-example.yml` | 1046 | 参考：`workflow_call` 可复用工作流的调用方写法 |
| 7 | engineering-audit-kit | `368f4d8` | `playbooks/templates/PROJECT-AUDIT-TEMPLATE.md` | `docs/templates/audit-kit/PROJECT-AUDIT-TEMPLATE.md` | 901 | 模板：项目审计报告骨架 |
| 8 | engineering-audit-kit | `368f4d8` | `pyproject.toml` | `docs/templates/audit-kit/pyproject.reference.toml` | 195 | 参考：审计工具依赖声明 |
| 9 | Claw | `082cef8` | `.github/workflows/claw-ci.yml` | `docs/templates/ci-snippets/claw/claw-ci.yml` | 18006 | 参考：真实主干 CI（含多 job 门禁与失败聚合） |
| 10 | Claw | `082cef8` | `.github/workflows/security-scan.yml` | `docs/templates/ci-snippets/claw/security-scan.yml` | 10097 | 参考：真实密钥/漏洞扫描（版本 A） |
| 11 | QuantTradingSystem | `8cc5e344` | `.github/workflows/ci.yml` | `docs/templates/ci-snippets/quanttrading/ci.yml` | 1609 | 参考：最小可用 CI 骨架（1KB） |
| 12 | QuantTradingSystem | `8cc5e344` | `.github/workflows/security-scan.yml` | `docs/templates/ci-snippets/quanttrading/security-scan.yml` | 12313 | 参考：真实扫描（版本 B，三种里最全） |
| 13 | StockInsight | `1d8635c` | `.github/workflows/stockinsight-ci.yml` | `docs/templates/ci-snippets/stockinsight/stockinsight-ci.yml` | 11209 | 参考：严格 7-job 门禁编排 |
| 14 | StockInsight | `1d8635c` | `.github/workflows/security-scan.yml` | `docs/templates/ci-snippets/stockinsight/security-scan.yml` | 5559 | 参考：真实扫描（版本 C，最精简） |

机读清单：`docs/templates/MATERIALS-MANIFEST.json`（含每件完整 sha256 与 `activated:false`）。

## 三、缺口对照：哪些被填上、哪些仍缺

| 缺口 | 状态 | 说明 |
|---|---|---|
| 无 gitleaks 规则 | ✅ **2026-11-02 已启用** | 配置按本仓库修正（title / allowlist / 引号支持），实测 0 误报且能抓高熵串与 AWS 前缀；详见 §四 |
| 无 CODEOWNERS | ✅ **2026-11-02 已启用** | 占位样例已删，改为适配本仓库真实结构的 `CODEOWNERS`，owner 全为 `@guandada123`。**当前单人 + main 未保护 → 眼下不生效**，价值在"未来加人时自动指派对的审查人" |
| 无 pre-commit | ⏳ 待启用（需决策） | 文件就位、工具已装。**唯一卡点是 `ruff-format` 钩子**：本仓库实测会重排 **79 个 py 文件**（不是早前抄来的 280，那是 Claw 的数字），需先定"要不要一次性格式化" |
| ruff 配了但 CI 不跑 | ✅ **2026-11-01 已解决** | 独立 CI 步骤（固定 ruff 0.15.17），本地 `make lint`；接入时存量 6 处已全修 |
| RESULT 报告格式不统一 | ⚠️ 模板已到位 | 未强制套用；需要时可把新报告按模板写 |
| 依赖漂移（ruff/bandit 版本） | ⚠️ 参考已到位 | 本项目 `pyproject.toml` 未改，版本仍以自身为准 |

## 四、启用记录（2026-11-02）

2026-11-01 的"只导入不启用"是当时的决定；2026-11-02 用户要求启用，逐项执行如下。

| 项 | 实际做了什么 | 验证方式与结果 |
|---|---|---|
| **gitleaks** | ① title `Claw Monorepo Secret Scan` → 本项目；② allowlist **最小化**排除扫描器自指（`pre_push_scan.py`，密钥检测器源码必然含密钥模式）与非源码缓存（`__pycache__`/`_trash`，均未被 git 跟踪）；③ `generic-api-key` 正则补引号支持（常见写法 `KEY = "value"` 原本抓不到） | 首跑命中 5 处（当前 4 + 历史 1）**全是自指误报**，真泄露 0；改后 `gitleaks dir` / `gitleaks git`（197 commits）**均 no leaks**；正样本注入（40 位高熵串、`AKIA...`）**能抓到**，证明 0 误报不是靠关规则换来的 |
| **CODEOWNERS** | 删除占位样例（`@team/leads`/`@alice`/`@bob` + 本仓库不存在的 `app/**`/`src/**`/`infra/**`），改写为真实结构：默认 `*` + 8 条关键路径（CI/工具链配置/闸门实现/协议决策） | `verify_materials.py` 查"生效行里的 owner"（跳过 `#` 注释），只认 `@guandada123`；反证：把占位内容真放进去 → 报红并点名三个占位账号 |
| **pre-commit** | **未启用**（见上表） | — |

### 🔴 启用 CODEOWNERS 前必须知道的一件事

开 GitHub 分支保护的「Require review from Code Owners」之前，**必须先至少再加 1 个协作者**。
原因是 GitHub 不允许 PR 作者 approve 自己的 PR——单人仓库开了这项，自己的 PR 会**永久卡在等 approve**，
且不报错、只是永远等不到。这条已写进 `CODEOWNERS` 文件头的注释里。

## 五、若要启用 pre-commit（决策项，尚未执行）

| 选项 | 做法 | 代价 |
|---|---|---|
| A 一次性格式化 | `pre-commit run --all-files` → 单独提交一次 | 79 个 py 文件重排，`git blame` 被污染；之后提交不再被打断 |
| B 去掉 format 钩子 | 从 `.pre-commit-config.yaml` 删 `ruff-format`，保留 lint + 空白类 + gitleaks | 无大 diff；格式不强制统一（`ruff check` 仍保证质量） |
| C 原样启用不预格式化 | 直接 `pre-commit install` | 每个含未格式化代码的提交都会被钩子改写并中断，需手动 re-add |

其余钩子（trailing-whitespace / end-of-file-fixer / check-yaml / check-toml / check-json /
check-added-large-files / check-merge-conflict / detect-private-key / mixed-line-ending /
ruff --fix / gitleaks）均已验证可运行，且 gitleaks 钩子命令 `gitleaks git --pre-commit` 语法有效。

## 五、检查过但**未采用**的内容（以及为什么）

1. **EAK `onboarding/per-repo/{Claw,QuantTradingSystem,StockInsight}/.github/workflows/ci.yml`** —— 已比对：与真实仓库**全部漂移**；其中 Claw 与 StockInsight **根本没有 `ci.yml`**（真实文件是 `claw-ci.yml` / `stockinsight-ci.yml`）。副本会把过期内容带进来，故弃用，改取真实文件（#9–#14）。
2. **EAK 的 `playbooks/*.md` 正文（ALL-PROJECTS / FRONTEND-REACT-VUE / LARAVEL-LIVEWIRE-FLUXUI）** —— 与本项目（Python 审计闸门）技术栈不匹配，只取其 `templates/` 骨架。
3. **三家 `security-scan.yml` 合并成一份** —— 三者是**三个不同版本**（sha 各不相同：`36bfc1b3` / `78dd0e6a` / `f9c31ee4`，5.5–12KB），合并会丢掉各自演进；故各自保留，供对比后取舍。
4. **EAK `configs/CODEOWNERS.sample` 直接改名启用** —— 见 §四：占位团队会坏 PR 路由。

## 六、可能的替代方案（若后续需要更多素材）

- 若需要**可执行**的扫描器而不只是配置：EAK 的 `pyproject.toml` 指向 ruff/bandit，二者可直接 `pip install`，无需从仓库拷实现。
- 若需要**更强的密钥扫描**：本项目已有自定义 E 闸，可只补 `.gitleaks.toml` 的规则集而不装 pre-commit。
- 若需要**统一 RESULT 格式**：用 `docs/templates/audit-kit/PROJECT-AUDIT-TEMPLATE.md` 作为骨架，但本项目已有 `RESULT-*` 惯例（结论先行 + 双口径），建议以现惯例为主、只借鉴其「风险与遗留」小节。

---

**取件可复现**：每条均含 `source_repo` + `source_commit` + `sha256`，重放命令示例：

```bash
git -C ../engineering-audit-kit show 368f4d8:.pre-commit-config.yaml
```

（`368f4d8` 为 EAK 取件时 HEAD；若上游已前进，用清单里的 commit 可回溯到同一字节。）
