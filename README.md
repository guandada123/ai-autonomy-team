# ai-autonomy-team

**给「多角色 AI 评审」做过程约束与结果验收的工具集。**
它不产出结论，它约束"得出这个结论的过程是否可信"。

零第三方依赖 · 纯标准库 · Python ≥ 3.9 · 可单独 clone 后直接运行

---

## 当前状态（2026-10-23 实测）

| 指标 | 值 |
|---|---|
| 交接自检 | 6/6 项通过（`verify_handover.py`） |
| 发布门禁 | A–H 全过（8/8） |
| 全量回归 | 50/50 |
| 源码规模 | 26 个脚本（`scripts/` 24 + `judges/` 2）/ 约 12,600 行 |
| 已有资产 | 33 轮运行记录 · 47 份决策 · 15 条经验教训 · 7 份协议 |

---

## 30 秒上手

```bash
cd ai-autonomy-team

# 1) 这套东西在你这台机器上能用吗
python3 scripts/verify_handover.py
#    全新 clone 首跑会红在第 6 项（推送钩子是本地文件、不入库）：
make hooks        # 装完再跑一次即 6/6

# 2) 全量门禁（发版前必须过）
make check          # 等价于 python3 scripts/pre_release_check.py

# 3) 全量回归
make test           # 等价于 python3 scripts/regress.py
```

没有 `make` 也能跑——每个命令都是一行 `python3 scripts/xxx.py`，`make help` 里有对照。

---

## 常用命令

| 想做什么 | 命令 |
|---|---|
| 接手验收 | `python3 scripts/verify_handover.py` |
| 发版前检查 | `make check` |
| 跑全量回归 | `make test` |
| 看三轨指标 | `make metrics` |
| 开一轮评审 | `python3 scripts/new_round.py --round 34 --params migration-package/config/params.json` |
| 八角审查一个决定 | `make review DECISION='要不要把 X 接进发版门禁'` |
| 推远端前 | `make pre-push`（历史密钥 + 远端是否 private） |
| 装推送钩子（换机后） | `make hooks` |
| 本地备份 | `make backup`（bundle + 归档 tar，逐份还原验证）；`make backup-list` 只列不生成；另有月度自动化 |
| 改完源码同步到包 | `make package-apply` |
| 打真源快照 | `make snapshot LABEL='改口径'` |
| 全部入口 | `make help` |

---

## 我该读哪份

| 你的目的 | 读这份 |
|---|---|
| 想知道**怎么用** | 本文件 + `make help` |
| 想知道**为什么这样设计**、模块边界在哪 | [ARCHITECTURE.md](./ARCHITECTURE.md) |
| 想知道**依赖与环境**、怎么迁移 | [DEPENDENCIES.md](./DEPENDENCIES.md) |
| 想知道**判据口径**（什么算合格） | `docs/protocol/` 7 份协议，先读 `05-工作协议.md` |
| 想知道**某个决定为什么这么定** | `docs/decisions/` 45 份，按编号搜 |
| 想知道**踩过什么坑** | `.learnings/` 12 条，`grep -r "关键词" .learnings/` |
| 想知道**评审框架整体方案** | `docs/自治团队-最终方案-20261022.md` |

---

## 目录速览

```
scripts/               ★ 唯一源码树（24 个脚本 + judges/ 2 个）
migration-package/config/  契约参数（★ 在包内，工作区根没有；改这个 = 改判据口径）
docs/protocol/         7 份工作协议
docs/decisions/        47 份决策记录（编号到 D-0048；D-0017 未建，以文件数为准）
.learnings/            15 条经验教训（L-XXXX）
templates/             席位提示词模板
fixtures/frozen/       冻结基线（回归第 8 项依赖，不可删）
migration-package/     ⚠️ 构建产物（--check 可验证），其中 runs/ 是 33 轮真实记录
reviews/               八角审查产出
_trash/  _archive/     ⊘ 不入库：scratch 可重建，快照归档只此一份
```

**三类目录别混淆**：

| 类型 | 能否手工改 | 能否删 |
|---|---|---|
| `scripts/` `config/` `templates/` | ✅ 改完跑 `make check` | ❌ |
| `migration-package/`（除 `runs/`） | ❌ 会被判漂移，用 `make package-apply` | ✅ 可重建 |
| `migration-package/runs/` `reviews/` `real-runs/` | ❌ 是物证 | ❌ |

---

## 三条不能丢的约束

代码可以重写，这三条不行（详见 ARCHITECTURE §6）：

1. **未裁决必须是非零退出码** —— 返 0 就等于上游把"没裁决"当"通过"。
2. **验收依赖必须是交付物的一部分** —— 住在 `_trash/` 里的基线，新机器 clone 后必红。
3. **脚本不得写它正在校验的资产** —— 否则校验会自己制造"通过"。

---

## 贡献方式（给自己看的）

```bash
# 改代码
vim scripts/xxx.py

# 改完必须三件套
make test           # 50 项回归
make package-apply  # 同步到包（否则 D 项红）
make check          # 8 项门禁

# 踩了新坑 → 落一条经验
# .learnings/L-00NN-一句话标题.md
```

**不要**直接改 `migration-package/` 下的文件——它是产物。
**例外**：`migration-package/gen_manifest.py` 与 `precheck.py` 是包内独有的源码，改它们要同时记到 `build_package.py` 的说明里。
