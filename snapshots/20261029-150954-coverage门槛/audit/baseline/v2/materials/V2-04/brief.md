# 材料包 V2-04 · CI 要不要加 macOS 矩阵

## 情形
CI 目前只在 Linux 上跑。有人提议加一个 macOS job，让矩阵覆盖两种系统。

## 事实
- 仓库是**私有**的；GitHub Actions 的 macOS runner 按 Linux 的 **10 倍分钟**计费
- 现有 Linux job 覆盖：干净 clone 能否跑通、全量回归 54 项、发布前门禁 8 项
- 开发机是 macOS
- 已知跨平台差异实例：`git core.quotePath` 默认会把中文路径转义成带引号的形式，
  导致 `grep '\.md$'` 只数到 284（真实 654）——这个坑在 Linux 上不会出现
- 当前 CI 分钟预算偏紧

## 请你裁
`do`（加 macOS 矩阵）/ `dont`（不加）/ `defer`
