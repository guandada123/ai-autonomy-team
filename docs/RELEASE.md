# 发布流程（GitHub Release，不走 PyPI）

## 为什么不用 PyPI
本项目是**脚本集**（入口为 `python3 scripts/<name>.py`），`pyproject.toml` 刻意不定义
`[project]`/`[build-system]`——见该文件头部注释。发布到 PyPI 会产出「能安装但装完不可用」
的空包，比不发布更误导。因此发布形态 = **源码包 + tag**，使用者 `git clone` 后直接运行。

## 发布步骤
1. 更新 `VERSION`（语义化版本）；
2. 在 `CHANGELOG` 或 README 的变更段记录本次改动；
3. `make check && make selftest`（全绿才可发）；
4. `git tag -a v<version> -m "v<version>"` 并 `git push origin v<version>`；
5. GitHub 上以该 tag 创建 Release，附变更摘要。

## 许可
MIT（见仓根 LICENSE）。分发时保留版权声明即可。
