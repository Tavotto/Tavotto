"""`check_package_availability`（ADR 0221 §三，outward 档）。

判据的主语：**工具返回的 JSON 与它走过的执行器 / 网络桩**。一律不碰真实网络、不起真 pip：
安装器（dry-run）、环境探测、索引读取、受管环境供 Python 的判断都是注入的桩；索引是 tmp 目录里按真实 simple 页形状生成的
JSON / HTML，wheel 只有文件名没有内容。参数构造与镜像回退的接线另用 monkeypatch 钉「走的就是安装器那一份」。
"""

from __future__ import annotations

import json
import logging
import os

import pytest

import test_fixtools as _tf
from packaging import tags as ptags
from tavotto.engine import config, deprepair, packagecheck as pc, packageindex
from tavotto.engine.fixtools import REGISTRY, STAGE_OF_TOOL, AuditLog

box = _tf.box  # 同一个假主目录 / 数据目录夹具
call, make_ctx = _tf.call, _tf.make_ctx

# 假凭据运行时拼接（源码里不留完整形状，错题本 L-046）
TOKEN = "s3" + "cr3t-" + "idx-" + "tok3n9"
INDEX_WITH_CREDS = f"https://alice:{TOKEN}@idx.example.test/simple"

NAME = "phreeqpython"


def env_tags(minor: int, plat: str) -> list[str]:
    ver = (3, minor)
    out = list(ptags.cpython_tags(python_version=ver, platforms=[plat]))
    out += list(
        ptags.compatible_tags(python_version=ver, interpreter=f"cp3{minor}", platforms=[plat])
    )
    return [str(t) for t in out]


def write_index(root, name, files, *, html=False):
    """`files`: [(文件名, requires-python, yanked)]；写成 `<root>/<name>.json|html`。"""
    if html:
        rows = ""
        for fn, rp, y in files:
            attrs = (f' data-requires-python="{rp}"' if rp else "") + (" data-yanked" if y else "")
            rows += f'<a href="{fn}"{attrs}>{fn}</a><br>'
        (root / f"{name}.html").write_text(
            f"<!DOCTYPE html><html><body>{rows}</body></html>", "utf-8"
        )
    else:
        body = {
            "meta": {"api-version": "1.0"},
            "name": name,
            "files": [
                {
                    "filename": fn,
                    "url": f"https://files.example.test/{fn}",
                    "yanked": y,
                    "requires-python": rp,
                }
                for fn, rp, y in files
            ],
        }
        (root / f"{name}.json").write_text(json.dumps(body), "utf-8")


class Net:
    """假索引的读取：按 URL 末段的包名读 tmp 目录；记录每次请求（不含凭据的 URL + 头）。"""

    def __init__(self, root):
        self.root = root
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        name = url.rstrip("/").rsplit("/", 1)[-1]
        for ext, ctype in (("json", "application/vnd.pypi.simple.v1+json"), ("html", "text/html")):
            f = self.root / f"{name}.{ext}"
            if f.exists():
                return ctype, f.read_bytes()
        raise packageindex.IndexFetchError(packageindex.CODE_NOT_FOUND)


def report(version, name=NAME):
    return json.dumps(
        {
            "version": "1",
            "install": [{"metadata": {"name": name, "version": version}, "requested": True}]
            if version
            else [],
            "environment": {"python_full_version": "3.13.1"},
        },
        indent=4,
    )


class Stubs:
    def __init__(
        self,
        net,
        *,
        minor=13,
        plat="win_amd64",
        would="1.5.7",
        code="",
        index_url=None,
        offered=("3.12",),
    ):
        self.net = net
        self.minor, self.plat = minor, plat
        self.would, self.code = would, code
        self.index_url = INDEX_WITH_CREDS if index_url is None else index_url
        self.offered = set(offered)
        self.dry_calls: list[tuple] = []
        self.offered_asked: list[str] = []
        self.clock = [1_000_000.0]
        self.installed = ""
        self.out = None

    def hooks(self):
        def dry(python, canonical, cancel):
            self.dry_calls.append((python, canonical))
            out = self.out if self.out is not None else ("" if self.code else report(self.would))
            return {"code": self.code, "out": out, "source": "pypi", "index_url": self.index_url}

        def probe(python, canonical):
            return {
                "python": f"3.{self.minor}.1",
                "tags": env_tags(self.minor, self.plat),
                "installed": self.installed,
            }

        def offered(minor):
            self.offered_asked.append(minor)
            return minor in self.offered

        return pc.PackageCheckHooks(
            dry_run=dry,
            env_probe=probe,
            index_fetcher=self.net,
            python_offered=offered,
            now=lambda: self.clock[0],
        )


def approve_all(card):
    return {"answer": "yes"}


@pytest.fixture
def idx(tmp_path):
    root = tmp_path / "fake-index"
    root.mkdir()
    return root


def setup(box, idx, files, *, ask=approve_all, **kw):  # noqa: F811
    write_index(idx, NAME, files)
    net = Net(idx)
    st = Stubs(net, **kw)
    ctx = make_ctx(box, environments={"e1": "/fake/python"}, ask=ask)
    ctx.package_check = st.hooks()
    return ctx, st, net


def run(ctx, package=NAME, env="e1", audit=None):
    r = call(ctx, "check_package_availability", {"env": env, "package": package}, audit)
    return r, (json.loads(r.content) if not r.is_error else None)


PHREEQ = [
    (f"{NAME}-1.6.2-cp312-cp312-win_amd64.whl", ">=3.12", False),
    (f"{NAME}-1.5.7-py3-none-any.whl", "", False),
    (f"{NAME}-1.5.7.tar.gz", "", False),
]


# =========================================================================== 登记
def test_registered_as_outward_diagnose_tool_that_waits_for_the_user():
    tool = REGISTRY["check_package_availability"]
    assert tool.tier == "outward" and tool.waits_for_user
    assert STAGE_OF_TOOL["check_package_availability"] == "diagnose"


# =========================================================================== 判因
def test_latest_only_for_cp312_on_python_313_explains_and_suggests_312(box, idx):
    ctx, st, net = setup(box, idx, PHREEQ)
    r, out = run(ctx)
    assert not r.is_error, r.content
    assert out["would_install"] == "1.5.7"
    assert out["latest"] == "1.6.2"
    assert out["reason"] == "no_wheel_for_python"
    assert out["latest_requires"] == {"python_tags": ["cp312"], "platforms": ["win_amd64"]}
    assert out["suggest"] == {"tool": "change_python", "python": "3.12"}
    assert out["index"] == "pypi"
    # 候选只从 Tavotto 支持的区间里挑（3.14 最高、3.10 最低），并且不问当前版本
    assert set(st.offered_asked) <= {"3.14", "3.12", "3.11", "3.10"}
    assert "3.13" not in st.offered_asked


def test_no_suggestion_when_the_managed_environment_cannot_provide_that_python(box, idx):
    ctx, st, _ = setup(box, idx, PHREEQ, offered=())
    _, out = run(ctx)
    assert out["reason"] == "no_wheel_for_python" and "suggest" not in out


def test_up_to_date(box, idx):
    ctx, _, _ = setup(box, idx, PHREEQ, minor=12, would="1.6.2")
    _, out = run(ctx)
    assert out["reason"] == "up_to_date" and out["would_install"] == out["latest"] == "1.6.2"
    assert "suggest" not in out


def test_latest_has_wheels_but_only_for_another_platform(box, idx):
    ctx, _, _ = setup(box, idx, PHREEQ, minor=12, plat="macosx_14_0_arm64")
    _, out = run(ctx)
    assert out["reason"] == "no_wheel_for_platform" and "suggest" not in out
    assert out["latest_requires"]["platforms"] == ["win_amd64"]


def test_abi3_wheel_counts_for_newer_pythons(box, idx):
    files = [
        (f"{NAME}-2.0-cp39-abi3-win_amd64.whl", "", False),
        (f"{NAME}-1.0-py3-none-any.whl", "", False),
    ]
    ctx, _, _ = setup(box, idx, files, minor=13, would="2.0")
    _, out = run(ctx)
    assert out["reason"] == "up_to_date"


def test_requires_python_on_a_pure_wheel_blocks_old_pythons(box, idx):
    files = [
        (f"{NAME}-2.0-py3-none-any.whl", ">=3.13", False),
        (f"{NAME}-1.0-py3-none-any.whl", "", False),
    ]
    ctx, _, _ = setup(
        box, idx, files, minor=11, would="1.0", plat="win_amd64", offered=("3.14", "3.13")
    )
    _, out = run(ctx)
    assert out["reason"] == "no_wheel_for_python"
    assert out["suggest"]["python"] == "3.14"  # 区间内最高的、供得出的


def test_sdist_only_latest_counts_as_no_wheel_for_python(box, idx):
    files = [(f"{NAME}-2.0.tar.gz", "", False), (f"{NAME}-1.0-py3-none-any.whl", "", False)]
    ctx, _, _ = setup(box, idx, files, would="1.0")
    _, out = run(ctx)
    assert out["reason"] == "no_wheel_for_python" and out["latest"] == "2.0"
    assert out["latest_requires"] == {"python_tags": [], "platforms": []}


def test_prereleases_and_yanked_files_never_count_as_latest(box, idx):
    files = [
        (f"{NAME}-3.0rc1-py3-none-any.whl", "", False),
        (f"{NAME}-2.0-py3-none-any.whl", "", True),
        (f"{NAME}-1.0-py3-none-any.whl", "", False),
    ]
    ctx, _, _ = setup(box, idx, files, would="1.0")
    _, out = run(ctx)
    assert out["latest"] == "1.0" and out["reason"] == "up_to_date"


def test_a_usable_latest_wheel_the_installer_skipped_is_held_back_not_a_guess(box, idx):
    ctx, _, _ = setup(box, idx, PHREEQ, minor=12, would="1.5.7")
    _, out = run(ctx)
    assert out["reason"] == "held_back" and "suggest" not in out


def test_already_installed_version_is_what_would_be_installed(box, idx):
    ctx, st, _ = setup(box, idx, PHREEQ, minor=12, would=None)
    st.installed = "1.6.2"
    _, out = run(ctx)
    assert out["would_install"] == "1.6.2" and out["already_installed"] == "1.6.2"
    assert out["reason"] == "up_to_date"


def test_pep503_html_page_is_read_when_the_index_has_no_json(box, idx):
    write_index(idx, NAME, PHREEQ, html=True)
    ctx = make_ctx(box, environments={"e1": "/fake/python"}, ask=approve_all)
    st = Stubs(Net(idx))
    ctx.package_check = st.hooks()
    _, out = run(ctx)
    assert out["latest"] == "1.6.2" and out["reason"] == "no_wheel_for_python"


# =========================================================================== 查不到 / 连不上
def test_not_found(box, idx):
    ctx, _, _ = setup(box, idx, PHREEQ, code=deprepair.ERROR_NOT_FOUND)
    _, out = run(ctx, package="no-such-package-xyz")
    assert out["reason"] == "not_found" and out["would_install"] is None and out["latest"] is None


def test_index_unreachable_from_the_installer(box, idx):
    ctx, st, net = setup(box, idx, PHREEQ, code=deprepair.ERROR_NETWORK)
    _, out = run(ctx)
    assert out["reason"] == "index_unreachable"
    assert net.calls == []  # 安装器都没连上，不再多打一次


def test_index_unreachable_when_the_page_cannot_be_read(box, idx):
    ctx, st, net = setup(box, idx, PHREEQ)

    def boom(url, headers):
        raise packageindex.IndexFetchError(packageindex.CODE_UNREACHABLE)

    st.net = boom
    ctx.package_check = st.hooks()
    _, out = run(ctx)
    assert out["reason"] == "index_unreachable" and out["would_install"] == "1.5.7"


def test_a_user_source_without_a_readable_index_url_says_unreachable(box, idx):
    ctx, st, _ = setup(box, idx, PHREEQ, index_url="")
    _, out = run(ctx)
    assert out["reason"] == "index_unreachable" and out["would_install"] == "1.5.7"


def test_plain_http_index_is_never_read():
    assert packageindex.project_url("http://idx.example.test/simple", "x") is None
    with pytest.raises(packageindex.IndexFetchError):
        packageindex.project_files(
            "http://idx.example.test/simple", "x", fetcher=lambda *a: pytest.fail("fetched")
        )


def test_installer_failures_other_than_net_and_not_found_are_tool_errors(box, idx):
    ctx, st, _ = setup(box, idx, PHREEQ, code=deprepair.ERROR_CONFLICT)
    st.out = f"ERROR: Cannot install because of conflicting dependencies via https://alice:{TOKEN}@idx.example.test/simple"
    r, _ = run(ctx)
    assert r.is_error and r.code == "check_failed"
    assert TOKEN not in r.content


def test_bad_arguments_and_unknown_env_do_not_bother_the_user(box, idx):
    asked = []
    ctx, st, _ = setup(box, idx, PHREEQ, ask=lambda c: asked.append(c) or {"answer": "yes"})
    assert run(ctx, env="nope")[0].code == "env_unknown"
    for bad in ("scipy>=1", "-r x.txt", "https://x/y", "a b", ""):
        assert run(ctx, package=bad)[0].code == "bad_arguments", bad
    assert asked == [] and st.dry_calls == []


# =========================================================================== 凭据
def test_index_credentials_stay_out_of_everything(box, idx, caplog, tmp_path):
    ctx, st, net = setup(box, idx, PHREEQ)
    audit = AuditLog(box.data / "ai" / "fix_audit.jsonl")
    with caplog.at_level(logging.DEBUG):
        r, _ = run(ctx, audit=audit)
    assert not r.is_error
    # 凭据确实被用来读这个索引（否则「不在输出里」可能只是没用到）：Basic 头到了假索引，URL 里没有
    ((url, headers),) = net.calls
    assert "Authorization" in headers and TOKEN not in url and "alice" not in url
    assert url == f"https://idx.example.test/simple/{NAME}/"
    # 结果、审计、日志、缓存文件里都没有
    assert TOKEN not in r.content and "idx.example.test" not in r.content
    assert TOKEN not in audit.path.read_text("utf-8")
    assert TOKEN not in caplog.text
    cached = list(packageindex.cache_dir().glob("*"))
    assert cached and all(
        TOKEN not in f.read_text("utf-8") and "alice" not in f.read_text("utf-8") for f in cached
    )
    assert all(TOKEN not in str(f) for f in cached)


# =========================================================================== 缓存
def test_index_facts_are_cached_for_an_hour_per_index_and_package(box, idx):
    ctx, st, net = setup(box, idx, PHREEQ)
    run(ctx)
    assert len(net.calls) == 1
    # 同一个索引、同一个包：别的环境（另一个 Python）直接命中缓存，但判因仍按**它自己的**标签算
    st.minor = 12
    st.would = "1.6.2"
    ctx.package_check = st.hooks()
    _, out = run(ctx)
    assert len(net.calls) == 1 and out["reason"] == "up_to_date"
    # 这是与环境无关的索引事实：缓存里没有 would_install / 环境标签
    (f,) = list(packageindex.cache_dir().glob("*.json"))
    assert set(json.loads(f.read_text("utf-8"))) == {"t", "files"}
    # 过了一小时重读
    st.clock[0] += packageindex.CACHE_TTL_S + 1
    run(ctx)
    assert len(net.calls) == 2
    # 换一个包名不命中
    write_index(idx, "other-pkg", [("other_pkg-1.0-py3-none-any.whl", "", False)])
    run(ctx, package="other-pkg")
    assert len(net.calls) == 3


def test_a_failed_read_is_not_cached(box, idx):
    ctx, st, net = setup(box, idx, PHREEQ, code=deprepair.ERROR_NOT_FOUND)
    run(ctx, package="ghost")
    run(ctx, package="ghost")
    assert len(net.calls) == 2 and not list(packageindex.cache_dir().glob("*.json"))


# =========================================================================== 审批
def test_first_use_asks_the_user_once_per_session(box, idx):
    cards = []
    ctx, st, _ = setup(box, idx, PHREEQ, ask=lambda c: cards.append(c) or {"answer": "yes"})
    audit = AuditLog(box.data / "ai" / "fix_audit.jsonl")
    run(ctx, audit=audit)
    run(ctx, audit=audit)
    assert len(cards) == 1
    assert cards[0]["kind"] == "approve" and cards[0]["tool"] == "check_package_availability"
    assert "Only the package name is sent" in cards[0]["summary"]
    rows = [json.loads(x) for x in audit.path.read_text("utf-8").splitlines()]
    assert [r["approved_by"] for r in rows] == ["user", "policy"]


def test_declined_or_no_interface_means_nothing_is_asked_of_the_network(box, idx):
    ctx, st, net = setup(box, idx, PHREEQ, ask=lambda c: {"answer": "no"})
    r, _ = run(ctx)
    assert r.code == "needs_approval" and st.dry_calls == [] and net.calls == []
    assert ctx.package_check_approved is False
    ctx2, st2, net2 = setup(box, idx, PHREEQ, ask=None)
    r2, _ = run(ctx2)
    assert r2.code == "needs_approval" and st2.dry_calls == [] and net2.calls == []


def test_a_read_only_check_does_not_void_a_passed_verification(box, idx):
    ctx, _, _ = setup(box, idx, PHREEQ)
    ctx.verification = {"ok": True}
    run(ctx)
    assert ctx.verification == {"ok": True}


# =========================================================================== 与安装器同一份参数 / 回退
def test_dry_run_uses_the_installers_own_argv_builder_and_fallback(monkeypatch):
    seen = {}

    def fake(build_argv, python, cancel, on_log, **kw):
        seen["official"] = build_argv(None)
        seen["mirror"] = build_argv(deprepair.PYPI_MIRROR_URL)
        seen["kw"] = kw
        path = seen["official"][seen["official"].index("--report") + 1]
        seen["path"] = path
        with open(
            path, "w", encoding="utf-8"
        ) as fh:  # 像 pip 一样把报告写进文件；stdout 只有进度行
            fh.write(report("1.0"))
        kw["on_source"](deprepair.PIP_SOURCE_MIRROR)
        return "", "Collecting scipy\n"

    monkeypatch.setattr(deprepair, "_run_pip_install", fake)
    import threading

    res = pc.default_dry_run("/py", "scipy", threading.Event())
    path = seen["path"]
    assert seen["official"] == deprepair.pip_install_argv("/py", "scipy", dry_run_report=path)
    assert seen["mirror"] == deprepair.pip_install_argv(
        "/py", "scipy", index_url=deprepair.PYPI_MIRROR_URL, dry_run_report=path
    )
    assert seen["official"][-4:] == ["--dry-run", "--report", path, "scipy"]
    assert "--quiet" not in seen["official"] and "-q" not in seen["official"]
    assert "-" != path and path.startswith(str(config.data_dir() / "cache"))
    # 报告从文件读（stdout 里只有进度行）；用完即删
    assert pc.reported_version(pc.parse_report(res["out"]), NAME) == "1.0"
    assert not os.path.exists(path)
    assert "--only-binary=:all:" in seen["official"] and "--upgrade" not in seen["official"]
    assert seen["kw"]["budget_s"] == pc.DRY_RUN_BUDGET_S
    # 镜像那一次读的就是镜像的 simple 页
    assert (
        res["source"] == deprepair.PIP_SOURCE_MIRROR
        and res["index_url"] == deprepair.PYPI_MIRROR_URL
    )


def test_dry_run_progress_lines_still_feed_the_watchdog(monkeypatch):
    """不加 `--quiet`：解析慢但有进度行的健康 dry-run，`_PipWatch` 的「卡住」计时被每一行刷新，不会在 PIP_STALL_S 被杀。"""
    monkeypatch.setattr(deprepair, "PIP_STALL_S", 90.0)
    w = deprepair._PipWatch(0.0, 1e9)
    for t in (0.0, 60.0, 120.0, 180.0):  # 总共 180 s，但每 60 s 有一行进度
        w.feed("Collecting scipy\n", t)
        assert w.verdict(t + 59.0) == ""
    quiet = deprepair._PipWatch(0.0, 1e9)  # 对照：--quiet 时没有任何行，90 s 就被判卡住
    assert quiet.verdict(95.0) == deprepair.PIP_STALLED


def test_default_installer_argv_is_unchanged():
    assert deprepair.pip_install_argv("/py", "scipy") == [
        "/py",
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--only-binary=:all:",
        "scipy",
    ]


def test_report_is_found_among_other_output():
    noisy = "WARNING: something\n" + report("2.0") + "\ntrailing {not json\n"
    assert pc.reported_version(pc.parse_report(noisy), NAME) == "2.0"
    assert pc.parse_report("no json here") is None
    assert pc.reported_version(pc.parse_report(report(None)), NAME) is None


def test_the_probe_runs_in_the_target_environment_not_the_parent(monkeypatch):
    seen = {}

    def fake_run(argv, timeout):
        seen["argv"] = argv
        return 0, json.dumps(
            {"python": "3.12.1", "tags": ["cp312-cp312-win_amd64"], "installed": ""}
        )

    monkeypatch.setattr(deprepair, "_run", fake_run)
    facts = pc.default_env_probe("/target/python", "scipy")
    assert seen["argv"][0] == "/target/python" and seen["argv"][-1] == "scipy"
    assert "pip._vendor" in pc.ENV_PROBE and facts["python"] == "3.12.1"


def test_the_cache_lives_under_the_data_dir(box):
    assert packageindex.cache_dir() == box.data / "cache" / "package_availability"


# =========================================================================== requires-python 按完整解释器版本判
def _patch_rel(spec, plat="win_amd64"):
    files = [(f"{NAME}-1.6.2-cp312-cp312-{plat}.whl", spec, False)]
    return pc.latest_release(pc.group_releases(_index_files(files), NAME))


def _index_files(files):
    from tavotto.engine import packageindex as pi

    return [pi.IndexFile(filename=fn, yanked=y, requires_python=rp) for fn, rp, y in files]


def _env():
    return {x for t in env_tags(13, "win_amd64") for x in ptags.parse_tag(t)}


def test_patch_level_lower_bound_is_judged_on_the_full_version_the_environment_would_use():
    rel = _patch_rel(">=3.12.1,<3.13")
    assert (
        pc.suggest_python(rel, "3.13.1", _env(), lambda m: "3.12.10" if m == "3.12" else False)
        == "3.12"
    )
    # 实际只供得出 3.12.0：下限 3.12.1 不满足，不推荐
    assert (
        pc.suggest_python(rel, "3.13.1", _env(), lambda m: "3.12.0" if m == "3.12" else False)
        is None
    )


def test_patch_level_upper_bound_excludes_a_too_new_actual_interpreter():
    rel = _patch_rel(">=3.12,<3.12.5")
    assert (
        pc.suggest_python(rel, "3.13.1", _env(), lambda m: "3.12.10" if m == "3.12" else False)
        is None
    )
    assert (
        pc.suggest_python(rel, "3.13.1", _env(), lambda m: "3.12.4" if m == "3.12" else False)
        == "3.12"
    )


def test_without_a_full_version_a_patch_level_constraint_is_not_suggested():
    env = _env()
    only_true = lambda m: m == "3.12"  # noqa: E731 - 能供应但不知道完整版本
    assert pc.suggest_python(_patch_rel(">=3.12.1,<3.13"), "3.13.1", env, only_true) is None
    assert pc.suggest_python(_patch_rel("<3.12.5"), "3.13.1", env, only_true) is None
    # 小版本级约束照旧可推荐
    assert pc.suggest_python(_patch_rel(">=3.12"), "3.13.1", env, only_true) == "3.12"
