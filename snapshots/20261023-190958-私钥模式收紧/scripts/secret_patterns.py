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
| **代码托管/云厂商令牌** | `ghp_…` / `github_pat_…` / `glpat-…` / `xox?-…` / `AKID…` / `LTAI…` | **2026-10-23 新增**（原盲区，见下） |

## 为什么补「代码托管令牌」（2026-10-23）

要往私有远端推仓库前，按「0 命中须先反证」做正样本测试：
拿 `ghp_` + 36 个字符去撞，**8 条模式一条都不命中**。也就是说
「历史扫描 0 命中」并不构成证明 —— 检测器连 GitHub PAT 都认不出。

补了 GitHub（classic / fine-grained）、GitLab、Slack、腾讯云（`AKID`）、
阿里云（`LTAI`）五类前缀。仍只收**高置信度前缀**：不做「32 位 hex」这类
宽匹配，否则会把 git 的 40 位 commit sha 全报成密钥。

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
    # 2026-10-23 收紧：只认「表头 + ≥100 字符正文」。裸表头不含任何密钥material，
    # 却会被快照/测试数据误命中（实测：本仓一个历史快照因此被拦推）。
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/=\r\n]{100,}"),
     "私钥块（表头+正文）"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9\-_.]{20,}"), "Bearer token"),

    # ---- 代码托管 / 云厂商令牌（2026-10-23 新增：原盲区）----
    # 只收带固定前缀的高置信度形态；不做「长 hex」宽匹配（会命中 commit sha）。
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b"), "GitHub token ghp_/gho_…"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b"), "GitHub fine-grained PAT"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b"), "GitLab PAT glpat-"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "Slack token xox?-"),
    (re.compile(r"\bAKID[A-Za-z0-9]{13,}\b"), "腾讯云 SecretId AKID…"),
    (re.compile(r"\bLTAI[A-Za-z0-9]{12,}\b"), "阿里云 AccessKey LTAI…"),

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
