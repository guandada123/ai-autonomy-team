# D-0019 · 读取域：放行本轮 round-manifest.json，但 runs/roundN/ 整目录仍禁读

- **日期**：2026-10-20（补落为正式文件；实施早于此）
- **依据来源**：`migration-package/judges/seat_check.py:523` 注释「round13/round20 实测」
- **操作分级**：🟡 中（读取域判定规则的例外）
- **状态**：已执行

## 决策

读取域按正则**精确放行**本轮的 `runs/roundN/round-manifest.json`；
但 `runs/roundN/` **整目录仍禁读**。

## 依据

| # | 依据 | 来源 |
|---|---|---|
| 1 | `round-manifest.json` 是轮次**元数据**（role / termination / 契约版本），**不含答案与席位产出** | `scripts/new_round.py` `make_manifest` |
| 2 | 席位需要它来核对轮次类型（自己跑的是证据轮还是修订轮） | round13 / round20 实测 |
| 3 | 整目录禁读是修订轮去匿名化的前提 —— 目录里含 `inputs/` 与席位产出 | `seat_check.py` classify_path |

## 备选方案与未采用理由

| 备选 | 未采用理由 |
|---|---|
| 整目录放行 | 修订轮会读到同伴原始产出，去匿名化失效 |
| 完全不放行 manifest | 席位无法确认轮次类型，round13/round20 已实测出此缺口 |

## 审计发现

读取域规则的价值在于**粒度**：同一个路径前缀下，「元数据」与「产出」性质完全不同。
本项目用**正则精确匹配**（`re.fullmatch`）而非前缀放行，就是为了避免「放行一个文件等于放行整个目录」。

同类处理见 D-0021（目录纯列举降级）。

## 落地校验

| 校验 | 方法 | 期望 |
|---|---|---|
| manifest 可声明 | 席位申报 `runs/roundN/round-manifest.json` | `class == "allow"` |
| 目录仍禁 | 申报 `runs/roundN/` | `deny` → 但按 D-0021 降级为 warning |
