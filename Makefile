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
        package-apply snapshot sync-docs clean

# ── 帮助 ──────────────────────────────────────────────────────────────
help:
	@echo "ai-autonomy-team —— 自治团队评审框架"
	@echo ""
	@echo "  首次接手先跑："
	@echo "    make handover      这套东西在本机能不能用（8 项环境自检）"
	@echo "    make check         发布前全量门禁（A–H，8 项）"
	@echo ""
	@echo "  日常："
	@echo "    make test          全量回归（项数见输出，不在此写死）"
	@echo "    make selftest      各脚本内置自检（闸门/开轮/八角/脱敏）"
	@echo "    make metrics       三轨指标看板"
	@echo "    make review DECISION='...'  八角审查（生成 8 份提示词）"
	@echo ""
	@echo "  交付："
	@echo "    make package-check  源与包是否逐字节一致"
	@echo "    make package-apply  同步源 → 包并重建 MANIFEST"
	@echo "    make snapshot       真源快照（改口径前先打）"
	@echo "    make sync-docs      把文档里的数字同步为实际值"
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
	@echo "── 判据自检 + 全量门禁（内含全量回归，约 60 秒）──"
	$(PY) $(SCRIPTS)/pre_release_check.py

# ── 观测 ──────────────────────────────────────────────────────────────
metrics:
	$(PY) $(SCRIPTS)/team_metrics.py

review:
	@if [ -z "$(DECISION)" ]; then \
		echo "用法: make review DECISION='要审查的决定' [FACTS=材料.md]"; exit 2; fi
	$(PY) $(SCRIPTS)/octagon.py --decision "$(DECISION)" $(if $(FACTS),--facts "$(FACTS)",)

# ── 交付 ──────────────────────────────────────────────────────────────
package-check:
	$(PY) $(SCRIPTS)/build_package.py --check

package-apply:
	$(PY) $(SCRIPTS)/build_package.py --apply --manifest

snapshot:
	$(PY) $(SCRIPTS)/snapshot_sources.py --label "$(LABEL)"

sync-docs:
	$(PY) $(SCRIPTS)/sync_doc_numbers.py

# ── 清理 ──────────────────────────────────────────────────────────────
# 只删可重建的 scratch。fixtures/frozen/ 与 migration-package/runs/ 不在其中。
clean:
	rm -rf _trash/tmp-regress _trash/bak-* 2>/dev/null || true
	@echo "已清 scratch；冻结基线与运行记录未动"
