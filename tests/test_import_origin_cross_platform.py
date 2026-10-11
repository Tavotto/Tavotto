"""Import Origin 的跨平台形状（计划 P7.5）：**能在任何宿主上模拟的那一半**。

三类「只在别人电脑上发生」的形状，在 macOS / Linux 上就能钉住——它们不依赖真的 Windows 文件系统，依赖的是**文本与路径的形状**：

1. 路径拼写不改变结论：同一棵项目树放在 ASCII 目录、中文 + 空格 + 括号目录、带重音字符的目录里，扫描结论逐字相同
   （只有项目相对路径进载荷，绝对路径从不出现）；中文模块名（`数据.py`）按真实 Python 的规则当本地模块；
   环境前缀在中文 + 空格路径下照样读得出发行包元数据；
2. 源码编码：GBK 声明（`# -*- coding: gbk -*-`，国内用户最常见）、UTF-8 BOM、latin-1 声明都能读出 import；读不懂的编码是
   「一条问题」而不是崩溃，且问题里没有文件内容；
3. Windows 形状（在 POSIX 上用文本模拟）：`RECORD` 里的反斜杠路径、`pyvenv.cfg` 里的盘符 + 中文用户名、`.pth` 里的盘符 / 反斜杠
   路径行（在 POSIX 上它只是个不存在的相对名，绝不能被当成 site-packages 之外的真路径去读）、大小写不同的 dist-info 目录名。

**这里证明不了的**（真 Windows 才有）：NTFS 的大小写不敏感与 8.3 短名、junction / reparse point 的真实判定、`MAX_PATH`、
`.pyd` / DLL 的真实加载、PyInstaller 冻结运行时的 `sys.path` 布局——列在 FINAL_ACCEPTANCE.md 的「需 Windows 真机 / CI 验证」里；
其中 reparse / junction 的判据已有替身用例（`test_import_origin_windows_links.py`），扩展名后缀表按**目标**解释器给
（`test_import_origin_target_facts.py`）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tavotto.engine import depresolve, distmeta, importscan
from test_import_origin_metadata import _dist, _env, _index, _write  # noqa: F401
from test_import_origin_static import _real_python

ROOTS = [
    pytest.param("ascii_root", id="ascii"),
    pytest.param("我的 论文/paper 图 (v2)", id="chinese-space-parens"),
    pytest.param("Ünïcode ñ/proj", id="accented"),
]

TREE = {
    "scripts/figure1.py": "import numpy as np\nimport docx\nfrom utils import load\nfrom lab import tools\n",
    "scripts/utils.py": "import scipy.signal\n\n\ndef load():\n    return 1\n",
    "scripts/lab/__init__.py": "",
    "scripts/lab/tools.py": "from . import helpers\nimport h5py\n",
    "scripts/lab/helpers.py": "VALUE = 1\n",
}


def _build(tmp_path: Path, root_name: str, tree: dict[str, str | bytes]) -> Path:
    root = tmp_path / root_name
    for rel, text in tree.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            p.write_bytes(text)
        else:
            p.write_text(text, encoding="utf-8")
    return root


def _facts(res: importscan.ScanResult) -> list[tuple]:
    return sorted(
        (c.module, c.bucket, c.context, c.origin_kind, c.resolution_status, c.local_path)
        for c in res.classes
    )


# ---------------------------------------------------------------- 1. 路径拼写不改变结论
class TestPathSpelling:
    @pytest.mark.parametrize("root_name", ROOTS)
    def test_the_same_tree_gives_the_same_facts_under_any_root_spelling(self, tmp_path, root_name):
        reference = importscan.scan(
            _build(tmp_path / "ref", "ascii_root", TREE), "scripts/figure1.py"
        )
        res = importscan.scan(_build(tmp_path / "x", root_name, TREE), "scripts/figure1.py")
        assert _facts(res) == _facts(reference)
        assert sorted(res.files) == sorted(reference.files)
        # 空转反证：这棵树确实被跟进了（不是两边都空）
        assert {"numpy", "docx", "scipy", "h5py"} <= {c.module for c in res.classes}
        assert "lab" in {c.module for c in res.classes}

    @pytest.mark.parametrize("root_name", ROOTS)
    def test_no_absolute_path_or_root_spelling_reaches_the_payload(self, tmp_path, root_name):
        tree = {**TREE, "scripts/broken.py": "def (:\n"}
        root = _build(tmp_path, root_name, tree)
        res = importscan.scan(root, "scripts/broken.py")
        payload = json.dumps(
            {"classes": [c.to_payload() for c in res.classes], "problems": res.problems},
            ensure_ascii=False,
        )
        assert str(root) not in payload and str(tmp_path) not in payload
        for part in Path(root_name).parts:
            if part not in ("scripts",):
                assert part not in payload, part
        assert res.problems, "语法错误应当是一条问题"

    def test_a_chinese_module_name_is_a_local_module_exactly_as_real_python_sees_it(self, tmp_path):
        root = _build(
            tmp_path,
            "我的 论文",
            {
                "figure.py": "from 数据 import 读取\nprint(读取())\n",
                "数据.py": "import yaml\n\n\ndef 读取():\n    return 42\n",
            },
        )
        res = importscan.scan(root, "figure.py")
        got = {c.module: c for c in res.classes}
        assert got["数据"].bucket == "local" and got["数据"].local_path == "数据.py"
        assert "yaml" in got and got["yaml"].via == ("数据.py",)  # 本地模块里的第三方 import 被跟进
        # 真实 Python 对拍：同一棵树，`python figure.py` 真的把 数据.py 当本地模块导入（yaml 在那里才会炸）
        (root / "yaml.py").write_text(
            "", encoding="utf-8"
        )  # 让 import yaml 在真实运行里成功，只验证 数据 被找到
        out = _real_python(root, "figure.py")
        assert out.returncode == 0 and out.stdout.strip() == "42", out.stderr

    @pytest.mark.parametrize("env_name", ["env", "我的 环境/venv 一"])
    def test_a_prefix_in_a_chinese_space_path_still_yields_the_distribution_facts(
        self, tmp_path, env_name
    ):
        prefix, site = _env(tmp_path, env_name)
        _dist(site, "python-docx", "1.1.2", top="docx\n", record=["docx/__init__.py"])
        index = _index(prefix)
        proj = _build(tmp_path, "proj 目录", {"s.py": "import docx\n"})
        res = importscan.scan(proj, "s.py", dists=index)
        (c,) = [c for c in res.classes if c.module == "docx"]
        assert c.distribution_status == "installed_confirmed"
        assert c.observed_distribution == "python-docx"
        assert str(tmp_path) not in json.dumps(index.to_payload(), ensure_ascii=False)


# ---------------------------------------------------------------- 2. 源码编码
class TestSourceEncodings:
    def _scan_bytes(self, tmp_path: Path, raw: bytes) -> importscan.ScanResult:
        root = _build(tmp_path, "论文", {"s.py": raw})
        return importscan.scan(root, "s.py")

    def test_a_gbk_declared_script_is_read_as_python_reads_it(self, tmp_path):
        raw = "# -*- coding: gbk -*-\n# 图一：实验结果\nimport yaml\nprint('中文')\n".encode("gbk")
        res = self._scan_bytes(tmp_path, raw)
        assert "yaml" in {c.module for c in res.classes}
        assert not res.problems

    def test_a_utf8_bom_script_is_read(self, tmp_path):
        raw = b"\xef\xbb\xbf" + "# 图一\nimport yaml\n".encode("utf-8")
        res = self._scan_bytes(tmp_path, raw)
        assert "yaml" in {c.module for c in res.classes} and not res.problems

    def test_a_latin1_declared_script_is_read(self, tmp_path):
        raw = "# -*- coding: latin-1 -*-\n# caf\xe9\nimport yaml\n".encode("latin-1")
        res = self._scan_bytes(tmp_path, raw)
        assert "yaml" in {c.module for c in res.classes} and not res.problems

    def test_an_unknown_codec_is_a_problem_not_a_crash_and_names_no_content(self, tmp_path):
        raw = b"# -*- coding: no-such-codec-zq9 -*-\nimport yaml\n"
        res = self._scan_bytes(tmp_path, raw)
        assert res.problems, "读不懂的编码应当记成问题"
        text = json.dumps(res.problems, ensure_ascii=False)
        assert "zq9" not in text and "yaml" not in text
        assert "yaml" not in {c.module for c in res.classes}  # 读不懂就不猜

    def test_bytes_that_are_not_valid_utf8_without_a_cookie_are_a_problem(self, tmp_path):
        raw = b"import yaml\nx = '\xff\xfe\xfa'\n"
        res = self._scan_bytes(tmp_path, raw)
        assert res.problems
        assert "yaml" not in json.dumps(res.problems)
        # 真实 Python 同样拒收（对拍：这不是我们更严）
        root = _build(tmp_path, "real", {"s.py": raw})
        assert _real_python(root, "s.py").returncode != 0


# ---------------------------------------------------------------- 3. Windows 形状（在任何宿主上模拟）
class TestWindowsShapesOnAnyHost:
    def test_a_record_with_backslash_paths_still_maps_the_import_name(self, tmp_path):
        prefix, site = _env(tmp_path)
        _dist(
            site, "python-docx", "1.1.2", top=None, record=["docx\\__init__.py", "docx\\oxml\\x.py"]
        )
        proj = _build(tmp_path, "proj", {"s.py": "import docx\n"})
        res = importscan.scan(proj, "s.py", dists=_index(prefix))
        (c,) = [c for c in res.classes if c.module == "docx"]
        assert c.distribution_status == "installed_confirmed"

    def test_a_pyvenv_cfg_with_a_drive_letter_and_a_chinese_user_name_does_not_break_the_layout(
        self, tmp_path
    ):
        prefix = tmp_path / "w"
        site = prefix / "Lib" / "site-packages"
        site.mkdir(parents=True)
        _write(
            prefix / "pyvenv.cfg",
            "home = C:\\Users\\张三\\AppData\\Local\\Programs\\Python\\Python313\r\n"
            "include-system-site-packages = false\r\nversion = 3.13.1\r\n",
        )
        _dist(site, "python-docx", "1.1.2", top="docx\n", record=["docx/__init__.py"])
        roots = distmeta.site_packages(prefix)
        assert [r.rel for r in roots] == ["Lib/site-packages"]
        proj = _build(tmp_path, "proj", {"s.py": "import docx\n"})
        res = importscan.scan(proj, "s.py", dists=distmeta.index_environment(prefix))
        (c,) = [c for c in res.classes if c.module == "docx"]
        assert c.distribution_status == "installed_confirmed"

    def test_a_pth_line_with_a_drive_letter_or_backslashes_is_never_a_real_path_here(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        _write(site / "legacy.pth", "C:\\Users\\张三\\lab\\src\n..\\..\\elsewhere\n")
        _dist(site, "python-docx", "1.1.2", top="docx\n", record=["docx/__init__.py"])
        index = _index(prefix)  # 不抛
        # 这些行在 POSIX 上只是「site 目录下的一个不存在的名字」：不被当成 site-packages 之外的目录去读
        assert all(Path(r.path).is_relative_to(prefix) for r in index.roots)
        proj = _build(tmp_path, "proj", {"s.py": "import docx\n"})
        res = importscan.scan(proj, "s.py", dists=index)
        (c,) = [c for c in res.classes if c.module == "docx"]
        # 没被跟进的路径行如实记账（`uncovered_paths`），不假装环境读全了
        assert c.distribution_status in {"installed_confirmed", "unverified"}

    def test_dist_info_directory_case_and_separators_normalise_to_one_distribution_key(
        self, tmp_path
    ):
        prefix, site = _env(tmp_path)
        d = site / "Python_Docx-1.1.2.dist-info"
        _write(
            d / "METADATA",
            "Metadata-Version: 2.1\nName: Python.Docx\nVersion: 1.1.2\n\n",
        )
        _write(d / "top_level.txt", "docx\n")
        _write(d / "RECORD", "docx/__init__.py,sha256=abc,1\n")
        proj = _build(tmp_path, "proj", {"s.py": "import docx\n"})
        res = importscan.scan(proj, "s.py", declared={"python-docx": ""}, dists=_index(prefix))
        (c,) = [c for c in res.classes if c.module == "docx"]
        assert c.distribution_status == "installed_confirmed"
        # 元数据里的显示名原样（`Python.Docx`），比较一律按 PEP 503 的同一个键：项目声明的 `python-docx` 认得出它
        assert depresolve.normalize_distribution(c.observed_distribution) == "python-docx"
        assert c.declared_requirement == "python-docx"
        assert "declared_matches_installed_candidate" in c.compatibility

    def test_the_host_platform_never_decides_which_extension_suffix_counts(self, tmp_path):
        """另一个平台的扩展后缀在本机文件系统上不是提供者；目标自己的后缀才是（目标事实由 depplan 量）。"""
        root = _build(
            tmp_path, "proj", {"s.py": "import fastmod\n", "fastmod.cp313-win_amd64.pyd": b"MZ"}
        )
        with_win = importscan.scan(
            root, "s.py", ext_suffixes=(".cp313-win_amd64.pyd", ".pyd", ".py")
        )
        no_win = importscan.scan(
            root, "s.py", ext_suffixes=(".cpython-313-darwin.so", ".abi3.so", ".so", ".py")
        )
        c1 = {c.module: c for c in with_win.classes}["fastmod"]
        c2 = {c.module: c for c in no_win.classes}["fastmod"]
        assert c1.origin_kind == "extension" and c1.bucket == "local"
        assert (
            c2.origin_kind != "extension"
        )  # 目标是 mac / Linux：`.pyd` 不是它的扩展，在每个宿主上都是同一个结论
