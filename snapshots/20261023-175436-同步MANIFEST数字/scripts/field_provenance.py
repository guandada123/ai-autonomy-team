#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""field_provenance.py —— 字段溯源：谁在写这个字段？（只读）

## 为什么存在

2026-10-21 一天内抓到两个同一类缺陷：
  `positions.pnl`                 —— 无活跃写入方，停在两个月前的值（虚高 23.5 倍）
  `summary.invested_cost_stocks`  —— 只被两个一次性脚本写过，每日刷新从不重算，
                                     而 dashboard.html 正在显示

两件事同因：**字段有值 ≠ 有人维护它**。聚合值稳定会掩盖组成变化。

本工具把「找下一个」从靠眼睛变成靠扫描：
  1. 取账本 JSON 里的数值型键（summary.* / holdings[].* / positions[].*）
  2. 在代码库里找写入方（`x["key"] = ...`）与读取方（`.get("key")` / `x["key"]`）
  3. 按文件名判定「活跃」还是「一次性」：
     - 一次性：`_` 前缀、名字含 8 位日期、位于 archive/ 或 _trash/
  4. 输出风险分级：
     🔴 僵尸   有读取方、无活跃写入方（值会永久停在某天）
     🟡 可疑   只有一次性写入方
     🟢 正常   有活跃写入方
     ⚪ 只读   无写入方也无读取方（可能是元数据/备注）

用法：
    python3 scripts/field_provenance.py --json <portfolio.json> --repo <代码库根>
    python3 scripts/field_provenance.py --json ... --repo ... --only-zombie

退出码：0 无僵尸 / 1 存在僵尸
"""
import argparse
import json
import re
import sys
from pathlib import Path

SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git", "archive", "_trash", "tests"}
DATE_IN_NAME = re.compile(r"(19|20)\d{6}")


def is_oneoff(p: Path) -> bool:
    name = p.name
    if name.startswith("_"):
        return True
    if DATE_IN_NAME.search(name):
        return True
    return False


def collect_keys(obj, prefix="", out=None):
    """收集数值型字段路径（只下钻 2 层容器，够用且不淹没）。"""
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.setdefault(k, set()).add(path)
            elif isinstance(v, dict) and prefix.count(".") < 1:
                collect_keys(v, path, out)
            elif isinstance(v, list) and prefix.count(".") < 1:
                for it in v[:3]:
                    if isinstance(it, dict):
                        collect_keys(it, path, out)
    return out


def scan_repo(repo: Path):
    """返回 {key: {"write": [Path], "read": [Path]}}"""
    idx = {}
    for p in repo.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in re.finditer(r'\[\s*"([A-Za-z_][\w]*)"\s*\]\s*=', src):
            idx.setdefault(m.group(1), {"write": [], "read": []})["write"].append(p)
        for m in re.finditer(r'\[\s*"([A-Za-z_][\w]*)"\s*\]', src):
            idx.setdefault(m.group(1), {"write": [], "read": []})["read"].append(p)
        for m in re.finditer(r'\.get\(\s*"([A-Za-z_][\w]*)"', src):
            idx.setdefault(m.group(1), {"write": [], "read": []})["read"].append(p)
        for m in re.finditer(r'^\s*"([A-Za-z_][\w]*)"\s*:', src, re.M):
            idx.setdefault(m.group(1), {"write": [], "read": []})["write"].append(p)
    return idx


def _consumer_reads(src: str, prefix: str):
    """从消费方源码里取 `prefix.field` 引用。

    两道清洗（不加就会误报，2026-10-22 实测）：
      1. 去掉 // 与 /* */ 注释——注释里的旧字段名不是引用；
      2. 前缀前不得有标识符字符——否则 `labels.push` 会被当成 `ls.push`。
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"//[^\n]*", " ", src)
    pat = r"(?<![A-Za-z0-9_$.])" + re.escape(prefix) + r"([A-Za-z_]\w*)"
    return re.findall(pat, src)


def main():
    ap = argparse.ArgumentParser(description="字段溯源：谁在写这个字段")
    ap.add_argument("--json", required=True, help="账本 JSON 路径")
    ap.add_argument("--repo", required=True, help="代码库根目录")
    ap.add_argument("--only-zombie", action="store_true", help="只列 🔴")
    ap.add_argument("--consumer-file", default="", help="前端文件（如 dashboard.html），审计它读的字段是否真的存在")
    ap.add_argument("--consumer-prefix", default="ls.", help="该文件里引用 summary 字段的前缀，默认 ls.")
    args = ap.parse_args()

    path = Path(args.json).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[ERR] 读取失败：{e}", file=sys.stderr)
        return 2

    keys = collect_keys(data)
    idx = scan_repo(Path(args.repo).expanduser())

    rows = []
    for key, paths in sorted(keys.items()):
        rec = idx.get(key, {"write": [], "read": []})
        writers = sorted({str(p) for p in rec["write"]})
        readers = sorted({str(p) for p in rec["read"]})
        active_w = [p for p in set(rec["write"]) if not is_oneoff(p)]
        oneoff_w = [p for p in set(rec["write"]) if is_oneoff(p)]
        if active_w:
            level = "🟢"
        elif oneoff_w:
            level = "🟡"
        elif readers:
            level = "🔴"
        else:
            level = "⚪"
        rows.append((level, key, len(active_w), len(oneoff_w), len(set(readers)), writers))

    order = {"🔴": 0, "🟡": 1, "🟢": 2, "⚪": 3}
    rows.sort(key=lambda r: (order[r[0]], r[1]))

    # ---- 消费方审计：前端读的字段，账本里到底有没有 ----
    missing = []
    if args.consumer_file:
        cp = Path(args.consumer_file).expanduser()
        try:
            src = cp.read_text(encoding="utf-8", errors="ignore")
        except OSError as e:
            print(f"[ERR] 读取消费方文件失败：{e}", file=sys.stderr)
            return 2
        reads = sorted(set(_consumer_reads(src, args.consumer_prefix)))
        have = set((data.get("summary") or {}).keys())
        missing = [k for k in reads if k not in have]
        print(f"[consumer] {cp.name} 读取 {len(reads)} 个 summary 字段"
              f"（前缀 {args.consumer_prefix}）")
        if missing:
            print(f"  🔴 读但账本里不存在 {len(missing)} 个（前端会静默显示 0/undefined）：")
            for k in missing:
                print(f"     - {k}")
        else:
            print("  ✅ 全部存在")
        print()

    print(f"[field_provenance] {path.name}  数值字段 {len(rows)} 个（仓库：{args.repo}）")
    print(f"  🔴 僵尸 {sum(1 for r in rows if r[0]=='🔴')} ｜ 🟡 仅一次性写入 "
          f"{sum(1 for r in rows if r[0]=='🟡')} ｜ 🟢 正常 {sum(1 for r in rows if r[0]=='🟢')} ｜ ⚪ 只读 "
          f"{sum(1 for r in rows if r[0]=='⚪')}")
    print()
    for level, key, na, no, nr, writers in rows:
        if args.only_zombie and level != "🔴":
            continue
        brief = ", ".join(Path(w).name for w in writers[:3]) or "（无写入方）"
        print(f"  {level} {key:<28} 活跃写{na} 一次性写{no} 读取{nr}   {brief}")

    return 1 if (missing or any(r[0] == "🔴" for r in rows)) else 0


if __name__ == "__main__":
    sys.exit(main())
