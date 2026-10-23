#!/usr/bin/env python3
"""secret_patterns.py —— 密钥/凭据检测模式（单一真源）。

## 为什么单独成模块

此前这些模式在 `make_transfer.py` 与 `pre_release_check.py` **各有一份副本**，
改一处另一处不知道。2026-10-21 做「检测可证伪性」测试时暴露此问题：
注入 YAML 风格的 `api_key: "..."` 竟未被捕获——因为两份副本都只认 JSON 风格。

## 覆盖范围（四类载体）

| 载体 | 样例 | 备注 |
|---|---|---|
| JSON | `"api_key": "…"` / `"apiKey": "…"` | 原始覆盖 |
| YAML / ENV | `api_key: …` / `API_KEY=…` | **2026-10-21 新增**（原盲区） |
| 口令类 | `secret: "…"` / `password: "…"` | 新增 |
| 令牌前缀 | `Bearer …` / `sk-…` / `AKIA…` / 私钥块 | 原始覆盖 |

## 刻意不匹配

- 单词 `token` 单独出现（本项目文档大量讨论 **LLM token 用量**，如 `tokens: 126100`）。
  只匹配带 `Bearer` 前缀的 Bearer token，避免把成本预算误报为凭据泄露。

## 用法

    from secret_patterns import SECRET_PATTERNS, scan_text

    hits = scan_text(text)         # → [(label, 命中片段)]
"""
import re

SECRET_PATTERNS = [
    # ---- 令牌前缀 / 私钥 ----
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI 风格密钥 sk-…"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "私钥块"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-_.]{20,}"), "Bearer token"),

    # ---- JSON 风格 ----
    (re.compile(r'"apiKey"\s*:\s*"[^"]{12,}"'), "apiKey（JSON）"),
    (re.compile(r'"api_key"\s*:\s*"[^"]{12,}"'), "api_key（JSON）"),

    # ---- YAML / ENV / 通用赋值（2026-10-21 新增：原盲区）----
    (re.compile(r"""(?i)\bapi[_-]?key\s*[:=]\s*["']?[A-Za-z0-9_\-]{16,}"""),
     "api_key 赋值（YAML/ENV/通用）"),

    # ---- 口令类（要求带引号且值 ≥8，降低误报）----
    (re.compile(r"""(?i)\b(?:secret|password|passwd|pwd)\s*[:=]\s*["'][^"']{8,}["']"""),
     "口令类赋值"),
]


def scan_text(text):
    """对单段文本做全模式扫描，返回 [(label, 命中片段前 24 字符)]。"""
    hits = []
    for pat, label in SECRET_PATTERNS:
        for m in pat.finditer(text):
            hits.append((label, m.group(0)[:24]))
    return hits


def scan_file(p, max_bytes=2_000_000):
    """对单个文件扫描；读失败返回空列表（不因单文件异常中断全量扫描）。"""
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return scan_text(text[:max_bytes])
