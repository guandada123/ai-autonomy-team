#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""recompute_truth.py —— 题库 v1.1 的「重算检查」：按锚点重算真值，与冻结值比对（只读）

## 为什么需要它（L-0018）

v1 的冻结只校验**文件哈希**——证明"卷子没被改"，但证明不了"卷子还对不对"。
7 小时内 8/27 题的真值就与仓库不符，而回归第 53 项一直绿。

本脚本补的是另一半：**按 anchor 重算真值，与冻结值比**。
- git 类锚到具体提交 → 永久可算，工作区继续增长也不影响；
- 文件类锚到 sha256 → 文件被换即报错，不静默；
- `attested` 类（用户口述 / 会话记录）**不假装能重算**，单独列出要求人工复核。

## 三种结果，不许合并

| 结果 | 含义 | 该做什么 |
|---|---|---|
| ✅ 一致 | 锚点存在且重算值等于冻结值 | 无 |
| ❌ 漂移 | 锚点存在但重算值不等于冻结值 | **停止出分**，修题库（开 v1.2） |
| ⚠️ 不可重算 | `attested` 类，或锚点文件缺失 | 人工确认 / 补回锚点 |

**"不可重算"绝不能算通过**——那正是 v1 出事的形态：没人检查，所以永远绿。

## 用法

    python3 audit/baseline/v1.1/recompute_truth.py            # 全量
    python3 audit/baseline/v1.1/recompute_truth.py --json     # 机读
    make recompute

退出码：0 无漂移（允许存在 attested）｜1 有漂移或锚点缺失｜2 输入/环境错误
"""
import argparse
import ast
import glob
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent          # 仓库根
TRUTH = HERE / "truth-v1.1.json"
TOL = 0.005


# ── 基础工具 ────────────────────────────────────────────────────────────
def sha256_of(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args):
    """只用 list 参数调用 git，不经 shell。"""
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def ls_tree(ref, path=None):
    cmd = ["ls-tree", "-r", "-z", "--name-only", ref] + (["--", path] if path else [])
    out = subprocess.run(["git", *cmd], cwd=ROOT, capture_output=True).stdout
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def expand(p):
    return Path(os.path.expanduser(p))


def safe_arith(expr):
    """只允许数字与 + - * / ( )，用 ast 白名单校验后再算，不直接 eval 任意代码。"""
    node = ast.parse(expr, mode="eval")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd, ast.Load)
    for n in ast.walk(node):
        if not isinstance(n, allowed):
            raise ValueError(f"表达式含不允许的节点：{type(n).__name__}")
        if isinstance(n, ast.Constant) and not isinstance(n.value, (int, float)):
            raise ValueError("表达式只能含数字")
    return eval(compile(node, "<truth>", "eval"), {"__builtins__": {}}, {})


def close(a, b):
    if isinstance(b, (int, float)) and isinstance(a, (int, float)):
        return abs(a - b) <= max(abs(b) * TOL, 1e-9)
    if isinstance(b, list) and isinstance(a, list):
        return len(a) == len(b) and all(close(x, y) for x, y in zip(a, b))
    if isinstance(b, str) and isinstance(a, str):
        f = lambda s: s.strip().lower().replace("×", "x").replace(" ", "")
        return f(a) == f(b)
    return a == b


# ── 各 kind 的重算 ───────────────────────────────────────────────────────
def do_git_ls_count(spec):
    ref, path = spec["ref"], spec["path"]
    inc = spec.get("include", "")
    exc = spec.get("exclude")
    depth = spec.get("max_depth")
    files = ls_tree(ref, path)
    out = []
    for f in files:
        rel = f[len(path):].lstrip("/")
        if depth is not None and rel.count("/") > depth - 1:
            continue
        if inc and inc not in f:
            continue
        if exc and exc in f:
            continue
        out.append(f)
    return len(out)


def do_git_dir_count(spec):
    ref, path, prefix = spec["ref"], spec["path"], spec.get("prefix", "")
    kids = set()
    for f in ls_tree(ref, path):
        rel = f[len(path):].lstrip("/")
        if "/" not in rel:
            continue
        child = rel.split("/")[0]
        if child.startswith(prefix):
            kids.add(child)
    return len(kids)


def do_git_md_count(spec):
    return sum(1 for f in ls_tree(spec["ref"]) if f.endswith(".md"))


def do_git_last_dec(spec):
    ref, path = spec["ref"], spec["path"]
    names = [Path(f).name for f in ls_tree(ref, path)
             if f.endswith(".md") and "TEMPLATE" not in f]
    if not names:
        raise ValueError("找不到决策文件")
    return sorted(names)[-1].split("-")[0] + "-" + sorted(names)[-1].split("-")[1]


def do_git_first_date(spec):
    r = git("log", "--reverse", "--format=%ad", "--date=short", spec["ref"])
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())
    return r.stdout.strip().split("\n")[0]


def do_bundle_count(spec):
    p = expand(spec["path"])
    if not p.exists():
        raise FileNotFoundError(f"锚点文件缺失：{p}")
    got = sha256_of(p)
    if got != spec["sha256"]:
        raise ValueError(f"bundle sha256 不符：文件已非锚定版本（{got[:12]}…）")
    with tempfile.TemporaryDirectory() as td:
        r = subprocess.run(["git", "clone", "--bare", "-q", str(p), td + "/b"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"clone 失败：{r.stderr.strip()[:120]}")
        c = subprocess.run(["git", "-C", td + "/b", "rev-list", "--count", "HEAD"],
                           capture_output=True, text=True)
        return int(c.stdout.strip())


def do_tar_bytes_mb(spec):
    files = [expand(p) for p in spec["paths"]]
    for f, want in zip(files, spec.get("sha256s", [])):
        if not f.exists():
            raise FileNotFoundError(f"锚点文件缺失：{f}")
        got = sha256_of(f)
        if spec.get("sha256_full_required", True):
            if got != want:
                raise ValueError(f"{f.name} sha256 不符")
        elif not got.startswith(want):
            raise ValueError(f"{f.name} sha256 前缀不符")
    # 也把目录里现存的其他 tar 一并纳入检查：数量变了就是漂移
    pat = str(files[0].parent / "snapshots-archive-*.tar.gz")
    present = sorted(glob.glob(pat))
    if len(present) != len(files):
        raise ValueError(f"tar 数量变了：锚定 {len(files)} 份，现存 {len(present)} 份")
    return round(sum(f.stat().st_size for f in files) / 1048576, 1)


def do_json_calibration(spec):
    p = ROOT / spec["path"]
    if not p.exists():
        raise FileNotFoundError(f"锚点文件缺失：{p}")
    if sha256_of(p) != spec["sha256"]:
        raise ValueError("calibration.json 已非锚定版本")
    d = json.loads(p.read_text(encoding="utf-8"))
    cases = d.get("cases", d)
    return [len(cases), sum(1 for c in cases if c.get("should_hit"))]


DISPATCH = {
    "git_ls_count": do_git_ls_count,
    "git_dir_count": do_git_dir_count,
    "git_md_count": do_git_md_count,
    "git_last_dec": do_git_last_dec,
    "git_first_date": do_git_first_date,
    "bundle_count": do_bundle_count,
    "tar_bytes_mb": do_tar_bytes_mb,
    "json_calibration": do_json_calibration,
    "arithmetic": lambda spec: safe_arith(spec["expr"]),
}


def main():
    ap = argparse.ArgumentParser(description="v1.1 真值重算检查")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not TRUTH.exists():
        print(f"[ERR] 找不到 {TRUTH}", file=sys.stderr)
        return 2
    t = json.loads(TRUTH.read_text(encoding="utf-8"))
    items = t["items"]

    ok, drift, attested, broken = [], [], [], []
    for q in sorted(items, key=lambda s: (s[0], int("".join(c for c in s if c.isdigit()) or 0))):
        it = items[q]
        spec = it["recompute"]
        kind = spec["kind"]
        if kind == "attested":
            attested.append((q, spec.get("source", "")))
            continue
        try:
            got = DISPATCH[kind](spec)
        except (FileNotFoundError, ValueError, RuntimeError) as e:
            broken.append((q, kind, str(e)))
            continue
        (ok if close(got, it["v"]) else drift).append((q, it["v"], got))

    if args.json:
        print(json.dumps({"ok": len(ok), "drift": drift, "broken": broken,
                          "attested": [a[0] for a in attested], "n": len(items)},
                         ensure_ascii=False, indent=2))
    else:
        print(f"[recompute] 题库 v1.1 · 锚点 {t['anchor_ref']} · 共 {len(items)} 题")
        print(f"  ✅ 重算一致 {len(ok)}")
        if drift:
            print(f"  ❌ 漂移 {len(drift)}：")
            for q, want, got in drift:
                print(f"      {q}: 冻结 {want} → 重算 {got}")
        if broken:
            print(f"  ⛔ 锚点缺失/不可算 {len(broken)}：")
            for q, kind, err in broken:
                print(f"      {q}（{kind}）：{err}")
        print(f"  ⚠️  不可重算（attested，须人工复核）{len(attested)}：")
        for q, src in attested:
            print(f"      {q}  ← {src}")

    bad = len(drift) + len(broken)
    if bad:
        print(f"\n[FAIL] {bad} 项不能用于判分——先修题库（开 v1.2），不许出分", file=sys.stderr)
        return 1
    print(f"\n[PASS] {len(ok)} 项可重算且一致；{len(attested)} 项须人工复核（不计入通过）",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
