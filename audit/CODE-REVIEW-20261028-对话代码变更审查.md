# 本对话代码变更审查（code-guard-pro）

- **日期**：2026-10-28 09:20–09:40
- **范围**：`1e3cbd0..344bffc`，共 **9 个提交、40 个文件（不含快照）、+3352 / −111 行**
- **方法**：逐提交 diff 重读 + 3 个实证探针（不采信提交信息与过程陈述）
- **代码文件**（9 个）：`inspection_watch.py`、`regress.py`（×2）、`Makefile`、`compare_v12.py`、`recompute_truth.py`、`recompute_watch.py`、`inspect_status.py`、`make_backup.py`、`snapshot_sources.py`

## 一、结论（先行）

**无 P0/P1。主路径正确性成立，可以交付。** 发现 **3 个 P2**（均有探针实证）与 **8 个 P3**。
3 个 P2 全部集中在"边缘路径上的输出/配置"，不影响任何门禁与判分主链路——但这正是它们能
活到今天的原因：门禁 8/8 与回归 56/56 都不覆盖"建议给人执行的命令"和"LLM 读的 prompt"。

| 级别 | 数 | 性质 |
|---|---|---|
| P0 | 0 | — |
| P1 | 0 | 主链路（判分/聚合/门禁/回归）无正确性缺陷 |
| P2 | 3 | 有实证的缺陷，建议本周修 |
| P3 | 8 | 风格/健壮性，择机修 |

## 二、P2 问题清单（实证 → 建议）

### P2-1 `make_backup.do_retention` 打印的清理命令未加引号 ⚠️ 安全相关

**实证**：`--dest "…/space dir"` 下实际输出：
```
trash /var/folders/…/space dir/snapshots-archive-20261024-0044.tar.gz
```
shell 会把空格处拆成两个参数：`trash /var/…/space` + `dir/snapshots-…`。
这是**给人复制执行的删除命令**——路径拼错时要么报错（现网大概率），要么删错对象（若相对路径恰好存在）。
默认 `~/Backups/ai-autonomy-team` 无空格所以今天没炸，但 `--dest` 是公开参数。

**建议**：`shlex.quote(str(p))`；同时 `ls -l` 行同样处理。2 行改动。

### P2-2 `snapshot_sources.catalog` 读坏 CATALOG.json 时静默丢失 missing 历史

**实证**：把 CATALOG.json 写成 `not-json{{{` 后重跑，`missing` 历史条目直接消失（探针输出
`missing: 0`、ghost 不在新文件里），**无任何告警**。该文件的唯一价值就是"累计式"追溯
（`D-0078` §四），静默降级成 `ls` 缓存等于功能没了但没人知道。

**建议**：`except (OSError, json.JSONDecodeError)` 分支打印
`[WARN] 既有清单不可读（…），missing 历史将丢失；原文可从 git 取回` 后再继续。
不选硬失败：快照流程不该被一个旁挂清单卡死，且文件在 git 里可回溯。

### P2-3 08:05 巡检 prompt 写死基线计数（自违铁律）

**实证**：automation `89087d94` 的 prompt 含「已 resolved 30 轮 / 未终态 4 轮 / 自称 pending
但已裁决 29 轮」。而本项目 08:10 prompt 的教训原文就是「**不要写死任何计数**——历史上把
『50 项』写进 prompt，脚本加到 54 项后这个提示一直是错的」。新增 round35 的那一刻，
这三个数字就开始错。

**建议**：删掉具体数字，改为「以脚本当次输出的 `CURRENT STATE SUMMARY` 为准，与上一日
日志对比，明显变化才上报」。属 automation 配置修改，**等你点头再动**。

## 三、P3 问题清单

| # | 位置 | 问题 | 建议 |
|---|---|---|---|
| 3-1 | `inspect_status.py` | 轮目录**字典序**：输出顺序 round1→round10→…→round34→**round4**→round5（探针1 实证）。新增 current state 行后按序阅读易误判 | `sorted(key=lambda n: int(re.sub(r'\D','',n) or 0))` 自然排序 |
| 3-2 | `inspect_status.py` | `current state: 未终态（无 answer-status.json —— 未终态）` 措辞重复 | detail 去掉尾缀 |
| 3-3 | `inspect_status.py` | verdict 行全量打印 pending 列表（round13 一行 ~500 字），每日 08:05 自动化读 stdout 的 token 成本 | 只打 `[ADJ-001, ADJ-002]` |
| 3-4 | `make_backup.py` | `do_retention` 与 `do_list` 重复同一 glob 清单 | 提取 `_backup_files(dest)` |
| 3-5 | `make_backup.classify` | 月份解析 `t.name.split("-")[2][:6]` 硬编码下标，文件名格式变更会**静默错月** | 加注释 + 对解析结果做 `\d{6}` 断言 |
| 3-6 | `snapshot_sources.main` | `snapshot()` 成功后 `catalog()` 异常会裸 traceback；自动化看到非 0 退出可能重试 → 再产一个快照（churn） | catch 后打明确错误，退出码保持非 0 |
| 3-7 | `snapshot_sources.catalog` | fresh clone 上直接跑会 `mkdir` 出空 `snapshots/`，改变门禁 H 的报错文案（边缘路径） | out 目录不存在且无快照时先提示 |
| 3-8 | `make_backup.do_retention` | 清理命令用 `trash`；本机 `/usr/bin/trash` 存在（已验证），但该命令非 macOS 自带 | 注释注明 fallback（Finder / osascript） |

## 四、逐提交审阅（结论：全部核验通过）

### `915b90b` round13 两席重跑（inspection_watch.py / regress.py + 数据产物）
- **check_c2 修复正确**：新三分支（双含→STILL / 仅 inputs 含→CONTRADICTION / 其余→GONE）
  与 §E2 预注册语义一致；把「blocked 含 + inputs 不含」从 CONTRADICTION 改为 GONE 是修 bug 不是放水——
  该状态即"已修"。回归第 54 项同时覆盖 STILL/CONTRADICTION/GONE 三分支，判据可被正样本击破。
- **数据产物**：`answer-final.json` 重写前留 `.3seat-superseded.json` 并逐字节校验（正确做法）；
  数值与三席版全等（380.00/0.00/6/0.55），未污染。
- 无发现。

### `ac1e0c0` 题库 v1.2（recompute_truth.py / compare_v12.py / recompute_watch.py / Makefile）
- **`do_doc_line_num` 正确**：文件 sha256 锚定 → 行号越界 → 正则取不到，三层全部 fail-red；
  「没提取到」与「提取到但不同」都是红（有负样本实证：改值 88 → 漂移、pattern 加 X → 不可算）。
  小注：`float(…) if "." in …` 对千分位（1,117）会抛异常——但抛异常=红，方向正确，可接受。
- **compare_v12.py**：重算闸门用 `HERE` 相对路径 + `sys.executable`，不通过拒绝出分（正确）；
  判分复用 `paired_compare.grade` 单一真源，未复制判分逻辑。
- **v1.1→v1.2 切换三处同步**（recompute_watch / Makefile / 08:10 prompt），无"代码跑 v1.2、
  prompt 写 v1.1"的错位。无发现。

### `53fbca5` round13 重判（纯数据）
- `answer-status.json` / `adjudicated.json` 重写，`provenance=post_hoc_derived`（诚实标注），
  旧 sha 记入 integrity-notes；`adjudication-record.json`（人类裁决）未动。无发现。

### `eb83c08` / `5beecc4` / `26c7be0` / `f5842ba`（数据与文档）
- round4 补状态只补缺失文件；`aggregation_inputs[]` 缺失**刻意不补**（L-0032，正确）。
- 文档计数同步经门禁 A 实测闭环。无发现。

### `7b23c4a` 四项修复（inspect_status / make_backup / regress / snapshot_sources）
- **inspect_status**：`current_state` 两套版式都认且标明来源（round34 差点被误报，回归覆盖）；
  `--runs` 只影响回归路径，缺省行为不变；模块级 argparse 有注释说明理由（可接受偏差）。
- **make_backup**：`classify` 纯函数 + 自检三态（含两个反模式探针）；月度锚点跨月用例正确
  （10-31 被留、11-05 是候选）。
- **snapshot_sources**：catalog 累计合并、排序确定、自检 16 项（含鬼条目、计数、排序）。
- **regress 第 55 项**：合成样本不依赖活数据；守门顺延 56 且文档同步。
- 发现即上文 P2-1/2、P3-4~8。

### `344bffc` 提示语改指 --retention
- 一行文案，无风险。无发现。

## 五、跨变更冲突与副作用

| 项 | 结论 |
|---|---|
| 功能性冲突 | **无**。v1.2 切换（3 处）、回归项顺延（55→56）、门禁 A 计数（4 文档）均已闭环，门禁 8/8 实测 |
| 输出格式成为契约 | `inspect_status` 的 `current state:` 行措辞现被回归第 55 项与 08:05 prompt 双重依赖——改动措辞会同时打红两者。这是刻意的（格式即契约），但改前要三处同改 |
| 快照 churn | 今日打快照 7 次（3 次属修 bug 的门禁循环）；git 内恒保持 3 份，历史无膨胀；CATALOG 全量追踪（219 条） |
| 备份增长 | 668.6MB / 20 文件；策略分类：保留 13 份 / 254.8MB，**可清理 7 份 / 413.8MB**（待用户拍板，工具不删） |
| 安全 | 无 `shell=True` / `eval` / 密钥（pre_push_scan：18 模式 × 3077 blob PASS）；子进程全 list 参数；唯一安全相关项 = P2-1 |

## 六、优先级排序的修改建议

1. **P2-1** `shlex.quote`（2 行，消掉"建议执行的删除命令"的拼错风险）
2. **P2-2** catalog 读坏时的 stderr 告警（3 行，保住追溯功能的可见性）
3. **P2-3** 08:05 prompt 去掉写死计数（automation 配置，等你点头）
4. **P3-1/2/3** inspect_status 三小项（自然排序 / 措辞 / pending 截断）——建议与 1、2 同一个提交
5. **P3-4~8** 择机，随下次触碰对应文件时顺手做（不要专门为此开提交——本仓铁律：别"顺便优化"）
6. 备份 7 份清理：**等你拍板**，`make_backup.py --retention` 已给出复核与执行命令

## 七、待确认项

- [ ] P2-3 涉及每日巡检自动化行为，修改需你确认
- [ ] 备份 7 份 / 413.8MB 是否清理（工具已给命令，不自动删）
- [ ] CI 全月不可用（外部约束，11-01 恢复）：本次审查的全部推送无 CI 验证，本地门禁为唯一保证

---

*AI 生成（代码审查 缺陷定位 修复建议 · code-guard-pro）；所有数字可回溯到本文件引用的探针输出与 git diff。*
