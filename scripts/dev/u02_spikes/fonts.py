"""render_spike 的**批准字体清单**：来源 URL、SHA-256、许可证、族 / 样式 / 字体程序格式。

本仓库不分发任何字体（`tests/test_font_provenance.py`）；这里只登记「从哪下、字节必须
是什么、许可证是什么」，`fetch()` 把它们下到 spike 的 evidence / scratch 目录。产品侧怎么
把字体文件带进发行物（NOTICE、许可证全文随包）是 U06 / #182 的事，清单里先把义务写清。

候选与理由（ADR 0055 §字体）：

* **Liberation 2.1.5**（SIL OFL 1.1；Red Hat / Google）——三个通用族各四态共 12 个 TrueType
  文件，且与旧后端 base-14 所对应的 Times / Helvetica / Courier **度量兼容**（Liberation
  Serif ↔ Times New Roman、Sans ↔ Arial、Mono ↔ Courier New），D07 的布局基线变化因此最小。
* **Noto Sans SC Regular**（SIL OFL 1.1；Google）——Sans2.004 的地区子集 OTF（CFF，CID-keyed），
  与 ADR 0045 Linux 回退链的第一张脸同一家族；「换族不换 CJK」的现行语义保留。

pypdfium2 / pikepdf 之类的候选包不在这里——本模块纯标准库。
"""

from __future__ import annotations

import argparse
import json
import sys
import tarfile
from pathlib import Path

from dev.u02_spikes.hashcheck import download_verified, verify_sha256

# Windows 上 stdout / stderr 被重定向成管道时会退回系统区域编码（cp1252 / cp936），
# 第一句中文就 UnicodeEncodeError；两条流都钉成 UTF-8（tests/test_windows_regressions.py 看护）。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

LIBERATION_VERSION = "2.1.5"
LIBERATION_URL = (
    "https://github.com/liberationfonts/liberation-fonts/files/7261482/"
    "liberation-fonts-ttf-2.1.5.tar.gz"
)
LIBERATION_SHA256 = "7191c669bf38899f73a2094ed00f7b800553364f90e2637010a69c0e268f25d0"
LIBERATION_LICENSE = "OFL-1.1"

NOTO_CJK_COMMIT = "523d033d6cb47f4a80c58a35753646f5c3608a78"  # tag Sans2.004
NOTO_CJK_RAW = f"https://raw.githubusercontent.com/notofonts/noto-cjk/{NOTO_CJK_COMMIT}"
NOTO_SANS_SC_SHA256 = "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9"
NOTO_CJK_LICENSE_SHA256 = "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2"

#: 12 个 Liberation 文件：id → (族, 粗, 斜, 该文件自己的 sha256)。tarball 内的成员名就是 `<id>.ttf`；
#: tarball 整体的 sha256 在上面，单个文件的在这里——解出来的每个字节也要能单独复核。
LIBERATION_FACES: dict[str, tuple[str, bool, bool, str]] = {
    "LiberationSerif-Regular": (
        "serif",
        False,
        False,
        "058ea80864aef09a23f45cbec2bb5400bc3dfbdea01c3f10538a21fcb497fb74",
    ),
    "LiberationSerif-Bold": (
        "serif",
        True,
        False,
        "d754ba427cfe0bca54ae052384baa8f842da5bd6550ad4da024ac441e7a7d5ce",
    ),
    "LiberationSerif-Italic": (
        "serif",
        False,
        True,
        "0e3dea9f8d613e006ccfa62201f33e265d19167bd0907725c3e145368b04fc2e",
    ),
    "LiberationSerif-BoldItalic": (
        "serif",
        True,
        True,
        "f17db8af71e24d2066b587546021d4f0b296be389512b658dec3c09affeb11a7",
    ),
    "LiberationSans-Regular": (
        "sans-serif",
        False,
        False,
        "76d04c18ea243f426b7de1f3ad208e927008f961dc5945e5aad352d0dfde8ee8",
    ),
    "LiberationSans-Bold": (
        "sans-serif",
        True,
        False,
        "788abee4c806d660e8aee46689dd8540cd4bb98da03dcc9d171ce3efd99a9173",
    ),
    "LiberationSans-Italic": (
        "sans-serif",
        False,
        True,
        "e5bae5c4cde31f22142753855f4f8fb86da6ff39955ed3c0a11248b0d16948b0",
    ),
    "LiberationSans-BoldItalic": (
        "sans-serif",
        True,
        True,
        "698da70fc191cc5f33ad4d6d3fe830fe4624b898ea2e3169955928b7c491f1ee",
    ),
    "LiberationMono-Regular": (
        "monospace",
        False,
        False,
        "f2b83c763e8afd21709333370bed4774337fae82267937e2b5aea7e2fbd922c1",
    ),
    "LiberationMono-Bold": (
        "monospace",
        True,
        False,
        "bd62a0672d0b9b6710b01df434c80ad54fa5f0835207eb7b17b7a761463067bb",
    ),
    "LiberationMono-Italic": (
        "monospace",
        False,
        True,
        "605c01c711b44480a7508d349dfbf3264e81fa43d69e61cfa7d10b86e764c4d1",
    ),
    "LiberationMono-BoldItalic": (
        "monospace",
        True,
        True,
        "79451f3c09fe25116098853b7a2ca6e2436220ccc11af022979adbcf195be130",
    ),
}

#: 批准清单（机器可读）。`file` 是 `fetch()` 落到 dest 之后的相对路径。
MANIFEST: dict[str, dict] = {
    **{
        face_id: {
            "file": f"liberation/{face_id}.ttf",
            "sha256": sha,
            "family": fam,
            "bold": bold,
            "italic": italic,
            "format": "truetype",
            "license": LIBERATION_LICENSE,
            "license_file": "liberation/LICENSE",
            "source": {
                "kind": "tarball-member",
                "url": LIBERATION_URL,
                "sha256": LIBERATION_SHA256,
                "member": f"liberation-fonts-ttf-{LIBERATION_VERSION}/{face_id}.ttf",
            },
            "notice": "Liberation Fonts 2.1.5 © 2012 Red Hat, Inc.; digitised data © 2010 Google "
            "Corporation with Reserved Font Names Arimo, Tinos and Cousine. SIL OFL 1.1.",
        }
        for face_id, (fam, bold, italic, sha) in LIBERATION_FACES.items()
    },
    "NotoSansSC-Regular": {
        "file": "noto/NotoSansSC-Regular.otf",
        "sha256": NOTO_SANS_SC_SHA256,
        "family": "cjk",
        "bold": False,
        "italic": False,
        "format": "cff-cid",
        "license": "OFL-1.1",
        "license_file": "noto/LICENSE",
        "source": {
            "kind": "file",
            "url": f"{NOTO_CJK_RAW}/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf",
            "sha256": NOTO_SANS_SC_SHA256,
        },
        "notice": "Noto Sans CJK (Sans2.004) © Google LLC / Adobe; SIL OFL 1.1 with Reserved Font "
        "Name 'Noto'.",
    },
}

#: 许可证全文（随字体文件一起落盘，发行物里要带的就是它们）。
LICENSE_FILES: dict[str, dict] = {
    "liberation/LICENSE": {
        "kind": "tarball-member",
        "url": LIBERATION_URL,
        "sha256": LIBERATION_SHA256,
        "member": f"liberation-fonts-ttf-{LIBERATION_VERSION}/LICENSE",
    },
    "noto/LICENSE": {
        "kind": "file",
        "url": f"{NOTO_CJK_RAW}/LICENSE",
        "sha256": NOTO_CJK_LICENSE_SHA256,
    },
}

#: ADR 0055 引用的许可证义务摘要（OFL 1.1 的四条；与 `docs/legal/` 的 SBOM 纪律衔接）。
OFL_OBLIGATIONS = (
    "随发行物附带许可证全文（OFL 1.1）与版权声明",
    "字体文件可以原样或子集嵌入 PDF（OFL §1 明确允许嵌入文档）",
    "修改后的字体不得使用 Reserved Font Name；本仓库不修改字体，只做 PDF 子集嵌入",
    "不得单独出售字体本身；随软件捆绑分发被 OFL 允许",
)


def _extract_member(archive: Path, member: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tar:
        info = tar.getmember(member)
        if not info.isfile():
            raise ValueError(f"{member} 不是普通文件")
        src = tar.extractfile(info)
        assert src is not None
        with dest.open("wb") as out:
            out.write(src.read())
    return dest


def fetch(dest: Path, *, cache: Path | None = None) -> dict[str, Path]:
    """把清单里的字体与许可证全文落到 `dest`；返回 id → 路径。

    每个字节都经过 SHA-256：tarball 整体校验一次，单文件各校验一次。已落盘且校验通过的
    直接复用（幂等）。
    """
    dest = Path(dest)
    cache = Path(cache) if cache else dest / "_downloads"
    out: dict[str, Path] = {}
    archives: dict[str, Path] = {}

    def archive_for(src: dict) -> Path:
        if src["url"] not in archives:
            archives[src["url"]] = download_verified(
                src["url"], cache / Path(src["url"]).name, src["sha256"]
            )
        return archives[src["url"]]

    for face_id, spec in MANIFEST.items():
        target = dest / spec["file"]
        src = spec["source"]
        if src["kind"] == "tarball-member":
            if not target.is_file():
                _extract_member(archive_for(src), src["member"], target)
        elif src["kind"] == "file":
            download_verified(src["url"], target, src["sha256"])
        else:  # pragma: no cover - 清单是常量
            raise ValueError(src["kind"])
        out[face_id] = target
    for rel, src in LICENSE_FILES.items():
        target = dest / rel
        if src["kind"] == "tarball-member":
            if not target.is_file():
                _extract_member(archive_for(src), src["member"], target)
        else:
            download_verified(src["url"], target, src["sha256"])
    return out


def verify(dest: Path) -> dict[str, str]:
    """已落盘的字体文件逐个对登记的 SHA-256 复核（返回 id → sha256）；许可证全文也要在。"""
    dest = Path(dest)
    digests: dict[str, str] = {}
    for face_id, spec in MANIFEST.items():
        path = dest / spec["file"]
        if not path.is_file():
            raise FileNotFoundError(path)
        verify_sha256(path, spec["sha256"])
        digests[face_id] = spec["sha256"]
    for rel, src in LICENSE_FILES.items():
        path = dest / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        if src["kind"] == "file":
            verify_sha256(path, src["sha256"])
    return digests


def face_path(dest: Path, family: str, bold: bool = False, italic: bool = False) -> Path:
    for face_id, spec in MANIFEST.items():
        if (spec["family"], spec["bold"], spec["italic"]) == (family, bold, italic):
            return Path(dest) / spec["file"]
    raise KeyError((family, bold, italic))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dest", required=True, type=Path, help="字体与许可证落盘目录")
    ap.add_argument("--cache", type=Path, default=None, help="下载缓存目录（默认 dest/_downloads）")
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
