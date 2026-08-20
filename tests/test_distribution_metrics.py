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
    pytest.skip("没有 scripts/（wheel/sdist 里不含构建与采集脚本）",
                allow_module_level=True)


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
    return collector.collect("2026-08-20", github_token=None,
                             github_json=str(GITHUB_FIXTURE),
                             pypi_json=str(PYPI_FIXTURE))


def _gh(events):
    return [e for e in events if e["event"] == "github_release_asset_snapshot"]


def _by_name(name: str, events):
    data = json.loads(GITHUB_FIXTURE.read_text(encoding="utf-8"))
    for release in data["releases"]:
        for asset in release["assets"]:
            if asset["name"] == name:
                return next(e for e in _gh(events)
                            if e["properties"]["asset_id"] == asset["id"])
    raise AssertionError(f"fixture 里没有 {name}")


# ---------------------------------------------------------------------------
# 资产分类：这一条决定「有多少人装过」是不是真话
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,role,platform", [
    ("Tavotto-0.8.0-macOS.dmg", "installer", "macos"),
    ("Tavotto-0.8.0-Windows-Setup.exe", "installer", "windows"),
    ("Tavotto.app.tar.gz", "updater", "macos"),
    ("Tavotto.app.tar.gz.sig", "checksum", "macos"),
    ("Tavotto_0.8.0_x64-setup.nsis.zip", "updater", "windows"),
    ("Tavotto_0.8.0_x64-setup.nsis.zip.sig", "checksum", "windows"),
    ("latest.json", "updater", "any"),
    ("tavotto-0.8.0-py3-none-any.whl", "wheel", "any"),
    ("tavotto-0.8.0.tar.gz", "sdist", "any"),
    ("codex-plugin.json", "plugin", "any"),
    ("codex-plugin-0.8.0.zip", "plugin", "any"),
    ("some-unlabelled-artifact.bin", "other", "other"),
])
def test_asset_classification(name, role, platform):
    assert collector.classify_asset(name) == (role, platform)


def test_updater_payloads_are_never_counted_as_installers(events):
    """自动更新包与签名文件绝不能进安装量——那会让这个数随老用户升级膨胀。"""
    installers = [e for e in _gh(events)
                  if e["properties"]["asset_role"] == "installer"]
    total = sum(e["properties"]["download_count_total"] for e in installers)
    assert total == 137 + 402 + 512 + 908
    # latest.json 被更新器每天拉一次，量最大且完全不是「装过的人」
    assert _by_name("latest.json", events)["properties"]["asset_role"] == "updater"
    assert all(e["properties"]["asset_role"] != "installer"
               for e in _gh(events)
               if e["properties"]["asset_id"] in (5003, 5004, 5005, 5006, 5007))


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
    assert props["download_count_total"] == 137        # 累计值，原样上报
    assert props["observed_date"] == "2026-08-20"
    assert "downloads_today" not in props and "delta" not in props


def test_asset_id_is_the_identity_not_the_filename(events):
    for ev in _gh(events):
        assert ev["properties"]["asset_id"] > 0
        # 文件名根本没进事件：资产会被删掉重传，名字会重复，id 不会
        blob = json.dumps(ev, ensure_ascii=False)
        assert ".dmg" not in blob and ".exe" not in blob and ".whl" not in blob


def test_snapshot_keys_are_deterministic_per_asset_and_day():
    first = collector.collect("2026-08-20", github_token=None,
                              github_json=str(GITHUB_FIXTURE),
                              pypi_json=str(PYPI_FIXTURE))
    again = collector.collect("2026-08-20", github_token=None,
                              github_json=str(GITHUB_FIXTURE),
                              pypi_json=str(PYPI_FIXTURE))
    keys = [e["properties"]["snapshot_key"] for e in first]
    assert keys == [e["properties"]["snapshot_key"] for e in again]
    assert len(set(keys)) == len(keys), "同一次运行内 snapshot_key 必须唯一"
    # 换一天就是另一批快照
    other = collector.collect("2026-08-21", github_token=None,
                              github_json=str(GITHUB_FIXTURE),
                              pypi_json=str(PYPI_FIXTURE))
    assert not (set(keys) & {e["properties"]["snapshot_key"]
                             for e in other
                             if e["event"] != "pypi_daily_downloads"})


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
    assert pypi and all(e["properties"]["category"] == "without_mirrors"
                        for e in pypi)
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
        "POST", "/v1/metrics",
        {"content-type": "application/json", "authorization": "Bearer t0ken"},
        json.dumps({"schema_version": 1, "events": events}).encode("utf-8"))
    assert status == 200, body
    assert body["accepted"] == len(events)


def test_dry_run_transmits_nothing(capsys, monkeypatch):
    def explode(*_a, **_kw):
        raise AssertionError("--dry-run 不许上报")
    monkeypatch.setattr(collector, "transmit", explode)
    rc = collector.main(["--dry-run", "--date", "2026-08-20",
                         "--github-json", str(GITHUB_FIXTURE),
                         "--pypi-json", str(PYPI_FIXTURE)])
    assert rc == 0
    out = capsys.readouterr()
    assert "downloads != users" in out.out


def test_missing_token_fails_loudly_instead_of_silently_skipping(monkeypatch, capsys):
    """采集器和桌面遥测相反：丢数据必须有人看见。"""
    monkeypatch.delenv("TAVOTTO_METRICS_TOKEN", raising=False)
    rc = collector.main(["--date", "2026-08-20",
                         "--github-json", str(GITHUB_FIXTURE),
                         "--pypi-json", str(PYPI_FIXTURE)])
    assert rc == 2
    assert "TAVOTTO_METRICS_TOKEN" in capsys.readouterr().err


def test_upstream_failure_is_a_nonzero_exit(monkeypatch, capsys):
    monkeypatch.setenv("TAVOTTO_METRICS_TOKEN", "t0ken-abcdef")
    monkeypatch.setattr(collector, "transmit",
                        lambda *_a: (_ for _ in ()).throw(
                            collector.CollectError("上报失败: HTTP 502")))
    rc = collector.main(["--date", "2026-08-20",
                         "--github-json", str(GITHUB_FIXTURE),
                         "--pypi-json", str(PYPI_FIXTURE)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "上报失败" in err
    assert "t0ken-abcdef" not in err, "错误输出里绝不能带 token"


def test_summary_keeps_installers_and_updaters_apart(events):
    summary = collector.summarize(events)
    assert summary["github_installer_downloads_lifetime"] == 1959
    assert summary["github_by_role"]["updater"]["downloads_total"] > 1959
    assert "users" not in json.dumps(summary["github_by_role"])
