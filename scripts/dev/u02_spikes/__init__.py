"""统一实施包 U02 的两个技术证明（render_spike / runtime_spike）——**spike 代码，不进产品**。

这里的模块只被 `scripts/dev/u02_spikes/*` 自己、`tests/test_foundation_u02_*.py` 与
`.github/workflows/foundation-u02-spikes.yml` 调用；`src/tavotto/` 一个 import 都不许指向
这里（`tests/support/importgraph.py` 守卫的是产品图，这个包不在图上）。候选包
（pypdfium2 / pikepdf / fontTools / uharfbuzz / pdfminer.six / pypdf / PyInstaller / uv）
全部装在独立的 spike venv 里，版本钉在 `requirements.txt`，主仓库 `.venv` 与
`pyproject.toml` 的依赖闭包一个字节不变。

结论与证据见 `docs/adr/0055-render-spike.md`、`docs/adr/0056-runtime-spike.md` 与
`docs/implementation/tavotto-foundation/evidence/u02/`。
"""
