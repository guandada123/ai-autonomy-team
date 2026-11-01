# 外部素材来源与用途（2026-11-01）

按用户指定范围取材：**engineering-audit-kit（EAK）＋ Claw / QuantTradingSystem / StockInsight 的真实 CI 片段**。
所有素材**按 commit 取字节**（`git show HEAD:<path>`），落盘后 sha256 与源逐一校验一致（见 `MATERIALS-MANIFEST.json`）。

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
| 6 | engineering-audit-kit | `368f4d8` | `.github/workflows/caller-example.yml` | `docs/templates/audit-kit/workflows/caller-example.yml` | 837 | 参考：`workflow_call` 可复用工作流的调用方写法 |
| 7 | engineering-audit-kit | `368f4d8` | `playbooks/templates/PROJECT-AUDIT-TEMPLATE.md` | `docs/templates/audit-kit/PROJECT-AUDIT-TEMPLATE.md` | 901 | 模板：项目审计报告骨架 |
| 8 | engineering-audit-kit | `368f4d8` | `pyproject.toml` | `docs/templates/audit-kit/pyproject.reference.toml` | 195 | 参考：审计工具依赖声明 |
| 9 | Claw | `082cef8` | `.github/workflows/claw-ci.yml` | `docs/templates/ci-snippets/claw/claw-ci.yml` | 18006 | 参考：真实主干 CI（含多 job 门禁与失败聚合） |
| 10 | Claw | `082cef8` | `.github/workflows/security-scan.yml` | `docs/templates/ci-snippets/claw/security-scan.yml` | 10097 | 参考：真实密钥/漏洞扫描（版本 A） |
| 11 | QuantTradingSystem | `8cc5e344` | `.github/workflows/ci.yml` | `docs/templates/ci-snippets/quanttrading/ci.yml` | 1099 | 参考：最小可用 CI 骨架（1KB） |
| 12 | QuantTradingSystem | `8cc5e344` | `.github/workflows/security-scan.yml` | `docs/templates/ci-snippets/quanttrading/security-scan.yml` | 12313 | 参考：真实扫描（版本 B，三种里最全） |
| 13 | StockInsight | `1d8635c` | `.github/workflows/stockinsight-ci.yml` | `docs/templates/ci-snippets/stockinsight/stockinsight-ci.yml` | 11209 | 参考：严格 7-job 门禁编排 |
| 14 | StockInsight | `1d8635c` | `.github/workflows/security-scan.yml` | `docs/templates/ci-snippets/stockinsight/security-scan.yml` | 5559 | 参考：真实扫描（版本 C，最精简） |

机读清单：`docs/templates/MATERIALS-MANIFEST.json`（含每件完整 sha256 与 `activated:false`）。

## 三、缺口对照：哪些被填上、哪些仍缺

| 缺口 | 状态 | 说明 |
|---|---|---|
| 无 pre-commit | ✅ 件已到位 | 文件在根目录，但**未执行 `pre-commit install`**，提交不受影响 |
| 无 gitleaks 规则 | ✅ 件已到位 | 本项目另有自定义「E 密钥扫描」闸（`pre_release_check`），二者**并行不冲突** |
| 无 CODEOWNERS | ✅ 样例已到位 | 仅 `.sample`，不参与生效 |
| ruff 配了但 CI 不跑 | ✅ **2026-11-01 已解决** | 独立 CI 步骤（固定 ruff 0.15.17），本地 `make lint`；接入时存量 6 处已全修 |
| RESULT 报告格式不统一 | ⚠️ 模板已到位 | 未强制套用；需要时可把新报告按模板写 |
| 依赖漂移（ruff/bandit 版本） | ⚠️ 参考已到位 | 本项目 `pyproject.toml` 未改，版本仍以自身为准 |

## 四、启用方法（都需你点头，我未执行）

| 要做的事 | 命令 / 改动 | 影响与风险 |
|---|---|---|
| 启用本地钩子 | `pip install pre-commit gitleaks && pre-commit install` | 之后每次 commit 先跑检查。**注意**：配置含 `ruff-format`，首次 `--all-files` 会**重排 280 个 py 文件**，建议先 `ruff format --diff` 看规模 |
| ~~让 CI 跑 ruff~~ | **已于 2026-11-01 执行** | 存量只有 6 处且全可自动修，未红；规则仍为 F+E9 |
| 启用 CODEOWNERS | 改名 `CODEOWNERS.sample` → `.github/CODEOWNERS`，并把 `@team/*` 换成真实账号 | 占位团队不存在会导致 PR 评审请求异常，**改名之前必须换人** |
| 用 gitleaks 规则 | `.gitleaks.toml` 已就位，被调用才生效 | **文件标题还写着 `Claw Monorepo Secret Scan`**，改成本项目名再对外用 |

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
