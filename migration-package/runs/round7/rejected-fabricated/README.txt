T-03 round1 三席 v1 产出（2026-10-20 08:1x）——判定：数据虚构。

证据：三席申报 ledger_total=114800 / bill_total=141440，而
fixtures/tasks/T-03/{ledger,bill}.csv 实际合计 120820 / 147460
（由 XJ-01 GLM 独立复算发现，编排方对 CSV 直接复算证实）。
三席套用了同构任务 T-01（round5）的数值结构 + T-03 的单号（T017-T024）。

处置：产出按席位槽位被 v2 覆盖前复制到本目录留档。
算术锚点（spec/arithmetic.json）在任何版本的 answer 上都会拒绝这批数值。
