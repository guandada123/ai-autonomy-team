#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inspect_agent_routing.py —— 子代理模型路由核验器

用途：判定「子代理实际被路由到哪个模型家族」，用于验证双裁判架构中
      软裁判（跨家族评委）是否真实可用。
原理：不采信子代理自述（模型未必知道自己被路由到哪），只读取运行时记录
      的 providerData 字段。

依赖：仅 Python 标准库。可在 Windows / macOS / Linux 运行。

用法：
    python3 inspect_agent_routing.py                 # 自动探测当前项目
    python3 inspect_agent_routing.py --project-dir <path>
    python3 inspect_agent_routing.py --filter af638059
    python3 inspect_agent_routing.py --json          # 机器可读输出
"""

import argparse
import json
import os
import sys
from pathlib import Path

# 关注的字段名（出现即提取）
KEYS_OF_INTEREST = {
    "model",
    "requestModelId",
    "requestModelName",
    "agent",
    "provider",
}

# 厂商家族识别规则：关键字 -> 家族名
FAMILY_RULES = [
    ("deepseek", "DeepSeek"),
    ("glm", "智谱 GLM"),
    ("chatglm", "智谱 GLM"),
    ("kimi", "月之暗面 Kimi"),
    ("moonshot", "月之暗面 Kimi"),
    ("hy3", "腾讯混元"),
    ("hy4", "腾讯混元"),
    ("hunyuan", "腾讯混元"),
    ("minimax", "MiniMax"),
    ("abab", "MiniMax"),
    ("claude", "Anthropic Claude"),
    ("gpt", "OpenAI"),
    ("o1", "OpenAI"),
    ("o3", "OpenAI"),
    ("gemini", "Google"),
    ("qwen", "阿里通义"),
    ("ernie", "百度文心"),
]


def detect_family(model_id):
    """从模型标识推断厂商家族。返回 (家族, 置信标记)。"""
    if not model_id:
        return ("UNKNOWN", "无标识")
    low = str(model_id).lower()
    for kw, fam in FAMILY_RULES:
        if kw in low:
            return (fam, "推断")
    return ("UNKNOWN", "未收录规则")


def default_project_dir():
    """按当前工作目录推断 projects 缓存路径。"""
    cwd = Path.cwd().resolve()
    # 会话缓存目录名把路径中的分隔符与冒号替换成连字符
    slug = str(cwd).replace(":", "-").replace("\\", "-").replace("/", "-").lstrip("-")
    base = Path.home() / ".workbuddy" / "projects"
    # 同时尝试两种 slug 形态（大小写 / 前导斜杠差异）
    candidates = [
        base / ("c-" + slug) if os.name == "nt" else base / slug,
        base / slug,
    ]
    for c in candidates:
        if c.is_dir():
            return c
    # 兜底：扫描 base 下名字包含最后两级目录名的候选项
    if base.is_dir():
        tail = cwd.name.lower()
        for d in base.iterdir():
            if d.is_dir() and tail in d.name.lower():
                # 选择最近修改的
                return d
    return None


def walk_extract(obj, path="", out=None):
    """递归提取关注字段。"""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in KEYS_OF_INTEREST and not isinstance(v, (dict, list)):
                out.append((path + k, v))
            walk_extract(v, path + k + ".", out)
    elif isinstance(obj, list):
        for item in obj:
            walk_extract(item, path, out)
    return out


def scan_subagent_file(fp):
    """扫描单个子代理 jsonl，返回 (模型集合, 原始字段字典)。"""
    models = set()
    fields = {}
    try:
        with open(fp, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "providerData" not in line and "requestModelId" not in line:
                    continue
                try:
                    d = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                for p, v in walk_extract(d):
                    fields.setdefault(p, v)
                    if p.endswith("requestModelId") or p.endswith(".model") or p == "model":
                        models.add(str(v))
    except OSError as e:
        return (set(), {"_error": str(e)})
    return (models, fields)


def collect(project_dir, name_filter=None):
    """收集子代理记录。兼容两种层级：

      1) 项目目录  <proj>/<session>/subagents/agent-*.jsonl
      2) 会话目录  <session>/subagents/agent-*.jsonl
    """
    rows = []
    root = Path(project_dir) if project_dir else None
    if root is None or not root.is_dir():
        return rows

    # 层级 2：传入的目录本身就是会话目录
    if (root / "subagents").is_dir():
        sessions = [root]
    else:
        # 层级 1：传入的是项目目录
        sessions = [d for d in sorted(root.iterdir()) if (d / "subagents").is_dir()]

    for sess in sessions:
        sub_dir = sess / "subagents"
        for fp in sorted(sub_dir.glob("agent-*.jsonl")):
            if name_filter and name_filter not in fp.name:
                continue
            models, fields = scan_subagent_file(fp)
            rows.append(
                {
                    "session": sess.name,
                    "file": fp.name,
                    "agent_id": fp.stem,
                    "models": sorted(models),
                    "raw": fields,
                }
            )
    return rows


def main():
    ap = argparse.ArgumentParser(description="子代理模型路由核验器")
    ap.add_argument("--project-dir", default=None, help="会话缓存目录（默认自动探测）")
    ap.add_argument("--filter", default=None, help="只处理文件名包含该串的子代理")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    proj = Path(args.project_dir) if args.project_dir else default_project_dir()
    if proj is None or not proj.is_dir():
        print("[ERR] 未找到会话缓存目录，请用 --project-dir 指定", file=sys.stderr)
        return 2

    rows = collect(proj, args.filter)
    if not rows:
        print(f"[ERR] 在 {proj} 下未找到任何子代理记录", file=sys.stderr)
        return 2

    # 家族汇总
    all_models = set()
    for r in rows:
        all_models.update(r["models"])
    families = {}
    for m in sorted(all_models):
        fam, _ = detect_family(m)
        families.setdefault(fam, []).append(m)

    verdict = "SKIP"
    note = ""
    non_unknown = {f: ms for f, ms in families.items() if f != "UNKNOWN"}
    if len(non_unknown) >= 2:
        verdict = "PASS"
        note = f"检出 {len(non_unknown)} 个不同家族，跨家族路由可用"
    elif len(non_unknown) == 1:
        verdict = "FAIL"
        note = "仅检出单一家族，跨家族路由不可用"
    else:
        note = "无法从记录中识别家族"

    if args.json:
        print(
            json.dumps(
                {
                    "project_dir": str(proj),
                    "verdict": verdict,
                    "note": note,
                    "families": families,
                    "subagents": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"扫描目录: {proj}")
    print(f"子代理记录: {len(rows)} 个")
    print("-" * 72)
    for r in rows:
        ms = ", ".join(r["models"]) if r["models"] else "(无 providerData 记录)"
        print(f"  {r['agent_id'][:44]:46s} {ms}")
    print("-" * 72)
    print("家族分布:")
    for fam in sorted(families):
        print(f"  {fam:16s} <- {', '.join(families[fam])}")
    print("-" * 72)
    print(f"判定: {verdict}  {note}")
    if verdict == "FAIL":
        print("降级动作: 软裁判权重=0，权重全部转移到硬裁判；写入独立性折扣记录。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
