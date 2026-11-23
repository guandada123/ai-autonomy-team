#!/usr/bin/env python3
"""check_py39_floor.py —— 守住「声明最低版本」与实现之间的边界。

## 为什么存在

`DEPENDENCIES.md` 承诺「只要有 Python 3.9+，clone 下来就能跑」，`regress` 也有一项
「py39 兼容」。但实测 2026-11-23：全仓 15 处 `Path.write_text(..., newline=)`
（该参数 3.10 才有），3.9.6 下 `audit/kit` 自检直接红 2 项。

原来那项 regress 只查 `match` / `X | Y` 联合类型这类**语法**，查不到「标准库 API 的版本差异」——
于是声明在前、实现漂了整整没人管。本脚本补的就是这一块：**把版本边界变成一条可跑的检查**，
而不是靠谁某天想起用 3.9 试一次。

## 只查「能静态确定」的少数几条

宁可漏，也不误报——误报会让人把检查关掉，那时连这层网都没了。当前清单：

| 判据 | 引入版本 | 说明 |
|---|---|---|
| `write_text` / `read_text` 带 `newline=` | 3.10 | 本次事故本体 |
| `zip(..., strict=)` | 3.10 | 常见坑 |
| `itertools.pairwise` | 3.10 | — |
| `int.bit_count` | 3.10 | — |
| `match` 语句 | 3.10 | 语法级（regress 已查，这里重复一次不亏） |
| `except*`（TryStar） | 3.11 | — |

`open(..., newline=)` **不在**清单里：它从 Python 2 起就有，3.9 完全支持。

## 本检查覆盖不到什么（别把它当"3.9 一定没问题"的凭据）

只覆盖**静态可判**的 6 类。**属性访问类**越线静态判不出来 —— 典型例子是
`ast.Match`：3.9 的 `ast` 模块根本没有这个属性，但语法完全合法，本脚本扫不出来。
首版就栽在这里（自己崩在 3.9），所以 CI（只跑 3.13）绿 ≠ 3.9 绿。
要确认下限，仍需在 3.9 上实跑一次：`/usr/bin/python3 scripts/check_py39_floor.py`。

## 用法

    python3 scripts/check_py39_floor.py              # 扫默认范围
    python3 scripts/check_py39_floor.py --root DIR   # 扫指定目录（自检用）
    python3 scripts/check_py39_floor.py --self-test  # 正负样本自检

退出码：0 干净 / 1 命中 / 2 环境错误
"""

import argparse
import ast
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = ["scripts", "audit", "tools", "migration-package"]
SKIP_PARTS = {"__pycache__", "_trash", "snapshots", ".git"}
# 运行数据不是交付源码：build_package 与 ruff 都排除 migration-package/runs，
# 里面还有故意写坏的负样本 fixture（3.9 下 SyntaxError 会误报成越线）。
SKIP_PREFIXES = ("migration-package/runs",)

# 3.10+ 才有 newline 参数的两个方法；open() 是 3.9 就支持的，不在判据里
TEXT_IO_WITH_NEWLINE = {"write_text", "read_text"}
# 属性名即判据（attribute 调用）
ATTR_FLOOR = {
    "pairwise": "3.10（itertools.pairwise）",
    "bit_count": "3.10（int.bit_count）",
}
# 本脚本要在 3.9 上也能跑（它就是替 3.9 把关的），所以新语法节点必须 getattr 兜底：
# 直接写 ast.Match 会在 3.9 抛 AttributeError —— 首版就栽在这，属自证其罪。
MATCH_NODE = getattr(ast, "Match", None)


def _iter_py(base):
    for d in SCAN_DIRS:
        p = base / d
        if p.is_file() and p.suffix == ".py":
            yield p
            continue
        if not p.is_dir():
            continue
        for f in sorted(p.rglob("*.py")):
            if any(part in SKIP_PARTS for part in f.parts):
                continue
            if any(part.startswith(".") for part in f.parts):
                continue
            rel = f.relative_to(base).as_posix()
            if any(rel.startswith(pref + "/") or rel == pref for pref in SKIP_PREFIXES):
                continue
            yield f


def scan(base):
    """返回 [(相对路径, 行号, 原因)]，按文件、行号排序。"""
    base = Path(base)
    hits = []
    for f in _iter_py(base):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            hits.append((str(f.relative_to(base)), 0, f"无法解析：{type(exc).__name__}"))
            continue
        for node in ast.walk(tree):
            # ① Path.write_text/read_text(..., newline=...)：3.10 加的形参
            if isinstance(node, ast.Call):
                func = node.func
                attr = func.attr if isinstance(func, ast.Attribute) else None
                name = attr or getattr(func, "id", "")
                if attr in TEXT_IO_WITH_NEWLINE and any(k.arg == "newline" for k in node.keywords):
                    hits.append(
                        (
                            str(f.relative_to(base)),
                            node.lineno,
                            f"{attr}(newline=) 需 3.10+；改 open(..., newline=) 或 write_bytes",
                        )
                    )
                # ② zip(..., strict=)：3.10。注意 zip 通常是 Name 而非 Attribute，
                #    只查 Attribute 会漏（自检当场抓到过）。
                if name == "zip" and any(k.arg == "strict" for k in node.keywords):
                    hits.append((str(f.relative_to(base)), node.lineno, "zip(strict=) 需 3.10+"))
                # ③ 少数「属性即判据」的 API
                if attr in ATTR_FLOOR:
                    hits.append(
                        (
                            str(f.relative_to(base)),
                            node.lineno,
                            f"{attr} 需 {ATTR_FLOOR[attr]}",
                        )
                    )
            # ④ match 语句（3.10）。3.9 的 ast 没这个类，必须 getattr 兜底；
            #    3.9 上这类文件连 parse 都过不了，由上面的 SyntaxError 分支记「无法解析」。
            if MATCH_NODE is not None and isinstance(node, MATCH_NODE):
                hits.append((str(f.relative_to(base)), node.lineno, "match 语句需 3.10+"))
            # ⑤ except*（3.11）——3.9 的 ast 里没有这个节点类型，用 getattr 兜底
            try_star = getattr(ast, "TryStar", None)
            if try_star is not None and isinstance(node, try_star):
                hits.append((str(f.relative_to(base)), node.lineno, "except* 需 3.11+"))
    return sorted(set(hits))


def self_test():
    n_ok, fails = 0, []

    def expect(label, cond, detail=""):
        nonlocal n_ok
        if cond:
            n_ok += 1
            print(f"  [PASS] {label}" + (f"  {detail}" if detail else ""))
        else:
            fails.append(label)
            print(f"  [FAIL] {label}  {detail}")

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        (base / "scripts").mkdir()

        # 负样本（干净）：open 的 newline 不是问题，write_bytes 也不是
        (base / "scripts" / "clean.py").write_text(
            'with open("a", "w", encoding="utf-8", newline="\\n") as f:\n'
            '    f.write("x")\n'
            "def g(p, s):\n"
            '    p.write_bytes(s.encode("utf-8"))\n'
            "def h(a, b):\n"
            "    return list(zip(a, b))\n",
            encoding="utf-8",
        )
        expect("负样本：open(newline=) / write_bytes / zip 无 strict → 0 命中", scan(base) == [])

        # 正样本：逐条判据各造一例。match 单列一个文件 —— 3.9 下它连 parse 都过不了，
        # 混在一起会让其余命中全部消失（因为整文件解析失败），反而测不出东西。
        (base / "scripts" / "bad.py").write_text(
            "from pathlib import Path\n"
            'Path("x").write_text("a", encoding="utf-8", newline="\\n")\n'
            'Path("x").read_text(encoding="utf-8", newline="\\n")\n'
            "def z(a, b):\n"
            "    return list(zip(a, b, strict=True))\n"
            "import itertools\n"
            "def p(xs):\n"
            "    return list(itertools.pairwise(xs))\n"
            "def c(n):\n"
            "    return n.bit_count()\n",
            encoding="utf-8",
        )
        (base / "scripts" / "matchcase.py").write_text(
            "def m(v):\n    match v:\n        case 1:\n            return 1\n    return 0\n",
            encoding="utf-8",
        )
        got = scan(base)
        reasons = " | ".join(r for _, _, r in got)
        expect(
            "正样本：write_text(newline=) 命中", any("write_text" in r for r in [x[2] for x in got])
        )
        expect(
            "正样本：read_text(newline=) 命中", any("read_text" in r for r in [x[2] for x in got])
        )
        expect("正样本：zip(strict=) 命中", "zip(strict=)" in reasons)
        expect("正样本：itertools.pairwise 命中", "pairwise" in reasons)
        expect("正样本：bit_count 命中", "bit_count" in reasons)
        if MATCH_NODE is not None:
            expect("正样本：match 语句命中", "match" in reasons)
        else:
            # 3.9 跑本脚本时：match 文件根本解析不了 → 必须记成命中，不许静默跳过
            expect(
                "3.9 下 match 语法解析失败 → 记为命中（不静默跳过）",
                any("无法解析" in r and "matchcase" in p for p, _, r in got),
                reasons,
            )
        # 解析失败的条目是文件级、没有行号，单独排除；其余必须能定位到行
        expect(
            "正样本：行号非零且可定位",
            all(ln > 0 for _, ln, r in got if "无法解析" not in r),
            str(got[:2]),
        )

        # 反证：把坏行改成等价 3.9 写法 → 必须归零（防「永久红」式假绿）
        (base / "scripts" / "bad.py").write_text(
            "from pathlib import Path\n"
            "def w(p, s):\n"
            "    with open(p, 'w', encoding='utf-8', newline='\\n') as f:\n"
            "        f.write(s)\n",
            encoding="utf-8",
        )
        # matchcase.py 也要一并改成干净内容，否则反证永远测不出归零
        (base / "scripts" / "matchcase.py").write_text(
            "def m(v):\n    return 1 if v == 1 else 0\n", encoding="utf-8"
        )
        expect("反证：全部改成 3.9 写法后归零", scan(base) == [], str(scan(base)))

    print()
    if fails:
        print(f"[FAIL] 自检未通过：{len(fails)}/{n_ok + len(fails)} 项失败")
        for f in fails:
            print(f"  · {f}")
        return 1
    print(f"[PASS] 自检通过：{n_ok}/{n_ok} 项")
    return 0


def main():
    ap = argparse.ArgumentParser(description="声明最低 Python 版本 vs 实现的静态边界检查")
    ap.add_argument("--root", default=None, help="扫描根目录（默认仓根）")
    ap.add_argument("--self-test", action="store_true", help="正负样本自检")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    base = Path(args.root) if args.root else ROOT
    if not base.is_dir():
        print(f"[ERR] 根目录不存在：{base}", file=sys.stderr)
        return 2
    hits = scan(base)
    if not hits:
        print(f"[OK] 未发现 3.10+ 标准库用法（扫描 {base.name}：{'/'.join(SCAN_DIRS)}）")
        return 0
    print(f"[FAIL] 发现 {len(hits)} 处超出声明下限（Python 3.9）的用法：")
    for rel, ln, why in hits:
        print(f"  · {rel}:{ln} —— {why}")
    print("\n处置：改成 3.9 可用写法（如 open(..., newline=) 或 write_bytes），")
    print("      或先把 DEPENDENCIES.md 的下限改掉再改这里 —— 别让两者继续各说各话。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
