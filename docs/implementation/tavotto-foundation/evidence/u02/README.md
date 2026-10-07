# U02 evidence（两个技术证明的小产物）

全部由 `scripts/dev/u02_spikes/` 生成；候选包只装在独立的 spike venv
（`python -m venv <scratch>/spike-venv && pip install -r scripts/dev/u02_spikes/requirements.txt`）。
结论在 `docs/adr/0055-render-spike.md` / `0056-runtime-spike.md`，交接在 `../../handoffs/U02_spikes.md`。

| 目录 | 文件 | 来源 | 进 git 的理由 |
|---|---|---|---|
| `render/` | `truth.json` | **手写**的输入规格 + 解析式真值（不是写入器生成的） | 判据的主语 |
| | `spike.pdf`（18 KB） | `render_spike.py` 写出（pikepdf + fontTools + HarfBuzz） | `tests/test_foundation_u02_render.py` 用纯标准库读它 |
| | `spike_pdfium.png`（800×640 RGBA，30 KB） | PDFium 5.13.0 / 153.0.7999.0 栅格 | 同上（像素采样） |
| | `report.json` | 版本 / 输入输出 hash / 写入事实 / 三把独立读取器的 52 条核对 | 与上两个文件的 hash 互相钉住 |
| `freeze/` | `report-darwin-arm64.json`、`frozen-render-darwin-arm64.png`、`pyinstaller-log.txt` | `freeze_spike.py`（PyInstaller 6.19.0 onedir，产物 69 MB **不进 git**） | 冻结 exe 自起 child 真渲染的结果 |
| `runtime/` | `report-macos-arm64.json` | `runtime_spike.py`（纯标准库；uv / pbs / wheel 全按 hash 下载，落在临时 `TAVOTTO_DATA_DIR`） | 15 步各自的命令 / 退出码 / 路径 |

重生成（macOS arm64；两次运行的 `spike.pdf` / PNG 字节相同）：

```sh
S=<scratch>
PYTHONPATH=scripts:src $S/spike-venv/bin/python -m dev.u02_spikes.fonts --dest $S/fonts
PYTHONPATH=scripts:src $S/spike-venv/bin/python -m dev.u02_spikes.render_spike --fonts $S/fonts --out docs/implementation/tavotto-foundation/evidence/u02/render
PYTHONPATH=scripts:src $S/spike-venv/bin/python -m dev.u02_spikes.freeze_spike --fonts $S/fonts --pdf docs/implementation/tavotto-foundation/evidence/u02/render/spike.pdf --out docs/implementation/tavotto-foundation/evidence/u02/freeze
PYTHONPATH=scripts:src .venv/bin/python -m dev.u02_spikes.runtime_spike --out docs/implementation/tavotto-foundation/evidence/u02/runtime
```

其它平台的同一套产物由 `.github/workflows/foundation-u02-spikes.yml`（`workflow_dispatch` + 只在 spike 文件变动时的 `pull_request`，非 required）以工件 `u02-evidence-<os>` 给出，不进 git；run 号记在交接文件。

## 2026-10-07 render 复核

PR #790 的 `render/report.json` 与 `render/spike_pdfium.png` 已由 Linux x86_64 / CPython 3.12.14、pypdf 6.19.0 完整重生成；52/52，原 PDF 与真值逐字节不变。PNG 是本次 Linux 字节，不能当作原 macOS 栅格。恢复的原 spike 验证器来自仓库提交 `6dd63da`，仅在 scratch 运行；源码保持退役。方法、完整边界与 hash 见 ADR 0055 §8。上面的 macOS 命令与 freeze/runtime 目录仍是历史记录。
