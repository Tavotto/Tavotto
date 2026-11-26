"""生成 / 校对插件技能里的科学图形准则参考文件（唯一出处 `src/tavotto/profiles/sciviz_guidance.json`）。

python scripts/gen_sciviz_reference.py --write   # 改了 JSON 之后重出
python scripts/gen_sciviz_reference.py           # 校对：不一致退出码 1（`tests/test_sciviz_guidance.py` 同判）
"""

from __future__ import annotations

import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tavotto.engine import sciviz_guidance as sg  # noqa: E402


def main(argv: list[str]) -> int:
    target = ROOT / sg.SKILL_REFERENCE
    want = sg.render_markdown()
    if "--write" in argv:
        target.write_text(want, encoding="utf-8", newline="\n")
        print(f"wrote {sg.SKILL_REFERENCE}")
        return 0
    have = target.read_text(encoding="utf-8") if target.is_file() else ""
    if have != want:
        print(
            f"{sg.SKILL_REFERENCE} 与 sciviz_guidance.json 不一致：运行 --write 重出",
            file=sys.stderr,
        )
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
