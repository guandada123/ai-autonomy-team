# DEPENDENCIES — 依赖、环境与配置

> 结论先行：**零第三方依赖**。只要有 Python 3.9+，clone 下来就能跑。
> 这是刻意的，不是巧合——理由见 §3。

---

## 一、运行时要求

| 项 | 要求 | 实测环境 | 说明 |
|---|---|---|---|
| Python | **≥ 3.9** | 3.13.12 | 代码未用 `match`、未用 `X \| Y` 类型联合；`regress` 有一项专门校验 py39 兼容 |
| 操作系统 | macOS / Linux | macOS（APFS + 外置盘） | 未在 Windows 验证。`judges/run.cmd` 暗示曾考虑过，但未测 |
| 第三方包 | **无** | — | 见 §2 |
| 磁盘 | ≥ 50 MB | 源码+证据 9.6 MB，`_trash` 另占 71 MB（可删） | — |

### 检查你的环境

```bash
python3 -V                              # 必须 ≥ 3.9
python3 scripts/verify_handover.py      # 8 项环境自检，一条命令回答"能用吗"
```

`verify_handover.py` 会检查：Python 版本、关键脚本存在、config 可读、`runs/` 可写、夹具完整性、冻结基线、MANIFEST 可校验、退出码语义。

---

## 二、标准库依赖清单

全部 24 个脚本合计只用以下 23 个标准库模块，**无一例外**：

| 用途 | 模块 |
|---|---|
| 系统与路径 | `sys` `os` `pathlib` `shutil` `platform` `tempfile` |
| 数据 | `json` `csv` `decimal` `collections` `re` `fnmatch` |
| 时间 | `datetime` |
| 校验 | `hashlib` `zipfile` |
| 执行 | `subprocess` `importlib` `contextlib` `inspect` `argparse` `io` |
| 杂项 | `random` `math` |

**为什么坚持零依赖**（三条实际理由，不是洁癖）：

1. **判据不能随依赖版本漂移**。`decimal` 的舍入行为如果来自第三方库，同一份输入在不同机器上判出不同结果，"可复现"就断了。
2. **迁移成本**。这套东西要能在别人的 Mac 上 5 分钟跑起来（`make_transfer` 就是干这个的）。要 `pip install` 就意味着要处理虚拟环境、镜像源、版本冲突。
3. **审计成本**。`secret_patterns.py` 需要扫全仓找硬编码凭据。依赖越少，扫描面越确定。

**例外处理**：确实需要第三方库时（例如未来接入模型 API），放在独立脚本里并**不进入 `judges/` 与 `regress` 的关键路径**——判据层必须永远零依赖。

---

## 三、可选工具

| 工具 | 用途 | 缺失时的影响 |
|---|---|---|
| `git` | 版本控制 | **必需**。没有 git 就没有回滚，2026-10-23 之前这个项目正是因此处于"一次误删即永久丢失"状态 |
| `make` | `make check` 等便利入口 | 可选。每个 target 都是一行 `python3 scripts/x.py`，可手工执行（README 给了等价命令） |
| `ruff` | 代码风格（`pyproject.toml` 里配置） | 可选。缺失只影响格式一致性，不影响运行 |

**没有**：pytest、numpy、pandas、requests、任何 LLM SDK。
测试框架是自研的（`regress.py` 里的 `Suite` 类），因为需要"断言退出码 + 断言 stderr 关键字 + 断言文件未变"这类 pytest 不擅长表达的东西。

---

## 四、配置

### 配置文件清单

| 文件 | 大小 | 作用 | 改动影响 |
|---|---|---|---|
| `config/params.json` | 25 KB | 契约参数：`contract_version`、`seat_contract.families`、三轨指标定义 | **改判据口径**。必须重跑 `make test` |
| `config/read_domains.json` | 9 KB | 每个脚本能读哪些域 | 文本约束（不是物理屏障，见 `L-0003`） |
| `config/write_domains.json` | 1.3 KB | 每个脚本能写哪些域 | 同上 |
| `config/automations.json` | 3.6 KB | 自动化任务登记 | 不影响判据 |
| `config/models.json.example` | 1.8 KB | 模型路由示例（**示例，不是生效配置**） | 仅参考 |

### 加载顺序

```
脚本启动
  → ROOT = Path(__file__).parent.parent      # 位置无关，不依赖 cwd
  → 读 config/params.json                    # 缺失时按各脚本默认值，不猜
  → 读该轮的 round-manifest.json             # ★ contract_version 从这里读，不是从 params
```

> ⚠️ **最容易误解的一点**：`params.json` 里的 `contract_version` 只是**新建轮次的默认值**。校验既有轮次时读的是该轮 manifest 里的版本。混用会让老轮次被新判据误杀。

### 环境变量

| 变量 | 用于 | 默认 | 说明 |
|---|---|---|---|
| `AI_TEAM_PORTFOLIO` | `real_recon.py` 的账本路径 | 空 | 2026-10-23 新增。此前硬编码为某外部项目路径 |

```bash
# 不设变量、不给参数 → 明确报错，不猜
python3 scripts/real_recon.py
# [ERR] 未指定账本：加 --portfolio <portfolio.json>，或设环境变量 AI_TEAM_PORTFOLIO。

# 两种等价用法
AI_TEAM_PORTFOLIO=/path/to/portfolio.json python3 scripts/real_recon.py --json
python3 scripts/real_recon.py --portfolio /path/to/portfolio.json --json
```

---

## 五、独立性声明与验证

### 声明

本仓库**不 import、不读取、不依赖任何其他项目**。逐项核对：

| 检查项 | 结果 |
|---|---|
| `import` 其他项目的模块 | 无（仅 2 处内部复用：`seat_check`、`secret_patterns`） |
| 硬编码外部项目路径 | 无（唯一一处 `real_recon.py` 已于 2026-10-23 解除） |
| 读取外部项目的文件 | 无 |
| 调用外部项目的脚本 | 无 |
| 网络请求 | 无（全项目无 `urllib`/`requests`/`http`） |

**唯一保留的外部路径字符串**在 `redact_report.py` 里，但那是**脱敏规则表**——它把 `/Volumes/.../Claw` 替换成 `<CLAW>` 再输出。外部项目不存在时它照常工作，不构成依赖。

### 怎么自己验证

```bash
# 1) 干净目录里跑（不带 _trash / _archive，模拟新机器）
rsync -a --exclude=_trash --exclude=_archive --exclude=.git \
      ai-autonomy-team/ /tmp/aiteam-clean/

cd /tmp/aiteam-clean
python3 scripts/verify_handover.py     # 期望：8 项通过
python3 scripts/pre_release_check.py   # 期望：8/8
python3 scripts/regress.py             # 期望：50/50

# 2) 断网跑（证明无网络依赖）
# 3) 断开外置盘后跑（证明无外部挂载依赖）
```

2026-10-23 实测结果见 `docs/独立化验证-20261023.md`。

---

## 六、迁移到新机器

```bash
# 源机：打包
python3 scripts/make_transfer.py
# → outputs/transfer-m4-YYYYMMDD.zip

# 目标机：解压后自检
unzip transfer-m4-*.zip -d ai-autonomy-team
cd ai-autonomy-team
python3 migration-package/precheck.py       # 目标机环境自检（不依赖外部项目）
python3 migration-package/gen_manifest.py --root . --check MANIFEST.json
```

**迁移时必须一起走的三样**（漏一样就会红）：

| 必须带走 | 漏了会怎样 |
|---|---|
| `fixtures/frozen/truth.json.frozen` | `regress` 第 8 项 FAIL（不是跳过，是 FAIL——刻意设计） |
| `migration-package/MANIFEST.json` | 包完整性校验无基准 |
| `config/params.json` | 所有判据口径丢失 |

---

## 七、已知的依赖风险

| # | 风险 | 严重度 | 现状 |
|---|---|---|---|
| 1 | 依赖 Python 内部行为（`set` 迭代顺序、`hash` 随机化） | 中 | `L-0007` 记录过：`set` 顺序随 `PYTHONHASHSEED` 漂移。相关代码已改为排序后遍历 |
| 2 | macOS 专有路径语义（APFS 大小写不敏感、外置盘符号链接） | 中 | `cp -a` 对符号链接是假备份，已在 Claw 项目踩过；本项目无符号链接，暂无影响 |
| 3 | 未在 Windows 验证 | 低 | 有 `judges/run.cmd`，但路径分隔符相关代码未测 |
| 4 | `ruff` 版本未锁定 | 低 | 只影响格式检查，不影响运行；如需严格可加到 `pyproject.toml` 的注释里 |
