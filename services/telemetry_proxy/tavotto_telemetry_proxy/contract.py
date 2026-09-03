"""事件契约（代理这一份）。**纯数据，纯标准库，不 import 任何别的东西。**

这是 `src/tavotto/engine/telemetry.py` 里那张表的镜像。两份显式的表 + 一条
对拍用例（`tests/test_telemetry_proxy.py::test_client_and_proxy_contracts_match`），
刻意不做「共享 schema 编译器」：为了消除十几行重复引入一套机制，读起来更难、
出错的方式也更多。而**两边悄悄漂开**才是真正的风险——客户端发了新事件、
代理不认识，症状是「新版本发出去之后那个指标一直是 0」，还全绿。

代理这一侧多两样客户端没有的东西：
  * `METRICS_EVENTS` —— 只有拿着 bearer token 的定时采集器能发（发行量快照），
    它们**不属于任何一个匿名用户**，distinct_id 是常量 `distribution_metrics`；
  * 字符串类属性的**长度与字符集**约束 —— 客户端是自己人，代理面对的是公网。
"""

from __future__ import annotations

SCHEMA_VERSION = 1

#: 发行量快照的固定 distinct_id。**绝不能和产品事件混进同一批 distinct_id**：
#: 混了的话「本周有多少人成功导出过」里会多出一个从不导出的幽灵用户，
#: 留存曲线也会被一条每天准时出现的机器人拉直。
METRICS_DISTINCT_ID = "distribution_metrics"


def enum(*values: str) -> dict:
    return {"kind": "enum", "values": tuple(values)}


def integer(maximum: int) -> dict:
    return {"kind": "int", "max": maximum}


BOOL = {"kind": "bool"}
VERSION = {"kind": "version"}  # 短版本串：[0-9A-Za-z.+-_]{1,32}
DATE = {"kind": "date"}  # YYYY-MM-DD
KEY = {"kind": "key"}  # 快照身份：[A-Za-z0-9:._-]{1,120}

AUTO_PROPS: dict[str, dict] = {
    "app_version": VERSION,
    "platform": enum("macos", "windows", "linux", "other"),
    "arch": enum("arm64", "x86_64", "other"),
    "distribution": enum("desktop", "pipx", "pip", "source", "unknown"),
}

EVENTS: dict[str, dict[str, dict]] = {
    "telemetry_enabled": {"source": enum("first_run", "settings")},
    "app_started": {"app_mode": enum("desktop", "browser")},
    "figure_opened": {"asset_kind": enum("pdf", "raster"), "editable": BOOL},
    "figure_edit_completed": {
        "edit_kind": enum("text", "series", "axes", "annotation", "layout", "style", "other"),
        "patch_count": integer(1000),
    },
    "canvas_created": {"creation_kind": enum("blank", "project", "duplicate")},
    "preflight_completed": {
        "errors": integer(1000),
        "warnings": integer(1000),
        "not_verifiable": integer(1000),
        "suggestions": integer(1000),
        "passed": BOOL,
    },
    "export_completed": {
        "pdf": BOOL,
        "png": BOOL,
        "with_proof": BOOL,
        "panel_count": integer(1000),
    },
    "ai_assistant_invoked": {"agent": enum("codex", "claude", "other")},
    "update_completed": {"update_kind": enum("desktop", "pip", "pipx"), "target_version": VERSION},
    # ---- 客户端 CONSENT_VERSION 2（Session 22）新增的九条；与客户端表逐字对拍 ----
    "project_refresh_completed": {
        "source": enum("watcher", "manual", "codex", "ai"),
        "changed_bucket": enum("none", "one", "few", "many"),
    },
    "project_readiness_opened": {
        "source": enum("banner", "panel", "quickedit", "palette"),
        "status_bucket": enum("all_editable", "mixed", "layout_only"),
    },
    "tutorial_started": {
        "source": enum("picker", "help", "settings", "palette"),
        "tutorial_version": integer(1000),
    },
    "tutorial_step_completed": {
        "step_id": enum(
            "welcome",
            "open_fast_edit",
            "select_text",
            "change_typography",
            "locate_problem",
            "export_original",
            "add_to_layout",
            "multi_select_align",
            "export_canvas",
            "done",
        ),
        "tutorial_version": integer(1000),
    },
    "tutorial_completed": {"tutorial_version": integer(1000)},
    "context_bar_multi_used": {
        "action_id": enum(
            "align_left",
            "align_center",
            "align_right",
            "align_top",
            "align_middle",
            "align_bottom",
            "distribute_h",
            "distribute_v",
            "same_width",
            "same_height",
            "group",
            "ungroup",
            "more",
        ),
        "selection_size_bucket": enum("2", "3_5", "6_plus"),
    },
    "document_saved": {
        "trigger": enum("manual", "autosave"),
        "outcome": enum("ok", "conflict", "failed"),
    },
    "recovery_action": {"action": enum("restore", "keep_main")},
    "package_action": {
        "action": enum("install", "update", "remove"),
        "outcome": enum("ok", "failed", "cancelled"),
    },
}

#: 发行量指标。**它们不是用户**——GitHub 的 download_count 是累计计数器，
#: PyPI 的日下载量里混着 CI 与自动化。指标字典里对应的措辞是
#: 「distribution downloads」，绝不是「users」。
METRICS_EVENTS: dict[str, dict[str, dict]] = {
    "github_release_asset_snapshot": {
        "release_id": integer(10**12),
        "release_tag": VERSION,
        "asset_id": integer(10**12),
        # 自动更新包与签名文件**不是人下载的安装包**。分不开这两类，
        # 「有多少人装过」就会被更新流量整个淹掉。
        # `update_check`（latest.json）与 `plugin_manifest`（codex-plugin.json）
        # 是**轮询次数**：装了不升级的机器也天天贡献。它们绝不能进任何
        # Downloads / Users / Installs 口径。
        "asset_role": enum(
            "installer",
            "updater",
            "update_check",
            "wheel",
            "sdist",
            "plugin",
            "plugin_manifest",
            "checksum",
            "other",
        ),
        "platform": enum("macos", "windows", "linux", "any", "other"),
        "download_count_total": integer(10**9),
        "observed_date": DATE,
        "snapshot_key": KEY,
    },
    "pypi_daily_downloads": {
        "date": DATE,
        "downloads": integer(10**9),
        "category": enum("without_mirrors"),
        "snapshot_key": KEY,
    },
    "github_repo_snapshot": {
        "stars": integer(10**8),
        "forks": integer(10**8),
        "observed_date": DATE,
        "snapshot_key": KEY,
    },
}
