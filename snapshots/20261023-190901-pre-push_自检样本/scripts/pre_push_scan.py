#!/usr/bin/env python3
"""pre_push_scan.py —— 推远端之前的闸门（密钥历史扫描 + 远端权限核对）。

## 为什么需要它

2026-10-23 第一次把本仓库推到私有远端时，按流程先扫「全部可达历史」得 0 命中。
但按「检测须可证伪」复核了一遍：拿 `ghp_` + 36 字符去撞，**8 条模式一条都不命中** ——
也就是说那个 0 命中是检测器瞎，不是库干净。补了 GitHub/GitLab/Slack/腾讯云/阿里云
六类前缀后重扫才是真的 0。

这件事说明：**「扫过了，很干净」这句话本身不可信**，除非能先证明检测器会命中。
所以本脚本把两件事绑成一条命令：

  1. **先自检**：每个模式必须配一个正样本，逐个验证「它真的会命中」；
     任一条模式没有正样本 → 直接 rc=2（不扫描、不放行）。
  2. **再扫描**：全部可达 blob（不是只看工作区——推上去的数据会离开本机）。
  3. **再核权限**：远端必须 private。误设 public 是不可完全撤回的操作（D-0045）。

## 用法

    python3 scripts/pre_push_scan.py                 # 手动跑（= make pre-push）
    python3 scripts/pre_push_scan.py --self-test     # 只跑检测器自检
    python3 scripts/pre_push_scan.py --install-hook  # 装 .git/hooks/pre-push
    python3 scripts/pre_push_scan.py --hook          # 供 hook 调用（读 stdin，静默成功）

## 退出码

    0 干净（可推） / 1 有命中或远端非私有（拒绝） / 2 环境错误或自检失败
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from secret_patterns import SECRET_PATTERNS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

OK, BAD, ERR = 0, 1, 2

# ── 每个模式的正样本（缺一个就拒绝扫描）──────────────────────────────
# 键必须与 secret_patterns.SECRET_PATTERNS 的 label 完全一致。
# 新增模式忘了配样本 → 本脚本 rc=2，逼着补上：没法证明会命中的检测器等于没有。
#
# ★ 正样本一律**拼接**，不要写成整串字面量：否则本文件自己会被密钥扫描命中
#   （2026-10-23 实测：`-----BEGIN … PRIVATE KEY-----` 写成整串 → E 项当场红）。
PROBES = {
    "OpenAI 风格密钥 sk-…": "sk-" + "a" * 24,
    "AWS Access Key": "AKIA" + "A1B2C3D4E5F6G7H8",
    "私钥块": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "Bearer token": "Authorization: Bearer " + "a" * 24,
    "apiKey（JSON）": '{"apiKey": "' + "a" * 16 + '"}',
    "api_key（JSON）": '{"api_key": "' + "a" * 16 + '"}',
    "api_key 赋值（YAML/ENV/通用）": "api_key: " + "a" * 20,
    "口令类赋值": 'password: "' + "a" * 10 + '"',
    "GitHub token ghp_/gho_…": "ghp_" + "A1b2C3d4" * 5,
    "GitHub fine-grained PAT": "github_pat_" + "a" * 60,
    "GitLab PAT glpat-": "glpat-" + "a" * 24,
    "Slack token xox?-": "xoxb-" + "1234567890-abcd",
    "腾讯云 SecretId AKID…": "AKID" + "a" * 20,
    "阿里云 AccessKey LTAI…": "LTAI" + "a" * 16,
}

# 负样本：绝不能被判成密钥，否则误报会把人训练成"忽略告警"
NEGATIVES = [
    ("40 位 commit sha", "5a3c9f1e8b7d6c5a4f3e2d1c0b9a8f7e6d5c4b3a"),
    ("token 用量数字", "这一轮 token 用量 126100，成本 0.42 元"),
    ("正文提到 api_key 这个词", "把 api_key 写进 .env，不要提交"),
]


def pattern_self_test(verbose=True):
    """逐条模式验证「会命中」，并验证负样本不误报。返回 (ok, 说明行)。"""
    lines, ok = [], True
    labels = {label for _, label in SECRET_PATTERNS}
    missing = sorted(labels - set(PROBES))
    extra = sorted(set(PROBES) - labels)
    if missing:
        ok = False
        lines.append(f"[FAIL] 这些模式没有正样本，无法证明会命中：{missing}")
    if extra:
        ok = False
        lines.append(f"[FAIL] 这些正样本没有对应模式（名字写错了？）：{extra}")
    for rx, label in SECRET_PATTERNS:
        probe = PROBES.get(label)
        if probe is None:
            continue
        if not rx.search(probe):
            ok = False
            lines.append(f"[FAIL] {label} 抓不到自己的正样本")
        else:
            lines.append(f"  [ok] {label}")
    for why, text in NEGATIVES:
        hit = [label for rx, label in SECRET_PATTERNS if rx.search(text)]
        if hit:
            ok = False
            lines.append(f"[FAIL] 负样本误报（{why}）→ {hit}")
    if verbose:
        print("\n".join(lines))
    return ok, lines


def _git(*args, **kw):
    return subprocess.run(["git", *args], capture_output=True, text=True, cwd=str(ROOT), **kw)


def _git_bytes(*args):
    """取原始字节：`cat-file` 会吐出二进制 blob（图片/压缩包），不能按 UTF-8 解码。"""
    return subprocess.run(["git", *args], capture_output=True, cwd=str(ROOT)).stdout


def reachable_blobs():
    """返回 {oid: path}，覆盖全部可达历史对象。"""
    out = _git("rev-list", "--objects", "--all").stdout.splitlines()
    blobs = {}
    for ln in out:
        parts = ln.split(" ", 1)
        if len(parts) == 2:
            blobs.setdefault(parts[0], parts[1])
    return blobs


def scan_history():
    """扫描全部可达 blob。返回 [(path, label, 片段前 12 字符)]。"""
    hits = []
    for oid, path in reachable_blobs().items():
        text = _git_bytes("cat-file", "-p", oid).decode("utf-8", "ignore")
        for rx, label in SECRET_PATTERNS:
            m = rx.search(text)
            if m:
                hits.append((path, label, m.group(0)[:12]))
                break
    return hits


def check_remote_visibility():
    """远端必须 private。返回 (状态, 说明)；状态 ∈ {ok, public, unknown}。"""
    remotes = [ln.split()[1] for ln in _git("remote", "-v").stdout.splitlines()
               if ln.split()[0:1] == ["origin"] and ln.rstrip().endswith("(push)")]
    if not remotes:
        return "ok", "无 origin 远端（本地仓库，无需核对）"
    url = remotes[0]
    if "github.com" not in url:
        return "unknown", f"非 GitHub 远端，无法自动核对权限：{url}"
    try:
        r = subprocess.run(["gh", "repo", "view", "--json", "visibility"], capture_output=True,
                           text=True, timeout=20, cwd=str(ROOT))
        if r.returncode != 0:
            return "unknown", f"gh 调用失败（离线或未登录？）：{r.stderr.strip()[:80]}"
        vis = json.loads(r.stdout).get("visibility", "")
        if vis == "PRIVATE":
            return "ok", f"{url} → PRIVATE"
        return "public", f"{url} → {vis or '未知'}（应为 PRIVATE）"
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        return "unknown", f"核对失败：{type(e).__name__}"


def main():
    ap = argparse.ArgumentParser(description="推远端前的闸门：密钥历史扫描 + 远端权限")
    ap.add_argument("--self-test", action="store_true", help="只跑检测器自检")
    ap.add_argument("--install-hook", action="store_true", help="安装 .git/hooks/pre-push")
    ap.add_argument("--hook", action="store_true", help="供 hook 调用（stdin 忽略，安静模式）")
    args = ap.parse_args()

    if args.install_hook:
        hook = ROOT / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(
            "#!/bin/sh\n"
            "# 由 scripts/pre_push_scan.py --install-hook 生成（本地文件，不入库）\n"
            "# 作用：推之前扫全部历史 + 核对远端是否 private（D-0045）\n"
            "exec python3 scripts/pre_push_scan.py --hook\n",
            encoding="utf-8")
        hook.chmod(0o755)
        print(f"[OK] 已安装 {hook.relative_to(ROOT)}")
        print("     ⚠️ hook 是本地文件、不入库：换机 clone 后需重跑 make hooks")
        return OK

    loud = not args.hook

    # ① 检测器自检 —— 不能关：扫得干净的前提是它真能扫出东西
    ok, lines = pattern_self_test(verbose=False)
    if args.self_test:
        print("\n".join(lines))
        print(f"[{'PASS' if ok else 'FAIL'}] 检测器自检：{len(PROBES)} 条模式")
        return OK if ok else ERR
    if not ok:
        print("[FAIL] 检测器自检未通过，拒绝以「扫过了很干净」的名义放行：", file=sys.stderr)
        print("\n".join(lines), file=sys.stderr)
        return ERR
    if loud:
        print(f"[1/3] 检测器自检：{len(PROBES)} 条模式全部能命中自己的正样本", flush=True)

    # ② 历史扫描
    hits = scan_history()
    if loud:
        print(f"[2/3] 历史扫描：{len(reachable_blobs())} 个 blob（全部可达对象）", flush=True)
    if hits:
        print(f"[FAIL] 命中 {len(hits)} 处，拒绝推送：", file=sys.stderr)
        for path, label, frag in hits[:20]:
            print(f"  {path}  ←  {label}  「{frag}…」", file=sys.stderr)
        return BAD

    # ③ 远端权限
    state, msg = check_remote_visibility()
    if loud:
        print(f"[3/3] 远端权限：{msg}", flush=True)
    if state == "public":
        print(f"[FAIL] {msg}\n       误设 public 不可完全撤回，先改权限再推（D-0045）",
              file=sys.stderr)
        return BAD
    if state == "unknown":
        # 放弃核对而不是拦住推送：离线时不该把人锁死，但必须让人看见
        print(f"[WARN] {msg}\n       未核到权限，无法保证不是 public —— 请自行确认",
              file=sys.stderr)

    if loud:
        print("[PASS] 可推送", flush=True)
    return OK


if __name__ == "__main__":
    sys.exit(main())
