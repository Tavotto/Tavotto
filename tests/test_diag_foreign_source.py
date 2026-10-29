"""发送问题反馈「附带源码」收 R / MATLAB 与转换任务的候选脚本（ADR 0118 补充 2026-10-30）。

判据的主语：**包里 optional-source.zip 的成员**——来源（转换任务的原脚本、候选目录里留着的候选）与脱敏（R / MATLAB
文本里的家目录 / 用户名 / 秘密）。脚本内容全是合成的，不含任何真实用户脚本。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from tavotto.engine import ai_convert, ai_history, diagdetail

SID = "0123456789ab"
SID2 = "ba9876543210"
PROJECT_KEY_NAME = "proj"

R_SCRIPT = """# loveplot: data in /Users/someone/Desktop/Some Project/data.xlsx (someone)
library(ggplot2)
xlsx <- "~/Desktop/Some Project/data_2026.xlsx"
home_csv <- path.expand("~/Desktop/x.csv")
out <- 'C:\\\\Users\\\\someone\\\\Documents\\\\out.pdf'
api_key <- "sk-CANARYSECRETKEY1234567890"
password = 'CANARY_PASSWORD_VALUE'
"""

M_SCRIPT = """% data from /Users/someone/Desktop/a.csv by someone
%{
block comment C:\\Users\\someone\\Desktop\\b.xlsx
password=CANARY_BLOCK_SECRET
%}
f = 'C:\\Users\\someone\\Desktop\\a.xlsx';
t = readtable("/Users/someone/Desktop/a.xlsx", ...
   'x');
apiKey = 'sk-CANARYSECRETKEY1234567890';
"""


@pytest.fixture
def home(monkeypatch):
    monkeypatch.setenv("HOME", "/Users/someone")
    monkeypatch.setenv("USERPROFILE", "C:\\Users\\someone")
    for k in ("USER", "LOGNAME", "USERNAME"):
        monkeypatch.setenv(k, "someone")


@pytest.fixture
def world(tmp_path, monkeypatch):
    proj = tmp_path / PROJECT_KEY_NAME
    proj.mkdir()
    cdir = tmp_path / "ai_convert"
    cdir.mkdir()
    monkeypatch.setattr(ai_convert, "CONVERT_DIR", cdir)
    monkeypatch.setattr(ai_history, "DB_PATH", tmp_path / "ai_history.sqlite3")
    return proj, cdir


def _session(
    proj: Path,
    sid: str,
    script: str,
    outcome: str | None,
    *,
    task_type="convert_to_editable",
    project=None,
):
    ai_history.record_start(
        {
            "id": sid,
            "project": project or str(proj.resolve()),
            "provider": "claude",
            "script": script,
            "task_type": task_type,
        }
    )
    ai_history.record_end(sid, "done", task_result={"outcome": outcome} if outcome else None)


def _candidate(cdir: Path, proj: Path, sid: str, script: str, body: str, **meta) -> Path:
    d = cdir / sid
    d.mkdir()
    name = Path(script).stem + "_matplotlib.py"
    (d / name).write_text(body, encoding="utf-8")
    (d / Path(script).name).write_text("ORIGINAL_COPY_CANARY\n", encoding="utf-8")  # 原脚本副本不收
    (d / "meta.json").write_text(
        json.dumps(
            {
                "sid": sid,
                "project": str(proj.resolve()),
                "script_rel": script,
                "target_name": name,
                **meta,
            }
        ),
        encoding="utf-8",
    )
    return d / name


def _rels(src, **kw):
    reasons: dict[str, str] = {}
    got = diagdetail.source_candidates(src, reasons=reasons, **kw)
    return [r for r, _p in got], reasons


class TestConvertSources:
    def test_failed_r_conversion_original_and_candidate_are_attached(self, world):
        proj, cdir = world
        (proj / "loveplot.R").write_text(R_SCRIPT, encoding="utf-8")
        (proj / "other.py").write_text("OTHER = 1\n", encoding="utf-8")
        cand = _candidate(cdir, proj, SID, "loveplot.R", "CANDIDATE_CANARY = 1\n")
        _session(proj, SID, "loveplot.R", "failed")
        rows = ai_convert.recent_attempts(str(proj.resolve()), 3600)
        assert [(r["script"], r["failed"], r["candidate"]) for r in rows] == [
            ("loveplot.R", True, cand)
        ]
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            scripts=["other.py"],
            converts=[
                diagdetail.ConvertAttempt(r["sid"], r["script"], r["failed"], r["candidate"])
                for r in rows
            ],
        )
        rels, reasons = _rels(src)
        assert rels == ["loveplot.R", f"convert-candidate/{SID}/loveplot_matplotlib.py", "other.py"]
        assert reasons["loveplot.R"] == "failing_attempt"
        assert reasons[rels[1]] == "convert_candidate"
        assert reasons["other.py"] == "recent"

    def test_zip_members_never_carry_an_absolute_path(self, world, home):
        proj, cdir = world
        (proj / "loveplot.R").write_text(R_SCRIPT, encoding="utf-8")
        cand = _candidate(cdir, proj, SID, "loveplot.R", "CANDIDATE_CANARY = 1\n")
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            converts=[diagdetail.ConvertAttempt(SID, "loveplot.R", True, cand)],
        )
        items = diagdetail.collect_source(
            src, diagdetail.DetailOptions(source=True), diagdetail.Scrubber()
        )
        names = [i.path for i in items]
        blob = names + [i.data.decode() for i in items if i.path == "index.json"]
        assert any(n.endswith("loveplot_matplotlib.py") for n in names)
        for text in blob:
            assert str(cdir) not in text and str(proj) not in text
        body = {i.path: i.data.decode() for i in items}
        assert any("CANDIDATE_CANARY" in v for v in body.values())
        assert not any("ORIGINAL_COPY_CANARY" in v for v in body.values())

    def test_other_project_other_task_adopted_and_traversal_are_not_collected(self, world):
        proj, cdir = world
        (proj / "a.R").write_text("a <- 1\n", encoding="utf-8")
        mine = _candidate(cdir, proj, SID, "a.R", "MINE = 1\n")
        _session(proj, SID, "a.R", "completed")
        # 别的项目的会话 / 别的任务类型的会话：查询就看不到
        _session(proj, SID2, "a.R", "failed", project="/elsewhere")
        _session(proj, "aaaaaaaaaaaa", "a.R", "failed", task_type="figure_edit")
        rows = ai_convert.recent_attempts(str(proj.resolve()), 3600)
        assert [r["sid"] for r in rows] == [SID] and rows[0]["failed"] is False
        assert rows[0]["candidate"] == mine
        # meta 说是别的项目 / 已采用 / target_name 带路径：候选一律 None
        for patch in (
            {"project": "/elsewhere"},
            {"adopted_script": "a_matplotlib.py"},
            {"target_name": "../x.py"},
        ):
            (cdir / SID / "meta.json").write_text(
                json.dumps(
                    {
                        "sid": SID,
                        "project": str(proj.resolve()),
                        "target_name": "a_matplotlib.py",
                        **patch,
                    }
                ),
                encoding="utf-8",
            )
            assert ai_convert.recent_attempts(str(proj.resolve()), 3600)[0]["candidate"] is None, (
                patch
            )

    def test_symlinked_candidate_and_candidate_dir_are_refused(self, world, tmp_path):
        proj, cdir = world
        (proj / "a.R").write_text("a <- 1\n", encoding="utf-8")
        outside = tmp_path / "outside.py"
        outside.write_text("OUTSIDE_CANARY = 1\n", encoding="utf-8")
        d = cdir / SID
        d.mkdir()
        os.symlink(outside, d / "a_matplotlib.py")
        (d / "meta.json").write_text(
            json.dumps(
                {"sid": SID, "project": str(proj.resolve()), "target_name": "a_matplotlib.py"}
            ),
            encoding="utf-8",
        )
        _session(proj, SID, "a.R", "failed")
        assert ai_convert.recent_attempts(str(proj.resolve()), 3600)[0]["candidate"] is None
        # 即使调用方把符号链接递了进来，收集时也不收
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            converts=[diagdetail.ConvertAttempt(SID, "a.R", True, d / "a_matplotlib.py")],
        )
        rels, _ = _rels(src)
        assert rels == ["a.R"]
        # 候选目录本身是符号链接
        real = tmp_path / "real_dir"
        real.mkdir()
        (real / "a_matplotlib.py").write_text("X = 1\n", encoding="utf-8")
        (real / "meta.json").write_text((d / "meta.json").read_text(), encoding="utf-8")
        (d / "a_matplotlib.py").unlink()
        (d / "meta.json").unlink()
        d.rmdir()
        os.symlink(real, cdir / SID)
        assert ai_convert.recent_attempts(str(proj.resolve()), 3600)[0]["candidate"] is None

    def test_original_outside_project_or_unsupported_suffix_is_not_collected(self, world, tmp_path):
        proj, _cdir = world
        (proj / "notes.Rmd").write_text("RMD_CANARY\n", encoding="utf-8")
        (tmp_path / "out.R").write_text("OUT_CANARY\n", encoding="utf-8")
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            converts=[
                diagdetail.ConvertAttempt(SID, "notes.Rmd", True),
                diagdetail.ConvertAttempt(SID2, "../out.R", True),
            ],
        )
        assert _rels(src)[0] == []

    def test_sessions_outside_the_retention_window_are_ignored(self, world):
        proj, _cdir = world
        _session(proj, SID, "a.R", "failed")
        assert ai_convert.recent_attempts(str(proj.resolve()), 3600)
        time.sleep(0.01)
        assert ai_convert.recent_attempts(str(proj.resolve()), 0) == []


class TestForeignScriptsAreScrubbed:
    """R / MATLAB 走 `scrub_script` 的非 `.py` 分支（整份按完整模式）：注释、字符串、代码里的家目录 / 用户名 / 秘密同一口径。"""

    @pytest.mark.parametrize(
        ("name", "text"), [("loveplot.R", R_SCRIPT), ("fig.m", M_SCRIPT), ("lower.r", R_SCRIPT)]
    )
    def test_home_user_and_secrets_are_gone(self, name, text, tmp_path, home):
        proj = tmp_path / "p"
        proj.mkdir()
        (proj / name).write_text(text, encoding="utf-8")
        src = diagdetail.Sources(
            project={"figures_dir": str(proj)},
            converts=[diagdetail.ConvertAttempt(SID, name, True)],
        )
        items = diagdetail.collect_source(
            src, diagdetail.DetailOptions(source=True), diagdetail.Scrubber()
        )
        out = next(i.data.decode() for i in items if i.path.endswith(name))
        for canary in (
            "/Users/someone",
            "C:\\Users\\someone",
            "someone",
            "CANARY_PASSWORD_VALUE",
            "CANARY_BLOCK_SECRET",
            "sk-CANARYSECRETKEY1234567890",
        ):
            assert canary not in out, canary
        assert "~" in out  # 反证：家目录确实被换成了 ~，不是什么都没读到
        assert "<redacted>" in out
        # 不含用户信息的正文原样保留（不是整份抹掉）
        assert ("library(ggplot2)" in out) or ("readtable" in out)
