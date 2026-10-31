# ai-autonomy-team —— 统一入口
#
# 设计原则：Makefile 只做「调度」，不实现逻辑。
# 每个 target 都是一行对 scripts/ 下某个脚本的调用 —— 这样任何一条都能
# 脱离 make 直接跑（README 里给的命令与这里逐字等价）。
#
# 全部入口都是只读或可回滚的；唯一会改文件的是 `package-apply` 与 `sync-docs`。

PY      := python3
SCRIPTS := scripts
PKG     := migration-package

.DEFAULT_GOAL := help
.PHONY: help check test selftest handover metrics review package-check \
        recompute v11-compare item-gate score brief-lint kit-test label-audit \
        package-apply snapshot sync-docs pre-push hooks backup backup-list veto-list coverage sync clean

# ── 帮助 ──────────────────────────────────────────────────────────────
help:
	@echo "ai-autonomy-team —— 自治团队评审框架"
	@echo ""
	@echo "  首次接手先跑："
	@echo "    make handover      这套东西在本机能不能用（6 项环境自检，含推送钩子）"
	@echo "    make check         发布前全量门禁（A–H，8 项）"
	@echo ""
	@echo "  日常："
	@echo "    make test          全量回归（项数见输出，不在此写死）"
	@echo "    make selftest      各脚本内置自检（闸门/开轮/八角/脱敏/推送/门禁）"
	@echo "    make pre-push      推之前自查（历史密钥 + 远端是否 private）"
	@echo "    make hooks         安装 git pre-push 钩子（本地文件，换机需重装）"
	@echo "    make backup        本地备份（bundle + 快照归档，逐份还原验证）"
	@echo "    make backup-list   列出已有备份、不生成新的（含总占用）"
	@echo "    make metrics       三轨指标看板"
	@echo "    make review DECISION='...'  八角审查（生成 8 份提示词）"
	@echo ""
	@echo "  交付："
	@echo "    make package-check  源与包是否逐字节一致"
	@echo "    make package-apply  同步源 → 包并重建 MANIFEST"
	@echo "    make snapshot       真源快照（改口径前先打）"
	@echo "    make sync-docs      把文档里的数字同步为实际值"
	@echo "    make coverage       覆盖率基线（开发期，需装 coverage；见 tools/README.md）"
	@echo "    make sync           并行会话防护：pull --rebase --autostash 后回读 HEAD/远端"
	@echo "    make veto-list      列出 24h 否决窗口内待裁的结论（人工否决权入口）"
	@echo "    make recompute      题库 v1.1 真值重算检查（漂移即红，先跑它再出分）"
	@echo "    make item-gate      题目准入闸（饱和/常数胜出/全错；当前应 FAIL）"
	@echo "    make score          通用判分器（题集无关，含 pass^k 与 Fleiss κ）"
	@echo "    make brief-lint     题面准入检查（情形段含结论/引用编号；当前应 FAIL）"
	@echo "    make kit-test       判定工具自检（项数以脚本自报为准，含与历史判分器的交叉验证）"
	@echo "    make label-audit    标注集饱和检查（满分/阈值不敏感；当前应命中）"
	@echo ""
	@echo "  清理："
	@echo "    make clean          清 _trash 下的 scratch（不动冻结基线）"

# ── 验收 ──────────────────────────────────────────────────────────────
handover:
	$(PY) $(SCRIPTS)/verify_handover.py

check:
	$(PY) $(SCRIPTS)/pre_release_check.py

test:
	$(PY) $(SCRIPTS)/regress.py

upgrade-status:
	$(PY) $(SCRIPTS)/upgrade_status.py

spec-check:
	$(PY) $(SCRIPTS)/spec_conformance.py

selftest:
	@echo "── 开轮器（含 preflight 闸门）──"
	cd $(PKG) && $(PY) new_round.py --self-test
	@echo ""
	@echo "── 八角审查 ──"
	$(PY) $(SCRIPTS)/octagon.py --self-test
	@echo ""
	@echo "── 脱敏（对外版生成器）──"
	$(PY) $(SCRIPTS)/redact_report.py --self-test
	@echo ""
	@echo "── 推送闸门（检测器正样本自检）──"
	$(PY) $(SCRIPTS)/pre_push_scan.py --self-test
	@echo ""
	@echo "── 备份验证逻辑自检 ──"
	$(PY) $(SCRIPTS)/make_backup.py --self-test
	@echo ""
	@echo "── 判定工具自检（多数票边界 / 覆盖率语义 / κ 已知值 / 与 score_v2 交叉）──"
	$(PY) audit/kit/test_kit.py
	@echo ""
	@echo "── 出题质检闸自检（C1–C6 + C0 哈希绑定 / 正负样本 / 压线边界）──"
	$(PY) audit/kit/test_item_review.py
	@echo ""
	@echo "── 口径库校验（审查员证据源）──"
	$(PY) audit/kit/caliber_lib.py --check
	@echo ""
	@echo "── 可举证率指标（含闸门与指标不得分歧的守卫）──"
	$(PY) audit/kit/test_cite_rate.py
	@echo ""
	@echo "── 模型留痕（口述不冒充机器证据 / 去重不被口述干扰）──"
	$(PY) scripts/test_record_model.py
	@echo ""
	@echo "── 真源快照自检（三向比对 / restore 正负样本）──"
	$(PY) $(SCRIPTS)/snapshot_sources.py --self-test
	@echo ""
	@echo "── 席位检查器自检 ──"
	$(PY) $(SCRIPTS)/judges/seat_check.py --self-test
	@echo ""
	@echo "── 指标脚本自检 ──"
	$(PY) $(SCRIPTS)/metrics.py --self-test
	@echo ""
	@echo "── 决策链一致性自检 ──"
	$(PY) $(SCRIPTS)/check_consistency.py --self-test
	@echo ""
	@echo "── 覆盖率门槛自检（ratchet 比较逻辑）──"
	./tools/coverage_gate.sh --self-test
	@echo ""
	@echo "── 交接摘要数字生成器自检（emit/check 往返）──"
	$(PY) $(SCRIPTS)/handover_facts.py --self-test
	@echo ""
	@echo "── 脱敏工具自检（Bearer / token= / URL 凭据 / 幂等）──"
	$(PY) $(SCRIPTS)/mask_secrets.py --self-test
	@echo ""
	@echo ""
	@echo "── 回归运行保障自检（超时 / 进程组 / scratch 隔离 / 防回退）──"
	$(PY) $(SCRIPTS)/regress.py --self-test
	@echo "── 门禁自身自检（每个检查项都配正负样本，防「门禁假绿」）──"
	$(PY) $(SCRIPTS)/pre_release_check.py --self-test
	@echo "── 判据自检 + 全量门禁（内含全量回归，约 15 秒）──"
	$(PY) $(SCRIPTS)/pre_release_check.py

# ── 观测 ──────────────────────────────────────────────────────────────
metrics:
	$(PY) $(SCRIPTS)/team_metrics.py

review:
	@if [ -z "$(DECISION)" ]; then \
		echo "用法: make review DECISION='要审查的决定' [FACTS=材料.md]"; exit 2; fi
	$(PY) $(SCRIPTS)/octagon.py --decision "$(DECISION)" $(if $(FACTS),--facts "$(FACTS)",)

# ── 推送 ──────────────────────────────────────────────────────────────
# 推之前扫全部可达历史（不是只看工作区）+ 核对远端是否 private。
# hook 是本地文件、不入库：换机 clone 后要重跑 make hooks。
pre-push:
	$(PY) $(SCRIPTS)/pre_push_scan.py

hooks:
	$(PY) $(SCRIPTS)/pre_push_scan.py --install-hook

# ── 备份 ──────────────────────────────────────────────────────────────
# 远端不覆盖 .gitignore 的 _trash/（历史快照归档只此一份），故本地另备一份。
# 每份产物都做还原验证：文件存在不算备份，能还原且一致才算。
# 注意：`make backup --list` 不生效 —— `--list` 会被 make 自己吃掉（表现为打印
#       make 帮助且 exit 0）。要传参用 ARGS，或直接用下面的 backup-list。
backup:
	$(PY) $(SCRIPTS)/make_backup.py $(ARGS)

backup-list:
	$(PY) $(SCRIPTS)/make_backup.py --list

# ── 交付 ──────────────────────────────────────────────────────────────
package-check:
	$(PY) $(SCRIPTS)/build_package.py --check

package-apply:
	$(PY) $(SCRIPTS)/build_package.py --apply --manifest

snapshot:
	$(PY) $(SCRIPTS)/snapshot_sources.py --label "$(LABEL)"

sync-docs:
	$(PY) $(SCRIPTS)/sync_doc_numbers.py

# ── 题库 v1.1：重算检查 + 同条件配对比较（审计用，不进门禁）──────────────
# 为什么单独：v1 的教训是「冻结只校验哈希，证明不了还对」。v1.1 必须先重算再出分。
recompute:
	$(PY) audit/baseline/v1.3/recompute_truth.py   # 2026-10-28（D-0079）：现行题库切到 v1.3（25 题）

# 题目准入闸：出分之前先判「题还有没有区分度」（饱和 / 常数胜出 / 全错）
# 为什么在 Makefile 里：v2、v3 两次事故都是跳过这一步直接算分造成的。
# ★ 当前它一定 FAIL —— 因为 v3 这批题本身没过准入（常数基准 0.700 高过所有臂）。
#   这不是脚本坏了，是它如实报告了题目不合格。修题后自然转 PASS。
#   故不列入 selftest，避免把「题不合格」和「代码坏了」混成同一个红灯。
item-gate:
	$(PY) audit/kit/item_gate.py \
		--adjudication audit/baseline/v3/adjudication-v3.json \
		--arm single=audit/baseline/v3/arm-M1.json \
		--arm seats=audit/baseline/v3/seat-S1.json,audit/baseline/v3/seat-S2.json,audit/baseline/v3/seat-S3.json

# 通用判分器（题集无关）：Q10 用户裁 C 的落地件。
# 与 score_v2 的差别：标签不限、理由/风险字段可选、支持重复跑算 pass^k、附带 Fleiss κ。
# 验收标准是「复现历史数字」：v2 第四轮 26/26、v3 首轮 单席 5/10 与多席 5/9。
# 带 k=3 的三次重复（数据已在库，直接跑即得 pass^1 / pass^3 / 自稳）。
score:
	$(PY) audit/kit/score.py --items audit/baseline/v3/adjudication-v3.json \
		--arm "M1=audit/baseline/v3/arm-M1.json,audit/baseline/v3/r2-M1.json,audit/baseline/v3/r3-M1.json" \
		--arm "S1=audit/baseline/v3/seat-S1.json,audit/baseline/v3/r2-S1.json,audit/baseline/v3/r3-S1.json" \
		--arm "S2=audit/baseline/v3/seat-S2.json,audit/baseline/v3/r2-S2.json,audit/baseline/v3/r3-S2.json" \
		--arm "S3=audit/baseline/v3/seat-S3.json,audit/baseline/v3/r2-S3.json,audit/baseline/v3/r3-S3.json"

# 题面准入：查「题面里有没有写着答案」（情形段含结论、引用编号、选项描述不对称）。
# 与 item-gate 分工：lint 查题面是否干净，item_gate 查题是否还有区分度。两道都过才可入库。
# ★ 当前对 v3 十份必然 FAIL（情形段有 4 处锚点）——这是如实报告，不是脚本坏了。
#   若你决定按 lint 重写 brief，改成 v3.1 后此目标自然转 PASS。
brief-lint:
	$(PY) audit/kit/brief_lint.py audit/baseline/v3/materials

# 判定工具自检。这三个脚本产出的是决策级数字（进决策记录 / 决定题能不能用），
# 此前零测试：brief_lint 误报过 10 份、item_gate 的单席路径被改崩过（0.500→0.000）。
# 含与历史 score_v2.py 的交叉验证——两处多数票实现若走偏，这里立刻红。
kit-test:
	$(PY) audit/kit/test_kit.py
	$(PY) audit/kit/test_item_review.py

# 出题质检闸：只看命题本身（题面/选项/结构），不看答题结果。
# 与 item_gate（答题侧区分度）、route_gate（路由/偏斜）串联：命题可信 → 路由合规 → 实验 → 区分度。
item-review:
	$(PY) audit/kit/item_review.py --dossier audit/baseline/v2/dossier-v2.json

# 口径库校验：审查员举证的唯一证据源，自己也要有闸门（id 唯一/来源可追/例外须带范围/反泄露）。
caliber-check:
	$(PY) audit/kit/caliber_lib.py --check

# 模型留痕：切换模型后跑一次，模型变化才追加（结论标签的强度由日志决定，不由记忆决定）。
# 查看历史：make model-log SHOW=1
model-log:
ifdef SHOW
	$(PY) scripts/record_model.py --show
else
	$(PY) scripts/record_model.py
endif

# 留痕工具的验收（含变异测试，见 .learnings/L-0043 与 L-0042）。
model-log-test:
	$(PY) scripts/test_record_model.py

# 标注集饱和检查：一套标注能不能验证规则（满分/阈值不敏感/两集同源三条判据）。
# 与 item-gate、brief-lint 并列，三者分工：题面能否入库 / 题有无区分度 / 标注能否验规则。
# ★ 当前必然命中（校准集与留出集都 1.00，留出集 9 个阈值点不变）——如实报告。
label-audit:
	$(PY) audit/kit/label_saturation.py

# 两臂须同条件（同一份提示词、同一套访问权限）；单臂与多臂都必须重跑
v11-compare:
	$(PY) audit/baseline/v1.1/compare_v11.py \
		--single audit/baseline/v1.1/arm-M1.json \
		--multi  audit/baseline/v1.1/multi-answers.json

# ── 否决窗口（人工否决权；无需调度器，想看时跑）──────────────────────────
veto-list:
	$(PY) $(SCRIPTS)/veto_sweep.py --list

# ── 覆盖率（开发期，非产品依赖）────────────────────────────────────────
# 需要装好 coverage 的解释器；子进程记账靠 tools/sitecustomize.py
coverage:
	@echo "（开发期工具：需要 python -m venv + pip install coverage，见 tools/README.md）"
	./tools/coverage_run.sh

# 双写者防护（2026-10-29 审计）：并行会话写同一仓库时，动手前先 sync。
# --autostash：本地未提交改动先收起、rebase 后恢复；回读 HEAD/远端相等才算同步。
sync:
	git pull --rebase --autostash origin main
	git fetch -q origin main
	@echo "── 回读 ──"
	@git rev-parse HEAD origin/main
	@if [ "$$(git rev-parse HEAD)" = "$$(git rev-parse origin/main)" ]; then \
		echo "[PASS] 本地与远端同步"; \
	else \
		echo "[FAIL] 未同步——先处理再继续"; exit 1; \
	fi

# ── 清理 ──────────────────────────────────────────────────────────────
# 只删可重建的 scratch。fixtures/frozen/ 与 migration-package/runs/ 不在其中。
clean:
	rm -rf _trash/tmp-regress _trash/bak-* 2>/dev/null || true
	@echo "已清 scratch；冻结基线与运行记录未动"
