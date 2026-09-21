"""render_spike 的字体清单——**自 U06 起是薄 shim**：真值只有一份，在产品包里
`src/tavotto/rendercore/fonts_allowlist.json`（ADR 0060），取文件的逻辑在 `scripts/fetch_fonts.py`。

U02 时这里登记了 13 张脸的 URL / sha256 / 许可证（ADR 0055）；U06 把它收编成产品的 allowlist 与
`scripts/fetch_fonts.py`，本模块只剩几个入口给 `render_spike.py` / `freeze_spike.py` / workflow 用：
`fetch()` / `verify()` / `face_path()` / `main()`，全部转发。等 U07 收编 render child 后整个
`scripts/dev/u02_spikes/` 一起退役（`foundation-u02-spikes.yml` 文件头写着寿命）。

纯标准库。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import fetch_fonts  # noqa: E402  —— scripts/fetch_fonts.py（纯标准库）

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_ALLOWLIST = fetch_fonts.load_allowlist()

#: 与 U02 时同名的三张表，现在**派生自产品 allowlist**（不再是第二份真值）。
MANIFEST: dict[str, dict] = _ALLOWLIST["faces"]
LICENSE_FILES: dict[str, dict] = _ALLOWLIST["license_files"]
OFL_OBLIGATIONS: tuple[str, ...] = tuple(_ALLOWLIST["obligations"])


def fetch(dest: Path, *, cache: Path | None = None) -> dict[str, Path]:
    return fetch_fonts.fetch(Path(dest), cache=cache, allowlist=_ALLOWLIST)


def verify(dest: Path) -> dict[str, str]:
    problems = fetch_fonts.check(Path(dest), allowlist=_ALLOWLIST)
    if problems:
        raise FileNotFoundError("; ".join(problems))
    return {fid: spec["sha256"] for fid, spec in MANIFEST.items()}


def face_path(dest: Path, family: str, bold: bool = False, italic: bool = False) -> Path:
    for spec in MANIFEST.values():
        if (spec["family"], spec["bold"], spec["italic"]) == (family, bold, italic):
            return Path(dest) / spec["file"]
    raise KeyError((family, bold, italic))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", required=True, type=Path, help="字体与许可证落盘目录")
    ap.add_argument("--cache", type=Path, default=None, help="下载缓存目录")
    ap.add_argument("--json", action="store_true", help="以 JSON 打印 id → 路径 / sha256")
    args = ap.parse_args(argv)
    paths = fetch(args.dest, cache=args.cache)
    digests = verify(args.dest)
    if args.json:
        json.dump(
            {k: {"path": str(paths[k]), "sha256": digests[k]} for k in paths},
            sys.stdout,
            ensure_ascii=False,
            indent=1,
        )
        print()
    else:
        for k in paths:
            print(f"{digests[k]}  {paths[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
