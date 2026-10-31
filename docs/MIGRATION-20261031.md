# 迁出会话目录（2026-10-31）

## 为什么迁

本项目原位于 `WorkBuddy/2026-10-19-22-33-38/ai-autonomy-team` —— 一个**会话时间戳目录**。

会话目录是平台每次对话自动创建的工作区，不是项目的正式归属地。项目在这里意味着：

- 按目录名找项目时找不到（只能靠记时间戳）
- 与 100+ 个其他会话目录混在同一层
- 语义上被当成"某次对话的临时产物"，而实际它有 163 个 commit、GitHub 远端、独立发布门禁

## 迁移结果

| 项 | 值 |
|---|---|
| 旧路径 | `/Volumes/ZHITAI/WorkBuddy/2026-10-19-22-33-38/ai-autonomy-team` |
| 新路径 | `/Volumes/ZHITAI/WorkBuddy/ai-autonomy-team` |
| 方式 | `cp -a` 整目录复制 → 验证 → 旧目录改名保留 → 旧路径建软链 |
| 完整性 | 163 commits 一致（HEAD `f7fda37`）、74746 个文件一致、639M 一致、2 个未提交文件一并带走 |

**旧路径现为软链**，指向新位置（同一 inode，非副本）。保留它是为了兼容：
- 5 个自动化的 prompt 里硬编码了旧绝对路径
- `docs/` 下多份历史交接文档引用旧路径

## 受影响的自动化（5 个，cwds 已改）

| 任务 | 说明 |
|---|---|
| 自治团队 · 看板巡检 | 每日 08:05 |
| 自治团队 · 巡检产出核对（元监控） | 每日 09:10 |
| ai-autonomy-team · 每日自检（真值重算 + 发布前门禁） | 每日 08:10 |
| 自治团队 · 失效复盘 | 每月 1 日 10:00（**从未运行过**） |
| ai-autonomy-team 月度备份 | 每月 1 日 20:00（**从未运行过**） |

## 已同步改动

1. **cwds**：5 条全部指向新路径（`next_run_at` 未变，调度未受影响）
2. **`scripts/redact_report.py`**：脱敏规则新增新路径条目，并保留旧路径两条（历史报告里可能仍出现）
3. **Claw 台账** `cross_project_state.json` 的 `active_projects['ai-autonomy-team'].cwd` → 新路径
4. **pmf** 采集快照里的 `path` → 新路径（`missing_path=0`）

## 未改动（刻意）

- `docs/` 下的历史交接文档：它们记录的是**当时的事实**，改了就成篡改历史
- `snapshots/`、`_trash/`、`real-runs/`：历史快照与运行记录，同理
- `.coverage`、`.ruff_cache/`：工具缓存

## 仍需人工处理

1. **prompt 里的旧绝对路径**：5 个自动化的 prompt 仍写着旧路径。目前靠软链兜底（能正常工作），
   但**软链是过渡手段**。彻底方案是把 prompt 里的路径改成新路径，然后删除软链。
2. **旧目录备份**：`2026-10-19-22-33-38/ai-autonomy-team.bak-20261031`（639M）保留中，
   确认新位置稳定运行后可删除，删前建议再比对一次 git 历史。
3. **docs 交接文档**：以后新写的交接文档请用新路径；旧文档按需在开头加一行「路径已于
   2026-10-31 迁移，见 MIGRATION-20261031.md」即可，不必逐行改。

## 验证方式

```bash
# 1. git 历史完整
git -C /Volumes/ZHITAI/WorkBuddy/ai-autonomy-team rev-list --count HEAD   # 应为 163

# 2. 软链指向正确
ls -ld /Volumes/ZHITAI/WorkBuddy/2026-10-19-22-33-38/ai-autonomy-team     # 应显示 -> 新路径

# 3. 同一份文件（非副本）
stat -f %i /Volumes/ZHITAI/WorkBuddy/ai-autonomy-team/README.md
stat -f %i /Volumes/ZHITAI/WorkBuddy/2026-10-19-22-33-38/ai-autonomy-team/README.md  # inode 应相同

# 4. 发布门禁仍可跑
cd /Volumes/ZHITAI/WorkBuddy/ai-autonomy-team && make help
```
