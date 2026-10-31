"""PDF → EPS 转换（可插拔，Ghostscript `eps2write`）。

带画布标注的原图导出（QA-WIN-20）里，EPS 不能像无标注时那样由 worker 的 matplotlib 直接写——标注是 RenderCore
画的。做法：RenderCore 先出**同一份**带标注的 PDF，再交给本模块转成真矢量 EPS（不是栅格化再裹一层 PS，ADR 0046）。

找不到可执行文件 = 转换不可用（`pdf_to_eps` 回 False），由调用方逐项报 `eps_annotations_need_converter`，其余格式照常交付；
**不静默丢标注**。查找顺序：环境变量 `TAVOTTO_GHOSTSCRIPT`（开发 / 测试覆盖）→ 安装包里捆绑的
`resources/ghostscript/bin/gs[.exe]`。不搜 PATH：用户机器上随便一个 gs 的版本与安全配置我们管不了。
调用参数固定（`-dSAFER` 在内），输入输出都是我们自己的临时文件。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

#: 单次转换的上限（秒）：转换在导出作业的 worker 线程里同步跑，不能无界。
TIMEOUT_S = 120


def find_ghostscript() -> Path | None:
    override = os.environ.get("TAVOTTO_GHOSTSCRIPT")
    if override:
        p = Path(override)
        return p if p.is_file() else None
    name = "gs.exe" if sys.platform == "win32" else "gs"
    bundled = Path(__file__).resolve().parent.parent / "resources" / "ghostscript" / "bin" / name
    return bundled if bundled.is_file() else None


def available() -> bool:
    return find_ghostscript() is not None


def pdf_to_eps(pdf: Path, out: Path) -> bool:
    """把单页 PDF 转成 EPS 写到 `out`。成功回 True；转换器不可用 / 失败 / 输出不像 EPS 回 False（`out` 不留残件）。"""
    gs = find_ghostscript()
    if gs is None:
        return False
    cmd = [
        str(gs),
        "-q",
        "-dNOPAUSE",
        "-dBATCH",
        "-dSAFER",
        "-sDEVICE=eps2write",
        f"-sOutputFile={out}",
        str(pdf),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(  # noqa: S603 —— 参数固定，路径都是作业自己的临时文件
            cmd, capture_output=True, timeout=TIMEOUT_S, check=False, creationflags=flags
        )
        ok = proc.returncode == 0 and out.is_file() and out.read_bytes()[:11] == b"%!PS-Adobe-"
    except (OSError, subprocess.SubprocessError):
        ok = False
    if not ok:
        out.unlink(missing_ok=True)
    return ok
