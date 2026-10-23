# 硬裁判 JD-01

> 本目录是整个系统里**唯一持有否决权**的部件。它不是 LLM。
> 规范定义见 `docs/protocol/04-双裁判规范.md`。

## 文件

| 文件 | 说明 |
|---|---|
| `verify.py` | 裁判主体。**纯标准库**，无第三方依赖 |
| `run.sh` | POSIX 薄封装（macOS / Linux / Git Bash） |
| `run.cmd` | Windows 薄封装 |
| `README.md` | 本文件 |

## 为什么只用标准库

因为**目标机是 Mac mini M4**，其系统 `python3` 不含 openpyxl / python-docx 等库。
硬裁判是唯一有否决权的部件——**它不能在目标机上跑不起来**。

## 用法

```bash
# macOS / Linux / Git Bash
./run.sh --spec truth.json --answer answer.json --out result.json

# Windows
run.cmd --spec truth.json --answer answer.json --out result.json

# 直接调用（任何平台）
python3 verify.py --spec truth.json --answer answer.json

# 判定逻辑自检（硬裁判自己也要能被验证）
python3 verify.py --self-test
```

**退出码**：`0` = pass ｜ `1` = fail ｜ `2` = 错误

## 输出契约

```json
{
  "task_id": "...",
  "verdict": "pass | fail",
  "judge": "JD-01",
  "judge_impl": "scripts/judge/verify.py (non-LLM, stdlib-only)",
  "summary": {"total": 5, "passed": 4, "failed": 1},
  "checks": [
    {"name": "...", "type": "...", "ok": true, "expected": "...", "actual": "..."}
  ],
  "evidence": [{"label": "spec", "path": "...", "sha256": "..."}],
  "ts": "2026-10-16T15:36:48+08:00",
  "override_policy": "本裁决不可被静默覆盖。PM 若要推进 fail 的动作，必须留痕：..."
}
```

## 支持的检查类型

| type | 用途 | 关键参数 |
|---|---|---|
| `numeric_equals` | 数值相等 | `path`, `expected`, `tol`(可选) |
| `bool_equals` | 布尔判定 | `path`, `expected` |
| `string_equals` | 字符串相等（去首尾空白） | `path`, `expected` |
| `count_equals` | 条数相等 | `path`, `expected` |
| `set_equals` | 集合相等（无序，报缺失/多出） | `path`, `expected`, `key`(可选，从 list[dict] 抽取字段) |
| `list_equals_unordered` | 列表无序相等 | `path`, `expected`, `key`(可选) |
| `sum_equals` | 合计相等 | `path`, `key`, `expected`, `tol`(可选) |
| `present` | 字段存在且非空 | `path` |
| `absent` | 字段不存在或为空 | `path` |

**路径语法**：点号路径，支持 `a.b.c` 与 `list[0].field`。

## spec 文件格式

```json
{
  "task_id": "SYNTH-RECON-001",
  "checks": [
    {"name": "差异总条数", "type": "numeric_equals", "path": "summary.diff_count", "expected": 5},
    {"name": "去重后金额", "type": "numeric_equals", "path": "summary.dedup_amount", "expected": 1234.56, "tol": 0.01},
    {"name": "口径差异未计入金额", "type": "bool_equals", "path": "summary.caliber_excluded_from_amount", "expected": true},
    {"name": "差异清单命中", "type": "set_equals", "path": "diffs", "key": "id", "expected": ["D1","D2","D3","D4","D5"]},
    {"name": "必须给出证伪条件", "type": "present", "path": "falsification"}
  ]
}
```

## 三条铁律（不得违反）

1. **不得只检查「能不能跑通」** —— MAST 案例：生成的国际象棋程序能编译，但不遵守规则。
2. **判 fail 不可被静默覆盖** —— 覆盖必须留痕（格式见输出契约的 `override_policy`）。
3. **输出必须含可回读证据 + sha256** —— 这是结构审计第 2 查「外部钩子」的判定依据。

## 跨平台注意事项

| 差异源 | 本目录的处理 |
|---|---|
| 解释器 | `run.sh` 探测 `python3`→`python`；`run.cmd` 探测 `py -3`→`python` |
| 路径 | 一律 `pathlib` / `os.path`，不做字符串拼接 |
| 换行 | 输出固定 `newline="\n"`（LF） |
| 编码 | 所有 IO 显式 `encoding="utf-8"`（Windows 默认 GBK 是真实坑） |
| shell | 脚本内不含 bash/zsh 特有语法 |
| 删除 | 不调用 `unlink`；需要清理时移动到 `_trash/` |
