"""发行量采集器：资产分类、快照语义、自愈窗口与「下载 ≠ 用户」的纪律。

**没有任何一条用例访问 GitHub / PyPI / 代理**——全部走 tests/fixtures/ 里的
离线样本。分类规则是从真实发布工作流推出来的，样本里因此覆盖了
release.yml 与 desktop-tauri.yml 会挂上去的每一类资产。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

if not (ROOT / "scripts" / "collect_distribution_metrics.py").is_file():
    # scripts/ 不进 wheel/sdist；源码树以外跑 pytest 时整个模块跳过
    pytest.skip("没有 scripts/（wheel/sdist 里不含构建与采集脚本）", allow_module_level=True)


def _load_collector():
    """按路径加载脚本（scripts/ 不是包，也不该为了测试变成包）。"""
    path = ROOT / "scripts" / "collect_distribution_metrics.py"
    spec = importlib.util.spec_from_file_location("collect_distribution_metrics", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


collector = _load_collector()
GITHUB_FIXTURE = FIXTURES / "github_releases.json"
PYPI_FIXTURE = FIXTURES / "pypistats_overall.json"


@pytest.fixture
def events():
    return collector.collect(
        "2026-08-20",
        github_token=None,
        github_json=str(GITHUB_FIXTURE),
        pypi_json=str(PYPI_FIXTURE),
    )


def _gh(events):
    return [e for e in events if e["event"] == "github_release_asset_snapshot"]


def _by_name(name: str, events):
    data = json.loads(GITHUB_FIXTURE.read_text(encoding="utf-8"))
    for release in data["releases"]:
        for asset in release["assets"]:
            if asset["name"] == name:
                return next(e for e in _gh(events) if e["properties"]["asset_id"] == asset["id"])
    raise AssertionError(f"fixture 里没有 {name}")


# ---------------------------------------------------------------------------
# 资产分类：这一条决定「有多少人装过」是不是真话
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name,role,platform",
    [
        ("Tavotto-0.8.0-macOS.dmg", "installer", "macos"),
        ("Tavotto-0.8.0-Windows-Setup.exe", "installer", "windows"),
        ("Tavotto.app.tar.gz", "updater", "macos"),
        ("Tavotto.app.tar.gz.sig", "checksum", "macos"),
        ("Tavotto_0.8.0_x64-setup.nsis.zip", "updater", "windows"),
        ("Tavotto_0.8.0_x64-setup.nsis.zip.sig", "checksum", "windows"),
        ("latest.json", "update_check", "any"),
        ("tavotto-0.8.0-py3-none-any.whl", "wheel", "any"),
        ("tavotto-0.8.0.tar.gz", "sdist", "any"),
        ("codex-plugin.json", "plugin_manifest", "any"),
        ("codex-plugin.json.sha256", "checksum", "any"),
        ("codex-plugin-0.8.0.zip", "plugin", "any"),
        # 兜底之前就被拦下：漏到 .tar.gz 那条会变成 sdist，等于把插件流量
        # 混进 Python 包下载量。这条用例是那个陷阱的看门狗。
        ("codex-plugin-0.12.0.tar.gz", "other", "other"),
        # 后缀循环漏网的校验/溯源清单：.txt / .json 结尾，曾经全落进 other
        ("SHA256SUMS.txt", "checksum", "any"),
        ("artifact-manifest.json", "checksum", "any"),
        ("artifact-manifest-python.json", "checksum", "any"),
        ("some-unlabelled-artifact.bin", "other", "other"),
    ],
)
def test_asset_classification(name, role, platform):
    assert collector.classify_asset(name) == (role, platform)


def test_updater_payloads_are_never_counted_as_installers(events):
    """自动更新包与签名文件绝不能进安装量——那会让这个数随老用户升级膨胀。"""
    installers = [e for e in _gh(events) if e["properties"]["asset_role"] == "installer"]
    total = sum(e["properties"]["download_count_total"] for e in installers)
    assert total == 137 + 402 + 512 + 908
    # latest.json 被更新器每天拉一次，量最大且完全不是「装过的人」
    assert _by_name("latest.json", events)["properties"]["asset_role"] == "update_check"
    assert all(
        e["properties"]["asset_role"] != "installer"
        for e in _gh(events)
        if e["properties"]["asset_id"] in (5003, 5004, 5005, 5006, 5007)
    )


def test_plugin_manifest_polls_are_not_plugin_downloads(events):
    """`codex-plugin.json` 是插件宿主检查更新时拉的，不是有人装了插件。

    线上实测：合成一个角色时该角色 3387 次里 3382 次是 manifest，真实
    zip 只有 5 次——「插件装机量」被放大近 700 倍。样本按同样形状构造。
    """
    manifest = _by_name("codex-plugin.json", events)["properties"]
    package = _by_name("codex-plugin-0.8.0.zip", events)["properties"]
    assert manifest["asset_role"] == "plugin_manifest"
    assert package["asset_role"] == "plugin"
    # 真正的护栏：轮询量必须**不在** plugin 这个角色里
    plugin_total = sum(
        e["properties"]["download_count_total"]
        for e in _gh(events)
        if e["properties"]["asset_role"] == "plugin"
    )
    assert plugin_total == package["download_count_total"]
    assert manifest["download_count_total"] not in (plugin_total, 0)


def test_automated_traffic_never_lands_in_human_downloads(events):
    """轮询与更新载荷绝不能进 downloads 口径——这是看板分区的判据本身。"""
    summary = collector.summarize(events)
    by_role = summary["github_by_role"]
    human = summary["github_human_downloads_lifetime"]
    automated = summary["github_automated_requests_lifetime"]

    assert set(collector.HUMAN_DOWNLOAD_ROLES).isdisjoint(collector.AUTOMATED_ROLES)
    # latest.json 在样本里是最大的那个数：它一旦漏进 human，这条就红
    assert by_role["update_check"]["downloads_total"] > human
    assert human == sum(
        by_role.get(r, {}).get("downloads_total", 0)
        for r in ("installer", "plugin", "wheel", "sdist")
    )
    assert automated == sum(
        by_role.get(r, {}).get("downloads_total", 0)
        for r in ("update_check", "plugin_manifest", "updater")
    )


# ---------------------------------------------------------------------------
# 看板与采集器的同源对：划分只写在一边，两边就会各说各的
# ---------------------------------------------------------------------------
DASHBOARD = ROOT / "docs" / "analytics" / "yc-dashboard.json"


def _dashboard():
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def _roles_of(section):
    """看板某一区实际覆盖到的 asset_role 集合。"""
    d = _dashboard()
    by_id = {m["id"]: m for m in d["metrics"]}
    roles = set()
    for mid in d["sections"][section]["metrics"]:
        f = by_id[mid].get("filter") or {}
        if "asset_role" in f:
            roles.add(f["asset_role"])
    return roles


def test_dashboard_sections_cover_exactly_the_collector_partition():
    """采集器说哪些角色算「人下载的」，看板 Downloads 区就必须正好列这些。

    这两处划分曾经各写各的：`sdist` 进了 HUMAN_DOWNLOAD_ROLES，却没有对应的
    看板指标，于是「人主动下载」在汇总里和在看板上不是同一个数。
    """
    assert _roles_of("Distribution / Downloads") == set(collector.HUMAN_DOWNLOAD_ROLES)
    assert _roles_of("Infrastructure / Automated Traffic") >= set(collector.AUTOMATED_ROLES)


def test_every_classifier_role_is_reachable_from_some_dashboard_section():
    """分类器能产出的角色，必须都能在看板上找到归宿——除了 checksum。

    漏一个角色不会让任何查询报错，只会让那部分流量从看板上**静静消失**。
    """
    produced = {
        collector.classify_asset(n)[0]
        for n in (
            "Tavotto-0.8.0-macOS.dmg",
            "Tavotto.app.tar.gz",
            "latest.json",
            "tavotto-0.8.0-py3-none-any.whl",
            "tavotto-0.8.0.tar.gz",
            "codex-plugin.json",
            "codex-plugin-0.8.0.zip",
            "SHA256SUMS.txt",
            "some-unlabelled-artifact.bin",
        )
    }
    placed = _roles_of("Distribution / Downloads") | _roles_of("Infrastructure / Automated Traffic")
    # checksum 是附属文件，刻意不进任何一区
    assert produced - placed == {"checksum"}


def test_role_filtered_metrics_carry_the_role_resolution_rule():
    """按 asset_role 过滤再聚合，会把 2026-08-27 换过角色的资产从中间切断。

    实测后果：plugin_manifest 的 30 天值变成整个累计计数器（3387 而不是 5），
    plugin 则把换角色前的 manifest 增量算成插件包下载（382 而不是 0）。
    每个按角色过滤的指标都必须自带「先按 asset_id 解析角色」这条规则。
    """
    d = _dashboard()
    assert "role_resolution" in d
    role_filtered = [
        m for m in d["metrics"] if isinstance(m.get("filter"), dict) and "asset_role" in m["filter"]
    ]
    assert role_filtered, "看板里一个按角色过滤的指标都没有，用例失去意义"
    for m in role_filtered:
        assert m.get("role_from") == "latest_snapshot_per_asset_id", m["id"]
        assert "RESOLVE ROLE PER asset_id FIRST" in m["query_note"], m["id"]


def test_automated_roles_are_never_labelled_as_downloads():
    """轮询指标必须显式禁用 Downloads/Users/Installs 这几个标题。"""
    d = _dashboard()
    by_id = {m["id"]: m for m in d["metrics"]}
    for mid in ("update_checks_30d", "plugin_manifest_checks_30d"):
        banned = set(by_id[mid]["label_must_not_be"])
        assert {"Downloads", "Users", "Installs"} <= banned, mid
    assert "hard_rule" in d["sections"]["Infrastructure / Automated Traffic"]


def test_sdist_and_macos_updater_share_a_suffix_but_not_a_role(events):
    """`Tavotto.app.tar.gz` 与 `tavotto-0.8.0.tar.gz` 后缀完全一样。"""
    assert _by_name("Tavotto.app.tar.gz", events)["properties"]["asset_role"] == "updater"
    assert _by_name("tavotto-0.8.0.tar.gz", events)["properties"]["asset_role"] == "sdist"


def test_unknown_assets_fall_back_to_other_not_installer():
    """不认识的东西宁可少算，也绝不悄悄算进安装量。"""
    for name in ("weird", "Tavotto.pkg.xz", "notes.txt", ""):
        role, _ = collector.classify_asset(name)
        assert role != "installer"


# ---------------------------------------------------------------------------
# 快照语义
# ---------------------------------------------------------------------------
def test_download_counts_are_sent_as_cumulative_snapshots(events):
    ev = _by_name("Tavotto-0.8.0-macOS.dmg", events)
    props = ev["properties"]
    assert props["download_count_total"] == 137  # 累计值，原样上报
    assert props["observed_date"] == "2026-08-20"
    assert "downloads_today" not in props and "delta" not in props


def test_asset_id_is_the_identity_not_the_filename(events):
    for ev in _gh(events):
        assert ev["properties"]["asset_id"] > 0
        # 文件名根本没进事件：资产会被删掉重传，名字会重复，id 不会
        blob = json.dumps(ev, ensure_ascii=False)
        assert ".dmg" not in blob and ".exe" not in blob and ".whl" not in blob


def test_snapshot_keys_are_deterministic_per_asset_and_day():
    first = collector.collect(
        "2026-08-20",
        github_token=None,
        github_json=str(GITHUB_FIXTURE),
        pypi_json=str(PYPI_FIXTURE),
    )
    again = collector.collect(
        "2026-08-20",
        github_token=None,
        github_json=str(GITHUB_FIXTURE),
        pypi_json=str(PYPI_FIXTURE),
    )
    keys = [e["properties"]["snapshot_key"] for e in first]
    assert keys == [e["properties"]["snapshot_key"] for e in again]
    assert len(set(keys)) == len(keys), "同一次运行内 snapshot_key 必须唯一"
    # 换一天就是另一批快照
    other = collector.collect(
        "2026-08-21",
        github_token=None,
        github_json=str(GITHUB_FIXTURE),
        pypi_json=str(PYPI_FIXTURE),
    )
    assert not (
        set(keys)
        & {e["properties"]["snapshot_key"] for e in other if e["event"] != "pypi_daily_downloads"}
    )


def test_repo_snapshot_is_collected(events):
    (repo,) = [e for e in events if e["event"] == "github_repo_snapshot"]
    assert repo["properties"]["stars"] == 421
    assert repo["properties"]["forks"] == 23
    assert repo["properties"]["observed_date"] == "2026-08-20"


# ---------------------------------------------------------------------------
# PyPI
# ---------------------------------------------------------------------------
def test_only_without_mirrors_is_used(events):
    pypi = [e for e in events if e["event"] == "pypi_daily_downloads"]
    assert pypi and all(e["properties"]["category"] == "without_mirrors" for e in pypi)
    # with_mirrors 的数字（940/1012/998）一个都不能出现
    downloads = {e["properties"]["downloads"] for e in pypi}
    assert downloads == {121, 143, 156}


def test_healing_window_bounds_and_deduplicates():
    payload = json.loads(PYPI_FIXTURE.read_text(encoding="utf-8"))
    rows = collector.pypi_snapshots(payload, "2026-08-20", window_days=14)
    dates = [e["properties"]["date"] for e in rows]
    assert dates == ["2026-08-17", "2026-08-18", "2026-08-19"]
    assert "2026-07-01" not in dates, "窗口之外的不该重复上报"
    assert "2026-09-30" not in dates, "未来日期是脏数据，不收"
    # 窄窗口只留最近的那几天
    narrow = collector.pypi_snapshots(payload, "2026-08-20", window_days=2)
    assert [e["properties"]["date"] for e in narrow] == ["2026-08-18", "2026-08-19"]


def test_reruns_produce_identical_pypi_snapshot_keys():
    """采集器重跑（自愈窗口天天覆盖同样的日期）不能让下载量翻倍。"""
    payload = json.loads(PYPI_FIXTURE.read_text(encoding="utf-8"))
    a = collector.pypi_snapshots(payload, "2026-08-20")
    b = collector.pypi_snapshots(payload, "2026-08-21")
    keys_a = {e["properties"]["snapshot_key"] for e in a}
    keys_b = {e["properties"]["snapshot_key"] for e in b}
    assert keys_a <= keys_b or keys_b <= keys_a or (keys_a & keys_b)
    for e in a:
        assert e["properties"]["snapshot_key"] == f"pypi:tavotto:{e['properties']['date']}"


# ---------------------------------------------------------------------------
# 数据源还不存在 ≠ 采集失败
#
# 2026-08-20 首次真跑就撞上：`tavotto` 还没发到 PyPI，PyPIStats 回 404，
# 整个 workflow 红——**连 GitHub 那半边的发行量也一起丢**，而且会每晚红一次
# 直到有人去看。「大声失败」这条纪律对真故障成立，对「这个数据源还没开始
# 存在」不成立。
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status,expect",
    [
        (404, "统计数据"),  # 最容易被误读成「包还没发布」的那个
        (429, "限流"),
        (500, "HTTP 500"),
        (None, "取不到"),
    ],
)
def test_pypi_failures_are_skipped_not_fatal(monkeypatch, capsys, status, expect):
    """取不到 PyPI → 跳过、GitHub 照常，且**说清楚为什么**。

    整段是「尽力而为」，因为有 14 天自愈窗口：漏掉的日期下次会补回来。
    为一次限流就让 workflow 红、顺带丢掉 GitHub 那半边，代价不成比例。
    """

    def failing(url, token=None):
        if "pypistats" in url:
            raise collector.CollectError("上游拒绝", status=status)
        raise AssertionError("不该走到 GitHub")

    monkeypatch.setattr(collector, "_get_json", failing)
    assert collector.fetch_pypi() == {}
    err = capsys.readouterr().err
    assert expect in err, f"notice 没说清原因：{err!r}"
    assert "自愈窗口" in err, "要让人知道漏掉的日期会自己补回来"


def test_404_is_not_described_as_unpublished(monkeypatch, capsys):
    """404 **不等于**「包还没发布」。

    PyPIStats 从下载日志按天跑批，包已经在 PyPI 上了也照样 404。
    把它写成「还没发布」的代价是：看到 notice 的人跑去查发布链路，
    而发布链路好着呢——这是 2026-08-20 真实发生过的一次误导。
    """

    def not_found(url, token=None):
        raise collector.CollectError("HTTP 404", status=404)

    monkeypatch.setattr(collector, "_get_json", not_found)
    collector.fetch_pypi()
    err = capsys.readouterr().err
    assert "还没有" in err and "统计数据" in err
    for wrong in ("还没发布", "发布之前", "PyPI 上还没有 tavotto（"):
        assert wrong not in err, f"这句会让人以为包没发出去：{err!r}"


def test_missing_pypi_package_still_collects_github(capsys):
    """整条 collect() 也要活下来，GitHub 的快照一条不少。"""
    import json as _json

    fixture = _json.loads(GITHUB_FIXTURE.read_text(encoding="utf-8"))
    expected = sum(len(r["assets"]) for r in fixture["releases"]) + 1  # +1 = repo 快照

    def only_pypi_404(url, token=None):
        if "pypistats" in url:
            raise collector.CollectError("GET … 失败: HTTP 404", status=404)
        raise AssertionError("本用例用 fixture 喂 GitHub")

    import unittest.mock as mock

    with mock.patch.object(collector, "_get_json", only_pypi_404):
        events = collector.collect("2026-08-20", github_token=None, github_json=str(GITHUB_FIXTURE))
    assert len(events) == expected
    assert not [e for e in events if e["event"] == "pypi_daily_downloads"]
    assert collector.summarize(events)["pypi_note"]


def test_github_failures_are_still_loud(monkeypatch):
    """**GitHub 那段没有这个待遇。**

    它是快照式的、没有自愈窗口，漏一天就是看板上一个真实的、再也补不回来的
    缺口。所以那边任何失败都必须让 workflow 红。
    """

    def boom(url, token=None):
        raise collector.CollectError("GitHub 挂了", status=503)

    monkeypatch.setattr(collector, "_get_json", boom)
    with pytest.raises(collector.CollectError):
        collector.fetch_github(None)


# ---------------------------------------------------------------------------
# 与代理契约的对拍 + CLI
# ---------------------------------------------------------------------------
def test_every_collected_event_passes_the_proxy_schema(events, monkeypatch):
    """采集器发的每一条都必须被代理原样收下——否则看板会安静地缺一整类。"""
    import sys

    sys.path.insert(0, str(ROOT / "services" / "telemetry_proxy"))
    from tavotto_telemetry_proxy import core, posthog

    monkeypatch.setenv("TAVOTTO_METRICS_TOKEN", "t0ken")
    monkeypatch.setenv("POSTHOG_PROJECT_KEY", "phc_test")
    sent: list[list[dict]] = []
    monkeypatch.setattr(posthog, "send", sent.append)
    status, body = core.handle(
        "POST",
        "/v1/metrics",
        {"content-type": "application/json", "authorization": "Bearer t0ken"},
        json.dumps({"schema_version": 1, "events": events}).encode("utf-8"),
    )
    assert status == 200, body
    assert body["accepted"] == len(events)


def test_dry_run_transmits_nothing(capsys, monkeypatch):
    def explode(*_a, **_kw):
        raise AssertionError("--dry-run 不许上报")

    monkeypatch.setattr(collector, "transmit", explode)
    rc = collector.main(
        [
            "--dry-run",
            "--date",
            "2026-08-20",
            "--github-json",
            str(GITHUB_FIXTURE),
            "--pypi-json",
            str(PYPI_FIXTURE),
        ]
    )
    assert rc == 0
    out = capsys.readouterr()
    assert "downloads != users" in out.out


def test_missing_token_fails_loudly_instead_of_silently_skipping(monkeypatch, capsys):
    """采集器和桌面遥测相反：丢数据必须有人看见。"""
    monkeypatch.delenv("TAVOTTO_METRICS_TOKEN", raising=False)
    rc = collector.main(
        [
            "--date",
            "2026-08-20",
            "--github-json",
            str(GITHUB_FIXTURE),
            "--pypi-json",
            str(PYPI_FIXTURE),
        ]
    )
    assert rc == 2
    assert "TAVOTTO_METRICS_TOKEN" in capsys.readouterr().err


def test_upstream_failure_is_a_nonzero_exit(monkeypatch, capsys):
    monkeypatch.setenv("TAVOTTO_METRICS_TOKEN", "t0ken-abcdef")
    monkeypatch.setattr(
        collector,
        "transmit",
        lambda *_a: (_ for _ in ()).throw(collector.CollectError("上报失败: HTTP 502")),
    )
    rc = collector.main(
        [
            "--date",
            "2026-08-20",
            "--github-json",
            str(GITHUB_FIXTURE),
            "--pypi-json",
            str(PYPI_FIXTURE),
        ]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "上报失败" in err
    assert "t0ken-abcdef" not in err, "错误输出里绝不能带 token"


def test_summary_keeps_installers_and_updaters_apart(events):
    summary = collector.summarize(events)
    assert summary["github_installer_downloads_lifetime"] == 1959
    assert summary["github_by_role"]["updater"]["downloads_total"] > 1959
    assert "users" not in json.dumps(summary["github_by_role"])
