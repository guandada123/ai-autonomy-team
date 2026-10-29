# 迁移包 · AI 自治团队机制

> **源机**：Windows（验证机）｜**目标机**：Mac mini M4（macOS / Apple Silicon）
> 最后更新：2026-10-19（第四轮：读取域 / 指针归一化 / 契约版本 / 六席校验）
> **包内状态：50 项回归全绿（2026-10-20；38 为源机旧数，39–44 第四轮，45–47 任务参数化，48 家族核验，49 扩样批自检）**

这是一个**自包含**迁移包：凭它可以在 Mac mini M4 上重建整套「单写多读 + 双裁判 + 分歧守恒」机制，并**继续开发**。
包内**不含任何密钥**，**不依赖任何第三方 Python 库**。

---

## 一、在 M4 上的重建步骤

```bash
# 0. 把整个工作区（含 scripts/ docs/ templates/ .learnings/ migration-package/）拷到 M4

# 1. 包级环境自检（不修改任何文件）
cd migration-package
python3 precheck.py
#    预期：全部 OK，仅 2 项「需人工」（跨家族权益、人类裁决记录）

# 2. ★ 全套回归（56 项）—— 这是「能不能继续开发」的判据
cd ..
python3 scripts/regress.py
#    预期：50/50 通过。再跑一次，仍应 50/50（幂等）

# 3. ★ 最关键的一步：核验跨家族路由是否可用
#    这决定「软裁判（跨家族评委）」能否成立。不要假定，必须实测。
cd migration-package
python3 inspect_agent_routing.py --json
#    ★ 脚本在包根（不是 judges/ 下）。若报「未找到会话缓存目录」，
#      加 --project-dir ~/.workbuddy/projects/<本工作区目录名(slug)>
#    PASS → 无需调整
#    FAIL → 编辑 config/params.json，把 independence_discount.soft_judge_weight 设为 0，
#           并在 discount_note 写明「跨家族不可用」，权重全部转移至 JD-01（硬裁判）

# 4. 硬裁判自检
python3 judges/verify.py --self-test

# 5. 端到端跑一次分层判据
python3 judges/verify.py --spec-dir fixtures/spec \
    --answer runs/round2/answer-final.json --out /tmp/v.json
#    预期：算术层 10/10 pass，判断层 2 项 pending → 退出码 3（未裁决 ≠ 通过）

# 6. 按 config/automations.json 的模板，在客户端手工重建定时任务
#    （包内不含已创建的任务 —— 原因见该文件头部说明）
```

> ⚠️ **冻结基线是 `fixtures/frozen/truth.json.frozen`**（2026-10-23 起）——
> 它随 git 入库，解压/克隆即有；`_trash/backup-pre-P5/` 下那份是迁移前的旧副本，
> 已不再是任何脚本的依赖，删不删都不影响回归。
> 回归第 8 项用它做「`truth.json` 是否被动过」的逐字节比对。

---

## 二、目录结构

```
migration-package/
├── README.md                  ← 本文件
├── MANIFEST.json              ← 全量清单 + sha256（177 文件，跨机完整性校验）
├── precheck.py                ← 目标机环境自检（纯标准库）
├── gen_manifest.py            ← 清单生成/校验 + 写入域前后比对（第 8 查）
├── new_round.py               ← 开轮器：建目录 / 匿名化 / 提示词（含读取域注入）
├── metrics.py                 ← 指标采集：立场反推 / 翻边率 / 分歧熵 / 空转
├── make_fixtures.py           ← 合成任务夹具生成器
├── inspect_agent_routing.py   ← 跨家族路由核验器
│
├── protocol/                  ← 协议定义【源】。冲突时以这里为准
│   ├── 01-架构.md                 单写多读 + 双裁判 + 分歧守恒
│   ├── 02-辩论协议.md              四阶段 / 参数 / 终止条件 / 防谄媚六条 / 输出契约
│   ├── 03-结构审计七查.md           审计清单（正文实为**九查**，文件名是历史原因）
│   ├── 04-双裁判规范.md             硬裁判契约 / 软裁判契约 / 输出契约
│   ├── 05-工作协议.md               决策依据 / 风险边界 / 自主审计 / 效果评估
│   ├── 06-写入域与判据分层.md       写入域 / **读取域（第 9 查）** / 判据分层 + 三道闸门
│   └── 07-轮次与指标.md             ★ 指标操作化定义 / 轮次契约 / 14 步半自动流程
│
├── prompts/                   ← 各席位提示词模板（唯一真实源在 templates/）
│   └── pm-01.md  ex-01.md  ex-02.md  ex-03.md  ad-01.md  xj-01.md
│
├── judges/                    ← 裁判（硬裁判是唯一有否决权的部件，非 LLM）
│   ├── verify.py                   硬裁判主体（纯标准库，跨平台）
│   ├── seat_check.py               席位契约校验 + 第 9 查 + 聚合防伪
│   ├── run.sh / run.cmd            薄封装（探测解释器后转发）
│   └── README.md                   输出契约与调用示例
│
├── config/
│   ├── params.json            ← 唯一参数源（辩论 / 防谄媚 / 契约 / 呈现 / 成本）
│   ├── write_domains.json     ← 写入域（第 8 查）
│   ├── read_domains.json      ← 读取域（第 9 查，四层声明式）
│   ├── models.json.example    ← 模型配置示例（不含密钥，须自行填）
│   └── automations.json       ← 定时任务定义（供手工重建）
│
├── fixtures/                  ← 合成任务输入与真值
│   ├── TASK.md  ledger.csv  bill.csv  truth.json
│   ├── spec/                  ← 判据分层：arithmetic.json（自动）/ adjudication.json（人类闸门）
│   └── regress/               ← 回归夹具（roundA…round9 / seat-* / params-threshold0）
│
├── runs/
│   ├── round1/                ← 第 1 轮证据（**契约引入前**产物，0/4 合规，不可作翻边率基线）
│   ├── round2/                ← 第 2 轮证据（契约 v1.0，4 席校验；**可作第 3 轮基线**）
│   └── round3/                ← 第 3 轮证据（契约 v2.0，6 席校验；**实测翻边 1/5**，见 D-0007）
│
└── docs/
    ├── 验证报告.md              ← 四个结构缺陷 F1–F4 + 效果评估
    ├── 修复报告-F1F2F3.md        ← 三项修复的实测记录
    └── 目标机-人工确认清单.md     ← ★ 无法自动化的项，必须人工过一遍
```

---

## 三、平台差异处理

| 项 | Windows（源机） | macOS（目标机） | 本包的处理 |
|---|---|---|---|
| Python | 托管 3.13.12 | 系统 `python3`（≥3.9） | 全部脚本**只用标准库**；`precheck.py` 校验版本 |
| 路径 | `\` | `/` | 一律 `pathlib`；脚本内不写死绝对路径 |
| 换行 | CRLF 风险 | LF | 文本输出统一 `newline="\n"`；否则 **sha256 证据链跨平台断裂** |
| 编码 | 默认 GBK 风险 | UTF-8 | 所有 IO 显式 `encoding="utf-8"` |
| shell | Git Bash | zsh | `run.sh` 用 POSIX sh；脚本内无 shell 方言 |
| 删除 | `unlink()` 被沙箱拦截 | 正常 | 统一「改名到 `_trash/`」，两平台一致；回归第 20 项静态扫描拦截 |
| 物理隔离 | ❌ **做不到**（`attrib +R`/ACL 都是软屏障） | ✅ `chmod` + 低权限运行用户 | 见 `protocol/06` 第三节 |
| 调度 | WorkBuddy 自动化（SQLite） | 同（客户端功能） | **不迁移 db**；导出定义供手工重建 |

---

## 四、四条硬约束

1. **写权限唯一化**——只有 PM-01 可写入共享产物。
   **已由第 8 查自动判定**（写入域前后快照比对），不再只是文本约束。
2. **席位读取域必须声明**——`reads[]` 列出实际读取全集；禁读含真值的文件。
   由**第 9 查**检测。⚠️ 它比第 8 查弱：读取不改变任何东西，故 `CLEAN` **只是下界**，
   未申报的读取对脚本完全不可见；真正的隔离只能在目标机用文件权限做实。
3. **硬裁判判 fail 不可静默覆盖**——覆盖必须留痕：
   `覆盖 JD-01 的 fail 判定。风险评级：__。不可逆：__。理由：__。承担人：PM-01。`
4. **分歧守恒**——不设「必须一致」的终止条件；轮次用尽时强制保留多数 + 少数双结论，
   各附证伪条件。**禁止为收尾好看而抹平分歧。**

---

## 五、已知局限（诚实披露，不许软化）

- **人类裁决记录仍未签署**。`runs/round2/adjudication-record.json` 不存在；
  回归第 5 项用的是文件名与署名均明示「仅管路自检」的替代记录。
  → 在真人签署前，「判断层已被裁决」这句话不成立。
- **第 3 轮未真跑**。已证明「合规基线可被消费、翻边率可算（D=5）」，
  但未在真实多轮中观察翻边行为。
- **读取域的检测 ≠ 隔离**，见第四节第 2 条。
- **两个机制从未触发过**：`restart_round`（翻边率告警）与 `force_devils_advocate_round`
  只在夹具层被验证会触发，真实运行中未出现。
- **本任务判据由本方编写**，故检验的是「机制能否运转」，**不是**「机制能否优于单 agent」。
  后者需要 20 条留出任务。
- **macOS 侧全部为静态审查**，未在真机运行。
