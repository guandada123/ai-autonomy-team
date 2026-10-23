#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
precheck.py —— 目标机（Mac mini M4 / macOS）环境自检

在迁移包解压后、【第一次运行任何东西之前】执行本脚本。
它不修改任何文件，只报告环境是否满足运行条件。

依赖：仅 Python 标准库。

用法：
    python3 precheck.py
    python3 precheck.py --json
"""

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

MIN_PY = (3, 9)
RESULTS = []


def rec(name, ok, detail, level="error"):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail, "level": level})
    return ok


def main():
    ap = argparse.ArgumentParser(description="目标机环境自检")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    here = Path(__file__).resolve().parent

    # 1. Python 版本
    v = sys.version_info
    rec(
        "Python 版本",
        v[:2] >= MIN_PY,
        f"{platform.python_version()}（要求 >= {MIN_PY[0]}.{MIN_PY[1]}）",
    )

    # 2. 平台
    rec(
        "平台",
        True,
        f"{platform.system()} {platform.machine()} | python={sys.executable}",
        level="info",
    )

    # 3. 硬裁判脚本存在
    judge = here / "judges" / "verify.py"
    rec("硬裁判脚本存在", judge.is_file(), str(judge))

    # 4. 硬裁判零第三方依赖（关键：目标机不能假定有 openpyxl 等）
    if judge.is_file():
        src = judge.read_text(encoding="utf-8", errors="replace")
        forbidden = [m for m in ("openpyxl", "docx", "pptx", "pdfkit", "pandas", "numpy", "reportlab")
                     if f"import {m}" in src or f"from {m}" in src]
        rec("硬裁判零第三方依赖", not forbidden,
            "未发现第三方 import" if not forbidden else f"发现: {forbidden}")

    # 5. 目录完整性（源机曾出现 5 个空目录导致包不可用）
    required = ["protocol", "prompts", "judges", "config", "fixtures", "docs"]
    missing = [d for d in required if not (here / d).is_dir()]
    empty = [d for d in required if (here / d).is_dir() and not any((here / d).iterdir())]
    rec("目录完整性", not missing and not empty,
        f"缺失={missing or '无'} 空目录={empty or '无'}")

    # 6. 写权限（自检目录本身，不污染交付物）
    try:
        probe = here / ".precheck_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        rec("目录可写", True, str(here))
    except OSError as e:
        rec("目录可写", False, f"{type(e).__name__}: {e}")

    # 7. 编码
    rec("默认编码", True, f"stdout={sys.stdout.encoding} | 文件系统={sys.getfilesystemencoding()}",
        level="info")

    # 8. shell
    rec("sh 可用", shutil.which("sh") is not None,
        shutil.which("sh") or "未找到（judges/run.sh 将不可用，可改用 python3 直调）")

    # 9. 换行基准（sha256 证据链跨平台一致性）
    rec("换行基准", True,
        "本包内文本文件统一 LF；写入必须显式 newline='\\n'，否则 sha256 证据链在 Windows/macOS 间不一致",
        level="info")

    # 9b. 写入域配置（第 8 查的前置）
    wd = here / "config" / "write_domains.json"
    if rec("写入域配置存在", wd.is_file(), str(wd) if wd.is_file() else "缺失：第 8 查无法运行"):
        try:
            cfg = json.loads(wd.read_text(encoding="utf-8"))
            deny = cfg.get("deny") or []
            allow = cfg.get("allow") or []
            rec("写入域配置可用", bool(deny) and bool(allow),
                f"allow={len(allow)} 条 / deny={len(deny)} 条")
        except (json.JSONDecodeError, OSError) as e:
            rec("写入域配置可用", False, f"{type(e).__name__}: {e}")

    # 9c. 分层判据（判据分层的前置）
    spec_dir = here / "fixtures" / "spec"
    for name in ("arithmetic.json", "adjudication.json"):
        rec(f"分层 spec · {name}", (spec_dir / name).is_file(), str(spec_dir / name))
    adj = spec_dir / "adjudication.json"
    if adj.is_file():
        try:
            data = json.loads(adj.read_text(encoding="utf-8"))
            leak = [it.get("id") for it in data.get("items", []) if "expected" in it]
            rec("判断项不含 expected（人类闸门完整性）", not leak,
                "全部合规" if not leak else f"★违规项：{leak} —— 判断项伪装成了机器真值")
        except (json.JSONDecodeError, OSError) as e:
            rec("判断项不含 expected（人类闸门完整性）", False, f"{type(e).__name__}: {e}")

    # 9d. 轮次与指标机制（本轮新增）
    for rel in ("judges/seat_check.py", "metrics.py", "new_round.py", "protocol/07-轮次与指标.md"):
        rec(f"轮次机制 · {rel}", (here / rel).is_file(), str(here / rel))

    if (here / "judges/seat_check.py").is_file() and (here / "config/params.json").is_file():
        r = subprocess.run(
            [sys.executable, str(here / "judges/seat_check.py"), "--lint-params",
             "--params", str(here / "config/params.json")],
            cwd=str(here), capture_output=True, text=True, encoding="utf-8")
        rec("params 键完整性（防「有阈值没定义」复发）", r.returncode == 0,
            (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "")

    wd = here / "config" / "write_domains.json"
    if wd.is_file():
        try:
            deny = json.loads(wd.read_text(encoding="utf-8")).get("deny") or []
            rec("写入域 deny 与协议文档一致", "judges/" in deny and "scripts/" not in deny,
                f"deny={deny}（包内对应目录是 judges/，不是 scripts/）")
        except (json.JSONDecodeError, OSError):
            pass

    # 9e. 读取域配置（第 9 查的前置）
    rd = here / "config" / "read_domains.json"
    if rec("读取域配置存在", rd.is_file(),
           str(rd) if rd.is_file() else "缺失：第 9 查（席位读取域）无法运行"):
        try:
            cfg = json.loads(rd.read_text(encoding="utf-8"))
            # 关键结构完整性：缺任一层都会让分类静默落到 unclassified
            need = ("deny_absolute", "deny_default", "allow")
            miss = [k for k in need if not cfg.get(k)]
            rec("读取域配置四层齐备", not miss,
                "deny_absolute / allow / deny_default / allow_extra_by_seat 齐备"
                if not miss else f"★缺层：{miss} —— 会导致分类静默落空")
            rec("读取域必禁项含 truth.json",
                any("truth.json" in p for p in (cfg.get("deny_absolute") or [])),
                "fixtures/truth.json 在 deny_absolute 内" if any(
                    "truth.json" in p for p in (cfg.get("deny_absolute") or []))
                else "★未禁真值文件 —— F5 等于没修")
        except (json.JSONDecodeError, OSError) as e:
            rec("读取域配置可用", False, f"{type(e).__name__}: {e}")

    if (here / "judges/seat_check.py").is_file() and rd.is_file():
        r = subprocess.run(
            [sys.executable, str(here / "judges/seat_check.py"), "--lint-read-domains",
             "--read-domains", str(rd)],
            cwd=str(here), capture_output=True, text=True, encoding="utf-8")
        rec("读取域配置可被加载（lint-read-domains）", r.returncode == 0,
            (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr) else "")

    # 10. 裁决材料齐备性（F20）
    # ★ 选轮逻辑不再用「round2 目录存在就用 round2」——那是把目录名当状态。
    _cand = []
    for d in sorted((here / "runs").glob("round*")):
        if (d / "answer-final.json").is_file():
            _cand.append(d)
    _latest = _cand[-1] if _cand else None
    # ★ F23：主待签 = 最新一轮；但**另外列出所有未签轮次**。
    #   只报最新一轮会让旧轮次的未签状态静默消失 —— 一旦开了第 3 轮，
    #   round2 的未签就再也不出现在预检里，等于「没签的轮次被新轮次盖住了」。
    _unsigned = [d.relative_to(here).as_posix() for d in _cand
                 if not (d / "adjudication-record.json").is_file()]
    if _latest is None:
        rec("裁决材料", False, "runs/ 下没有任何含 answer-final.json 的轮次", level="error")
    else:
        _rel = _latest.relative_to(here).as_posix()
        _tpl = _latest / "adjudication-record.TEMPLATE.md"
        _pen = _latest / "pending-adjudication.json"
        _signed = (_latest / "adjudication-record.json").is_file()
        rec(f"裁决模板存在（{_rel}）", _tpl.is_file(),
            "★模板是必交物：缺失说明没跑 --emit-record-template，"
            "签署时人会失去事实与选项对照" if not _tpl.is_file() else str(_tpl),
            level=("error" if not _tpl.is_file() else "info"))
        rec(f"待裁决清单存在（{_rel}）", _pen.is_file(),
            "★由 verify.py 生成；缺失说明未跑 pending 步骤" if not _pen.is_file()
            else str(_pen), level=("error" if not _pen.is_file() else "info"))
        if _signed:
            rec("人类裁决记录", True, f"最新轮次 {_rel} 已有 adjudication-record.json")
        else:
            rec("人类裁决记录", "unverified" == "yes",
                f"★待人类签名（当前待签轮次：{_rel}）。裁决记录须由人填写，"
                f"工具链不自动生成：参见 {_rel}/adjudication-record.TEMPLATE.md → "
                f"另存为 adjudication-record.json",
                level="manual")
        if _unsigned:
            # 注意：round1 是契约前产物，用已退役的三轴 spec，签不了也不该签。
            rec("未签轮次一览", True,
                f"未签：{', '.join(_unsigned)}"
                f"（其中 round1 为契约前产物、三轴 spec 已退役，不要求补签）",
                level="info")

    # 11. 关键提醒（无法自动验证的项）
    rec("跨家族模型权益", "unverified" == "yes",
        "★无法自动验证。请手动执行：python3 inspect_agent_routing.py --json（包根脚本；若报未找到会话缓存目录，加 --project-dir <~/.workbuddy/projects/<slug>）。本机 2026-10-20 已实测 PASS（glm-5.3-flash + hy4-preview）；换机/换账号后仍须重跑。★另：派席必须显式指定模型家族，禁用 default —— default 路由不保证跨家族。",
        level="manual")

    fails = [r for r in RESULTS if not r["ok"] and r["level"] == "error"]
    warns = [r for r in RESULTS if not r["ok"] and r["level"] != "error"]

    if args.json:
        print(json.dumps({"ok": not fails, "results": RESULTS}, ensure_ascii=False, indent=2))
        return 0 if not fails else 1

    print("=" * 68)
    print("目标机环境自检")
    print("=" * 68)
    for r in RESULTS:
        mark = "OK   " if r["ok"] else ("需人工" if r["level"] == "manual" else "FAIL ")
        print(f"  [{mark}] {r['name']}")
        print(f"           {r['detail']}")
    print("-" * 68)
    if fails:
        print(f"结果：不通过（{len(fails)} 项错误）。请先修复再继续。")
        return 1
    if warns:
        print(f"结果：通过，但有 {len(warns)} 项需人工确认（见上）。")
        return 0
    print("结果：全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
