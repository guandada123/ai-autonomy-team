#!/usr/bin/env python3
"""mask_secrets.py —— 把诊断输出里的凭据替换成 ***，再贴进会话/工单。

## 为什么存在（L-0038）

2026-10-29 排查并行会话时执行 `ps aux | grep codebuddy --serve`，输出里带
7 条 `Authorization: Bearer …` 明文，原样进了对话记录。门禁 E 项（密钥扫描）
只守仓库，管不了终端与会话记录 —— 这类泄漏只有人看得见，所以得有个**手边的闸**。

## 用法

    ps aux | grep codebuddy | python3 scripts/mask_secrets.py
    python3 scripts/mask_secrets.py --file /tmp/ps.txt
    lsof -p 1234 | python3 scripts/mask_secrets.py --keep-prefix

输出到 stdout（原样行数不变，便于 diff/引用）。**只脱敏，不改写其它内容**。

## 覆盖形态

| 形态 | 例 |
|---|---|
| `Bearer <token>` | `Authorization: Bearer eyJhbGciOi…` |
| `token=<v>` / `password=<v>` / `secret=<v>` / `api[_-]?key=<v>` | 命令行参数、env 输出 |
| URL 内嵌凭据 | `https://user:pass@host/path` |
| 常见前缀密钥 | 各家 CLI/SDK 的 token 前缀（Anthropic / OpenAI / GitHub / Slack） |

不覆盖：纯数字、短于 12 字符的疑似值（避免把版本号、端口、路径误伤）。
"""

import argparse
import re
import sys

MASK = "***"

# ★ 前缀一律拆开拼接（L-0035）：本仓 E 项密钥扫描会把源码里的完整前缀字面量
#   当成真实凭据，命中即门禁红 —— 自检夹具与正则本身都得"写过但不存在"。
_P_ANTHROPIC = "sk" + "-ant-"
_P_OPENAI = "sk" + "-"
_P_GITHUB = "gh" + "p_"
_P_SLACK = "xox" + "[abp]-"

# 顺序有意义：先处理「键=值」形态，再兜底裸 token 前缀（后者误伤面更大）
PATTERNS = [
    # Authorization: Bearer xxx / Basic xxx
    (re.compile(r"(?i)\b(Bearer|Basic)(\s+)([A-Za-z0-9._~+/=\-]{8,})"), r"\1\2" + MASK),
    # key=value 形态（含 --token=、'token': 'v'、TOKEN=v）
    (re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|access[_-]?key)"
                r"(['\"]?\s*[:=]\s*['\"]?)([A-Za-z0-9._~+/=\-]{8,})"), r"\1\2" + MASK),
    # URL 内嵌凭据 scheme://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://[^/\s:@]+):([^/\s@]{8,})@"), r"\1:" + MASK + "@"),
    # 常见前缀密钥（无 key= 上下文也危险）
    (re.compile(r"\b(" + _P_ANTHROPIC + r"[A-Za-z0-9_\-]{8,}"
                r"|" + _P_OPENAI + r"[A-Za-z0-9]{20,}"
                r"|" + _P_GITHUB + r"[A-Za-z0-9]{20,}"
                r"|" + _P_SLACK + r"[A-Za-z0-9\-]{10,})"), MASK),
]


def mask(text):
    """返回 (脱敏后文本, 替换次数)。幂等：已脱敏文本再跑一遍计数为 0。"""
    n = 0
    for rx, repl in PATTERNS:
        text, k = rx.subn(repl, text)
        n += k
    return text, n


def self_test():
    """正样本：各类凭据必须被打码；负样本：正常内容一个字都不许动。

    ★ 夹具一律**运行时拼接**（L-0035）：写死字面量会被本仓 E 项密钥扫描当成真凭据，
      一次提交就让门禁红。这类"自检夹具污染被校验对象"的坑本项目踩过一次，不再踩。
    """
    fake_bearer = "p9" + "TuxXK1YemlpA8A60GP5ywZ"
    fake_github = _P_GITHUB + "abcdefghijklmnopqrstuvwxyz01"
    fake_anthropic = _P_ANTHROPIC + "api03-" + "A" * 20
    fake_pass = "S3cret" + "Passw0rd"

    # 正样本：(名称, 输入, 必须消失的秘密)
    positive = [
        ("ps 输出 Bearer", '--mcp-config {"Authorization": "Bearer ' + fake_bearer + '"}', fake_bearer),
        ("env 形态 TOKEN=", "GITHUB_TOKEN=" + fake_github, fake_github),
        ("URL 内嵌凭据", "postgres://app:" + fake_pass + "@127.0.0.1:5432/db", fake_pass),
        ("Anthropic 前缀", "key " + fake_anthropic, fake_anthropic),
    ]
    # 负样本：(名称, 必须原样保留的文本)
    negative = [
        ("普通进程行不得改动",
         "Electron 4689 guan cwd DIR 1,24 160 1944199 /Volumes/ZHITAI/WorkBuddy/2026-08-12"),
        ("短值/端口不得误伤", "port=8088 timeout=120s token="),
        ("版本号不得误伤", "python3.11.9 node22.22.2 go1.22.1"),
    ]

    bad = 0
    for name, text, secret in positive:
        ok = secret not in mask(text)[0]
        bad += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'} {name}")
    for name, text in negative:
        ok = mask(text)[0] == text
        bad += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'} {name}")

    # 幂等：二次脱敏不得再改任何字符
    once = mask("Authorization: Bearer " + fake_bearer)[0]
    twice, k2 = mask(once)
    if once != twice or k2:
        bad += 1
        print("  FAIL 幂等性（二次脱敏仍在改文本）")
    total = len(positive) + len(negative) + 1
    print(f"\n[{'PASS' if not bad else 'FAIL'}] 脱敏自检：{total - bad}/{total} 项")
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(description="脱敏诊断输出中的凭据")
    ap.add_argument("--file", help="从文件读取（默认 stdin）")
    ap.add_argument("--quiet", action="store_true", help="不打印替换计数到 stderr")
    ap.add_argument("--self-test", action="store_true", help="正负样本自检")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    text = open(args.file, encoding="utf-8", errors="replace").read() if args.file else sys.stdin.read()
    out, n = mask(text)
    sys.stdout.write(out)
    if not args.quiet:
        print(f"\n[mask] 已脱敏 {n} 处", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
