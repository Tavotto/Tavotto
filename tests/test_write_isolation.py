"""写回校验的隔离重放（QA-WIN-51，Codex #1697 P1 ×2）：只把「写回目标」的写换到暂存副本。

`install_isolation` 会包装 `builtins.open` 等——那是一次性 worker 子进程的事，所以每条用例都在隔离的子进程里
跑一段脚本，打印 JSON 结果。真 worker + Flask 的链路用例在 `test_worker_roundtrip.py`。"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tavotto.engine import writetripwire

SRC = str(Path(writetripwire.__file__).resolve().parents[2])

PRELUDE = """
import json, os, pathlib, shutil, sys
sys.path.insert(0, {src!r})
from tavotto.engine import writetripwire as w
base = pathlib.Path({base!r})
target = base / "Fig.png"
other = base / "other.txt"
target.write_bytes(b"ORIGINAL")
other.write_bytes(b"OTHER")
w.install_isolation(str(base / "_iso"), [str(target)])
w.arm(True)
out = {{}}
"""


def _run(tmp_path: Path, body: str) -> dict:
    code = PRELUDE.format(src=SRC, base=str(tmp_path)) + textwrap.dedent(body)
    code += "\nw.arm(False)\nprint('RESULT' + json.dumps(out))\n"
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    line = [ln for ln in done.stdout.splitlines() if ln.startswith("RESULT")][-1]
    return json.loads(line[len("RESULT") :])


def test_open_write_modes_are_redirected_and_reads_see_the_copy(tmp_path):
    out = _run(
        tmp_path,
        """
        with open(target, "wb") as h:
            h.write(b"NEW")
        out["read_back"] = open(target, "rb").read().decode()
        with open(other, "ab") as h:
            h.write(b"+more")
        out["other"] = other.read_bytes().decode()
        w.arm(False)
        out["orig"] = target.read_bytes().decode()
        out["redirected"] = w.redirected()
        """,
    )
    assert out["read_back"] == "NEW", "脚本读回自己写的内容（保持自洽）"
    assert out["orig"] == "ORIGINAL"
    assert out["other"] == "OTHER+more", "非写回目标的写原样"
    assert out["redirected"] == ["Fig.png"]


def test_append_and_plus_modes_copy_on_write(tmp_path):
    out = _run(
        tmp_path,
        """
        with open(target, "ab") as h:
            h.write(b"+A")
        w.arm(False)
        out["orig"] = target.read_bytes().decode()
        w.arm(True)
        out["via"] = open(target, "rb").read().decode()
        """,
    )
    assert out["orig"] == "ORIGINAL" and out["via"] == "ORIGINAL+A"


def test_os_open_path_write_text_and_bytes_are_redirected(tmp_path):
    out = _run(
        tmp_path,
        """
        fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o666)
        os.write(fd, b"VIA-OS-OPEN"); os.close(fd)
        out["a"] = target.read_bytes().decode()
        target.write_bytes(b"VIA-PATH")
        out["b"] = target.read_bytes().decode()
        w.arm(False)
        out["orig"] = target.read_bytes().decode()
        """,
    )
    assert out["a"] == "VIA-OS-OPEN" and out["b"] == "VIA-PATH" and out["orig"] == "ORIGINAL"


def test_os_replace_and_shutil_move_copy_onto_the_target_are_redirected(tmp_path):
    out = _run(
        tmp_path,
        """
        part = base / "Fig.png.part"
        part.write_bytes(b"R1"); os.replace(part, target)
        out["after_replace"] = target.read_bytes().decode()
        out["part_gone"] = not part.exists()
        part.write_bytes(b"M2"); shutil.move(str(part), str(target))
        out["after_move"] = target.read_bytes().decode()
        shutil.copyfile(other, target)
        out["after_copy"] = target.read_bytes().decode()
        shutil.copy2(other, target)
        out["after_copy2"] = target.read_bytes().decode()
        w.arm(False)
        out["orig"] = target.read_bytes().decode()
        out["other_kept"] = other.exists()
        """,
    )
    assert out["after_replace"] == "R1" and out["part_gone"]
    assert out["after_move"] == "M2"
    assert out["after_copy"] == "OTHER" and out["after_copy2"] == "OTHER"
    assert out["orig"] == "ORIGINAL" and out["other_kept"]


def test_relative_path_after_chdir_and_dotdot_spelling_still_match(tmp_path):
    out = _run(
        tmp_path,
        """
        (base / "sub").mkdir()
        os.chdir(base / "sub")
        with open("../Fig.png", "wb") as h:
            h.write(b"REL")
        with open(str(base / "sub" / ".." / "Fig.png"), "ab") as h:
            h.write(b"+DOTDOT")
        w.arm(False)
        out["orig"] = (base / "Fig.png").read_bytes().decode()
        w.arm(True)
        out["via"] = open(base / "Fig.png", "rb").read().decode()
        """,
    )
    assert out["orig"] == "ORIGINAL" and out["via"] == "REL+DOTDOT"


def test_symlink_alias_of_the_target_is_redirected(tmp_path):
    if sys.platform == "win32":
        pytest.skip("符号链接需要特权")
    out = _run(
        tmp_path,
        """
        link = base / "alias.png"
        os.symlink(target, link)
        with open(link, "wb") as h:
            h.write(b"VIA-LINK")
        w.arm(False)
        out["orig"] = target.read_bytes().decode()
        """,
    )
    assert out["orig"] == "ORIGINAL"


def test_unfaithful_operations_on_a_target_fail_closed(tmp_path):
    out = _run(
        tmp_path,
        """
        errs = []
        for op in (
            lambda: os.truncate(target, 0),
            lambda: os.link(target, base / "hard.png"),
            lambda: os.symlink("x", target),
        ):
            try:
                op()
                errs.append("no-error")
            except PermissionError:
                errs.append("denied")
            except OSError as exc:
                errs.append(type(exc).__name__)
        out["errs"] = errs
        out["violations"] = len(w.violations())
        w.arm(False)
        out["orig"] = target.read_bytes().decode()
        """,
    )
    assert out["errs"] == ["denied", "denied", "denied"], out
    assert out["orig"] == "ORIGINAL"


def test_other_files_keep_their_behaviour_no_tripwire_roots(tmp_path):
    out = _run(
        tmp_path,
        """
        outside = pathlib.Path({outside!r})
        outside.write_bytes(b"x")  # 授权根之外的写：隔离模式没有绊线
        os.remove(other)
        out["other_removed"] = not other.exists()
        (base / "new").mkdir()
        shutil.rmtree(base / "new")
        w.arm(False)
        out["outside"] = outside.read_bytes().decode()
        out["violations"] = w.violations()
        """.format(outside=str(tmp_path.parent / f"{tmp_path.name}-outside.bin")),
    )
    assert out["other_removed"] and out["outside"] == "x" and out["violations"] == []


def test_deleting_the_target_only_whites_it_out(tmp_path):
    out = _run(
        tmp_path,
        """
        os.remove(target)
        out["exists_in_script"] = os.path.exists(target)
        w.arm(False)
        out["orig_kept"] = target.read_bytes().decode()
        """,
    )
    assert out["exists_in_script"] is False and out["orig_kept"] == "ORIGINAL"


def test_entries_round_trip_and_misconfiguration():
    entries = writetripwire.isolate_entries("/d/iso", ["/p/a.png", "/p/a.pdf"])
    rest, iso = writetripwire.split_isolation(("/root", *entries))
    assert rest == ["/root"] and iso == ("/d/iso", ["/p/a.png", "/p/a.pdf"])
    assert writetripwire.split_isolation(("/a", "/b")) == (["/a", "/b"], None)
    with pytest.raises(ValueError):
        writetripwire.split_isolation((writetripwire.ISOLATE_DIR_PREFIX + "/d",))
    with pytest.raises(ValueError):
        writetripwire.split_isolation((writetripwire.ISOLATE_FILE_PREFIX + "/p/a.png",))
