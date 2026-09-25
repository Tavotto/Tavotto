"""生成 docs/qa/2026-09-24/sci/ledger.json：用例台账的唯一出处（hash 在生成那一刻现算）。

用法（worktree 根目录）：/Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/sci/repro/build_ledger.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
SEC = ROOT / "docs/qa/2026-09-24/sci"
SOURCE_SHA = "62af729c2f84652bf3979c25b0c3117fcfa756a5"
R = "docs/qa/2026-09-24/sci/repro"

ENV = {
    "os": "macOS 27.0 (26A428)",
    "arch": "arm64",
    "parent_python": "3.13.11 (/Volumes/Projects/Tavotto/.venv, PYTHONPATH=<worktree>/src)",
    "parent_libs": "Flask 3.1.3 / pikepdf 10.13.0.post1 / pypdfium2 5.13.0 / uharfbuzz 0.56.1 / "
    "Pillow 12.3.0；测试侧独立读取器 PyMuPDF 1.28.2（只在测试进程，不进应用闭包）",
    "worker_python": "3.13.11 Homebrew (/opt/homebrew/opt/python@3.13/libexec/bin/python3)",
    "matplotlib": "3.10.8（worker）",
    "numpy": "2.4.3（worker）",
    "pandas": "3.0.1（worker）",
    "node": "v26.7.0 / pnpm 11.0.7 / vitest 4.1.11",
    "browser": "未使用（本节无 Playwright 用例）",
    "workerd": "worktree 内 cargo build --offline 的 debug 产物（仅 SCI-04 workerd 腿）",
    "fonts": "批准字体从主工作区 src/tavotto/resources/fonts 拷入（fetch_fonts.py 校验 sha256 通过；gitignored）",
}


def sha(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()


def fx(*rels: str) -> list[dict]:
    return [{"path": r, "sha256": sha(r)} for r in rels]


def log(case: str, summary: str) -> dict:
    rel = f"docs/qa/2026-09-24/sci/logs/{case}.log"
    return {"log": rel, "sha256": sha(rel), "summary": summary}


PY = "bash docs/qa/2026-09-24/sci/repro/pytest.sh"
RUNPY = "bash docs/qa/2026-09-24/sci/repro/runpy.sh"
MUT = "/Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/sci/repro/mutate.py"

CASES = [
    {
        "case_id": "SCI-01",
        "title": "零编辑原图：原生 Matplotlib vs Tavotto 零 override 导入、重绘、导出",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "scripts/ci/compat_matrix.py::stage_fidelity（原生 savefig PNG vs worker.render_png，"
            "逐像素 changed_pixel_ratio≤0.004 / mean_abs_diff≤1.2——两侧都是 Matplotlib Agg，量的是"
            "「Tavotto 有没有改 Figure」，不碰 RenderCore 最终导出物）",
            "scripts/ci/compat_matrix.py 九级漏斗 export 阶段（只判 PDF/PNG 解得开、体积合理、无 warning，"
            "不与原生比）",
            "tests/test_compat_manifest.py::test_fallback_only_cases_are_not_claimed_as_full_support",
            "tests/test_compat_runner.py::test_fidelity_tolerance_is_a_reviewable_constant",
        ],
        "expected_outcome": "smoke 24 case 九级全过且分类闭集；最终导出物（worker 零 override 重序列化 PDF、"
        "RenderCore 画布 1:1 合成 PDF）与原生 PDF 在页面盒、文字层、字体、150dpi 栅格上一致，差异只允许来自"
        "明示支持范围（如 fonttype 42）。",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "CompatBench smoke（PR 门，强制 fidelity）24/24 九级 100%、full_support 24、product_bug 0。"
        "补充探针：RenderCore 画布腿 5/5 与原生逐像素 0 差、文字层与字体集合相同；worker 重序列化腿 3/5 严格一致，"
        "另 2 条的偏差经对照实验归因于 Tavotto 明示的 pdf.fonttype=42（engine/figsession.py）：sci_mathtext 与"
        "『原生 + fonttype42』0 像素差、文字层相同（Tavotto 反而保住了 α/β/ν/√/−1，原生 Type 3 丢了），"
        "art_colorbar 对照后 0.39% 变化像素（阈值 0.4% 内，贴边，未深究成因）。探针自身按严格判据退出 1，"
        "判定为『明示支持范围内的差异』而非缺陷。意外 skip：无；browser_playground 路由 not_run=3（未带 --browser）。"
        "基线在 matplotlib 3.11.1 采，本机 worker 为 3.10.8（runner 已提示）。",
        "source_sha": SOURCE_SHA,
        "environment": ENV,
        "fixtures": fx(
            "tests/compat/manifest.json",
            "tests/compat/cases/core_artists/ca_basic_series.py",
            "tests/compat/cases/core_artists/ca_legend_colorbar.py",
            "tests/compat/cases/axes_layout/al_grids.py",
            "tests/compat/cases/scientific_stack/sci_typography.py",
            f"{R}/sci01_final_export_fidelity.py",
            f"{R}/sci01_fonttype42_control.py",
        ),
        "entry": "CompatBench 驱动（真 worker + Flask 端点 + CLI）；HTTP（Flask test client /api/export）",
        "commands": [
            "bash docs/qa/2026-09-24/sci/repro/sci01_compat_smoke.sh",
            f"SCI01_KEEP=$SCI_SCRATCH/sci01_keep {RUNPY} {R}/sci01_final_export_fidelity.py",
            f"{RUNPY} {R}/sci01_fonttype42_control.py $SCI_SCRATCH/sci01_keep",
        ],
        "exit_codes": {
            "compat_smoke": 0,
            "final_export_fidelity_probe": 1,
            "fonttype42_control": 0,
        },
        "evidence": log(
            "SCI-01",
            "compat smoke: 24 case，九级各 24/24，full_support 24，Product bugs 0；探针: B 腿 5/5 pass、"
            "A 腿 3/5 严格 pass + 2 条归因 fonttype42（对照 exit 0）",
        ),
        "artifacts": [
            {"path": "$SCI_SCRATCH/compat/compat-report.json", "note": "scratch，未入库"},
            {
                "path": "$SCI_SCRATCH/sci01_keep/*.pdf",
                "note": "原生 / Tavotto 重序列化 PDF，scratch",
            },
        ],
        "first_failed_invariant": None,
        "mutation_check": None,
        "mutation_check_reason": "本条未新增断言（CompatBench 为既有门禁，探针是一次性核对），未做变异；"
        "RenderCore 落位几何的反证见 SCI-06。",
        "new_tests": [],
        "product_bugs": [],
        "gaps": [
            "只跑 --smoke（24 case），未跑 --all 全量语料；未带 --browser（playground 路由 not_run）",
            "最终导出物对原生的比对只覆盖 5 张图、只比 PDF（PNG/TIFF 最终物未与原生比）",
            "CompatBench fidelity 两侧同为 Matplotlib Agg：它证明 Figure 未被改，不证明 RenderCore 产物保真——"
            "后者只有本探针的 5 张图作证",
            "art_colorbar A 腿残差 0.39% 贴近阈值，成因（推测为 600dpi 导出下色条位图重采样）未验证",
        ],
    },
    {
        "case_id": "SCI-02",
        "title": "只改外观：颜色/字号/线宽/图例位置/图幅之后科学输入、结果、单位与源脚本不变",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_invariants_engine.py::test_exact_restore_is_pixel_and_manifest_identical（改→撤销像素与 manifest"
            " 逐位还原；不量「改着的时候数据点在不在原处」）",
            "tests/test_invariants_engine.py::test_capability_truthfulness（宣称可编辑的 prop 真能改变画面）",
            "tests/test_override_sequences.py（热态 == 重放序列；不量数据数组）",
        ],
        "expected_outcome": "外观 patch 后，产物里数据曲线顶点（按坐标区归一化）与零 override 逐点一致（≤1e-3）；"
        "带单位轴标签保留；源脚本与数据文件 sha256 在渲染、导出、写回之后不变；写回后的原件曲线仍一致。",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "新增 tests/test_appearance_edits_keep_science.py 2 passed：6 个点在图幅 81×61→120×70 mm、"
        "改色/线宽/标题字号/图例位置之后归一化位置最大差 <1e-3；单位标签在；脚本与数据 sha256 写回前后相同；"
        "尺子活性对照（改 ylim）量得出 >0.05 的变化。既有 invariants+sequences 107 passed、1 skipped——"
        "skip 是 test_override_sequences.py:537 的 KNOWN 空参数集（没有登记在案的已知分歧），属预期。"
        "排查中发现 3.2×2.4in 默认边距下 xlabel 被裁出页面——原生 matplotlib 同样如此（已对照），是夹具问题不是缺陷。",
        "source_sha": SOURCE_SHA,
        "environment": ENV,
        "fixtures": fx(
            "tests/test_appearance_edits_keep_science.py", f"{R}/sci02_textlayer_probe.py"
        ),
        "entry": "pytest（Flask test client /api/engine/render、/api/export scope=original、/api/engine/update_source；真 worker）",
        "commands": [
            f"{PY} tests/test_appearance_edits_keep_science.py",
            f"{PY} tests/test_invariants_engine.py tests/test_override_sequences.py",
            f"{PY} {R}/sci02_textlayer_probe.py -s",
            f"{MUT} SCI-02 linewidth-leaks-into-data src/tavotto/engine/overrides.py "
            '\'    ("line", "linewidth"): (lambda a: a.get_linewidth(), lambda a, v: a.set_linewidth(float(v))),\' '
            "'<同上，setter 追加 a.set_ydata([y * 1.1 for y in a.get_ydata()])>' -- tests/test_appearance_edits_keep_science.py",
        ],
        "exit_codes": {
            "new_test": 0,
            "invariants_and_sequences": 0,
            "textlayer_probe": 0,
            "mutation": 1,
            "restored": 0,
        },
        "evidence": log(
            "SCI-02",
            "新测 2 passed；invariants+sequences 107 passed 1 skipped（KNOWN 空集）；变异 1 failed → 还原 2 passed",
        ),
        "artifacts": [
            {"path": "$SCI_SCRATCH/sci02_textlayer/*.png", "note": "逐 patch 栅格，scratch"}
        ],
        "first_failed_invariant": None,
        "mutation_check": {
            "target_file": "src/tavotto/engine/overrides.py",
            "mutation": "line.linewidth 的 setter 顺带把 ydata ×1.1（外观 patch 泄漏进数据）",
            "expected_red_case": "tests/test_appearance_edits_keep_science.py::test_appearance_only_patches_keep_points_ticks_units_and_sources",
            "observed": "red",
            "restored_green": True,
            "first_failed_assertion": "第 1 点：(0.2273, 0.8636) → (0.2273, 0.9455)",
        },
        "new_tests": [
            "tests/test_appearance_edits_keep_science.py::test_appearance_only_patches_keep_points_ticks_units_and_sources",
            "tests/test_appearance_edits_keep_science.py::test_the_ruler_sees_a_real_data_change",
        ],
        "product_bugs": [],
        "gaps": [
            "只覆盖一条 Line2D；散点 / 柱 / 误差棒 / 图像数组未量",
            "G2 双轴 / 父子跟随 / 锁定对象场景未构造",
            "刻度个数随图幅由 locator 重选（数值集合会变），未作为判据；数据范围由归一化顶点间接量",
            "「计算结果」只以脚本读入并绘出的数组为真值，没有独立的数值计算产物（如均值写文件）",
        ],
    },
    {
        "case_id": "SCI-03",
        "title": "脚本结构改变后旧 override 的匹配",
        "coverage_status": "待实现",
        "existing_tests": [
            "src/tavotto/engine/overrides.py 在 gid 不存在时报「元素不存在（脚本可能已改动）」→ 写回 409 write_back_warnings"
            "（tests/test_write_back.py::test_write_back_blocked_by_worker_warnings 用假 worker 钉住；本次 "
            "tests/test_write_back_real_409.py 真链路补钉）——只覆盖「gid 消失」，不覆盖「gid 还在但指向另一条曲线」",
            "tests/test_legend_binding.py（图例项 j 是原始序号——仅图例项，不是曲线身份）",
        ],
        "expected_outcome": "曲线 A 的 override 在重排 / 前插 / 删除 A / 前插子图之后，只落在 label=alpha 的曲线上，"
        "或明确报告无法匹配；绝不静默套到另一条曲线。",
        "product_outcome": "功能失败",
        "test_verdict": "fail",
        "verdict_reason": "4 个变体全部违反：一次性全新 worker（写回校验与重开走的同一条路）上，洋红 + 线宽 4.5 分别落在"
        " beta（重排）、gamma（前插）、beta（删除 A）、delta（前插子图，另一个 axes 上的曲线），warnings 全为空。"
        "HTTP 热路径 3/4 同样错套；重排那一例热路径仍显示 alpha（等待 3s 后热会话尚未换成新脚本，时序相关，"
        "以全新 worker 结果为准）。根因：gid 是位置式（manifest.py:685 `axes_{i}.lines_{j}`），override 只按 gid 匹配，"
        "没有任何身份核对。",
        "source_sha": SOURCE_SHA,
        "environment": ENV,
        "fixtures": fx(f"{R}/sci03_structure_change.py"),
        "entry": "HTTP（Flask test client /api/engine/render）+ pool.one_shot 全新 worker",
        "commands": [f"{RUNPY} {R}/sci03_structure_change.py"],
        "exit_codes": {"probe": 1},
        "evidence": log(
            "SCI-03",
            "4/4 变体 fresh worker 错套（magenta_on ≠ [alpha] 且 warnings=[]），VERDICT: FAIL",
        ),
        "artifacts": [],
        "first_failed_invariant": "旧 override 只匹配原逻辑对象（reorder 变体：axes_0.lines_0 的 override 落到 label=beta）",
        "mutation_check": None,
        "mutation_check_reason": "探针在未变异的产品上即为红（暴露缺陷），无需也无法做「应红」反证；探针的尺子"
        "按 label 找曲线、不信 gid，基线步骤里先断言热态 alpha 已变洋红（前提成立）。",
        "new_tests": [],
        "product_bugs": [
            {
                "summary": "脚本结构改变（重排/插入/删除曲线或子图）后，位置式 gid 让旧 override 静默套到另一条曲线/另一个子图，无任何 warning；"
                "写回校验（一次性 worker 同样按 gid）也不会拦（推断，未单独实测写回）",
                "repro": f"{RUNPY} {R}/sci03_structure_change.py",
                "severity": "高（科研保真 / 数据损坏级：用户的强调色、线宽落到别的数据系列上且无提示）",
            }
        ],
        "gaps": [
            "未实测写回事务在该场景下是否放行（按代码推断会放行）",
            "未覆盖前端是否对「脚本已改动」给出提示",
        ],
    },
    {
        "case_id": "SCI-04",
        "title": "四路等价与写回失败 409 字节不变",
        "coverage_status": "已有完整断言",
        "existing_tests": [
            "tests/test_equivalence_matrix.py::test_three_ways_agree（21 组，热态/清空重放/全新 worker，app._compare_manifests）",
            "tests/test_equivalence_matrix.py::test_write_back_then_reopen_matches_the_hot_session（8 组第四路：写回后全新 worker，6 对两两比 + PDF 页面尺寸与文字）",
            "tests/test_equivalence_matrix.py::test_three_ways_agree_on_workerd 等 workerd 腿",
            "tests/test_write_back.py（假 worker：warnings / replay_divergence / 像素门 / expected_mtime / 锁回滚，均断言 PDF/PNG 字节不变）",
            "tests/test_worker_roundtrip.py::test_write_back_blocks_when_the_script_changed_mid_session / "
            "test_write_back_blocks_attribute_only_divergence（真链路 script_changed、纯属性像素分歧）",
        ],
        "expected_outcome": "四路两两一致；prepare（source_changed）、verify（write_back_warnings / replay_divergence 几何）、"
        "commit（file_locked 首个 / 第二个目标）在真 worker 上一律 409，PDF+PNG 原件逐字节不变、无 .updating、基线不推进。",
        "product_outcome": "自动成功",
        "test_verdict": "partial",
        "verdict_reason": "四路等价 31 passed + 6 skipped（workerd 产物缺失）；在 worktree 内 cargo build 后 workerd 腿 6 passed——"
        "skip 已补跑，不计为 skip。新增真链路 409 用例 5 passed（+ test_write_back.py 22 = 27 passed）。"
        "偏差：verify 阶段一次性 worker 导出崩溃（WorkerError）时回 500 而不是 409（字节不变、无半成品），"
        "与根 AGENTS.md「任一环不过一律 409」字面不符；现有 tests/test_write_back.py::"
        "test_write_back_cleans_updating_when_second_export_fails 断言的恰是 500——合同与测试相互矛盾，记低严重度。",
        "source_sha": SOURCE_SHA,
        "environment": ENV,
        "fixtures": fx(
            "tests/test_equivalence_matrix.py",
            "tests/test_write_back_real_409.py",
            f"{R}/test_sci05_writeback_faults.py",
        ),
        "entry": "pytest（真 worker + Flask test client /api/engine/update_source）",
        "commands": [
            f"{PY} tests/test_equivalence_matrix.py -q",
            "(cd workerd && cargo build --offline) && "
            + f"{PY} tests/test_equivalence_matrix.py -q -k workerd",
            f"{PY} tests/test_write_back_real_409.py tests/test_write_back.py",
            f"{PY} {R}/test_sci05_writeback_faults.py -s -k staging",
            f"{MUT} SCI-04 commit-without-rollback src/tavotto/app.py "
            "'            rolled, failed = _rollback(done, backup_dir)' '            rolled, failed = [], []' "
            "-- tests/test_write_back_real_409.py",
            f"{MUT} SCI-04 geometry-gate-disabled src/tavotto/app.py "
            "'            diffs, compared = _compare_manifests(man_hot, man_fresh)' "
            "'            diffs, compared = [], 0' -- tests/test_write_back_real_409.py",
        ],
        "exit_codes": {
            "equivalence_matrix": 0,
            "equivalence_matrix_workerd": 0,
            "new_test_real_409": 0,
            "staging_failure_repro": 1,
            "mutation_rollback": 1,
            "mutation_geometry_gate": 1,
            "restored": 0,
        },
        "evidence": log(
            "SCI-04",
            "equivalence 31 passed 6 skipped → workerd 6 passed；real 409 + write_back 27 passed；"
            "staging 失败 repro: 500（合同 409）；两次变异均 1 failed → 还原全绿",
        ),
        "artifacts": [],
        "first_failed_invariant": "verify 阶段 staging 导出异常应回 409（实际 500；字节不变这一半成立）",
        "mutation_check": {
            "target_file": "src/tavotto/app.py",
            "mutation": "① commit 撞锁不回滚（rolled, failed = [], []）；② 几何门失效（_compare_manifests → [], 0）",
            "expected_red_case": "tests/test_write_back_real_409.py::test_commit_lock_is_409_file_locked_and_the_other_target_is_rolled_back[second-target] / "
            "::test_verify_geometry_drift_in_the_hot_session_is_409_replay_divergence",
            "observed": "red",
            "restored_green": True,
            "first_failed_assertion": "① body['rolled_back'] == seen[:1]；② ('axes_0','bbox') in fields",
        },
        "new_tests": [
            "tests/test_write_back_real_409.py::test_prepare_stale_mtime_is_409_and_both_originals_keep_their_bytes",
            "tests/test_write_back_real_409.py::test_verify_worker_warning_on_a_missing_gid_is_409_and_nothing_is_replaced",
            "tests/test_write_back_real_409.py::test_verify_geometry_drift_in_the_hot_session_is_409_replay_divergence",
            "tests/test_write_back_real_409.py::test_commit_lock_is_409_file_locked_and_the_other_target_is_rolled_back[first-target]",
            "tests/test_write_back_real_409.py::test_commit_lock_is_409_file_locked_and_the_other_target_is_rolled_back[second-target]",
        ],
        "product_bugs": [
            {
                "summary": "写回 verify 阶段一次性 worker 导出崩溃时 update_source 回 500（code 为空），不是合同要求的 409；原件字节不变、无残留",
                "repro": f"{PY} {R}/test_sci05_writeback_faults.py -s -k staging",
                "severity": "低（合同与实现 / 既有测试不一致；数据安全这一半成立）",
            }
        ],
        "gaps": [
            "commit 锁冲突用 monkeypatch Path.replace 模拟（macOS 无 Windows 独占锁）",
            "四路等价未在 Windows 平台跑",
        ],
    },
    {
        "case_id": "SCI-05",
        "title": "保存崩溃：autosave 排队、临时文件、提交前/后被杀、磁盘满、外部冲突",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_document_persistence.py::test_replace_failure_keeps_the_old_file_and_cleans_up / "
            "test_write_json_fsyncs_the_file_before_replacing / test_directory_fsync_failure_is_not_swallowed / "
            "test_revision_baseline_blocks_a_write_over_someone_elses_content（进程内 monkeypatch，未真杀进程）",
            "tests/test_autosave.py::test_stale_base_rejected_and_disk_untouched",
            "web/src/lib/autosave/diskWriter.test.ts（排队只留最新一份、409 之后基线原样、io 失败不清本机副本、冲突挡住排队那份）",
            "tests/test_write_back.py::test_a_locked_second_target_rolls_the_first_one_back（commit 锁回滚；不含备份阶段失败）",
        ],
        "expected_outcome": "提交前被杀：盘上仍是已确认版本、半文件不被当成文档；提交后被杀：盘上是完整新版本且客户端未收到成功；"
        "磁盘满：非 2xx + 结构化 code、旧版本不变、无 tmp；外部冲突：409、不覆盖、不推基线；写回事务任何一步磁盘满：409、原件零改动、无半成品。",
        "product_outcome": "功能失败",
        "test_verdict": "fail",
        "verdict_reason": "自动保存一侧全部符合：真子进程 os._exit(137) 于 os.replace 前 → 盘上仍 v1、GET=100、tmp 不进 /api/layouts；"
        "replace 后 → 盘上完整 v2、客户端无响应；fsync ENOSPC → 500 write_failed、槽位不变、无新 tmp；外部改动 → 409 external_change。"
        "既有后端 102 passed、前端 diskWriter 17 passed。失败：写回事务 commit 阶段给第二个目标做备份时 ENOSPC → "
        "Fig1.pdf 已被替换、Fig1.png 未换、`.Fig1.png.updating` 残留、HTTP 500、无回滚、基线未入账——违反「PDF 新 / PNG 旧比整件事失败糟糕得多」。",
        "source_sha": SOURCE_SHA,
        "environment": ENV,
        "fixtures": fx(f"{R}/sci05_autosave_crash.py", f"{R}/test_sci05_writeback_faults.py"),
        "entry": "HTTP（Flask test client PUT /api/autosave、/api/engine/update_source）+ 真子进程被杀；vitest（diskWriter 假端口）",
        "commands": [
            f"{RUNPY} {R}/sci05_autosave_crash.py",
            f"{PY} {R}/test_sci05_writeback_faults.py -s",
            f"{PY} tests/test_autosave.py tests/test_document_persistence.py tests/test_write_back.py tests/test_write_back_real_409.py",
            "bash docs/qa/2026-09-24/sci/repro/vitest.sh src/lib/autosave/diskWriter.test.ts",
            f"MUTATE_RUNNER=runpy.sh {MUT} SCI-05 write-failure-leaves-tmp src/tavotto/engine/atomicio.py "
            "'<_discard(tmp) + raise write_failed 两行>' '<只 raise>' -- docs/qa/2026-09-24/sci/repro/sci05_autosave_crash.py",
        ],
        "exit_codes": {
            "autosave_crash_probe": 0,
            "writeback_faults_repro": 1,
            "backend_existing": 0,
            "vitest_diskWriter": 0,
            "mutation": 1,
            "restored": 0,
        },
        "evidence": log(
            "SCI-05",
            "autosave 探针 K1/K2/D1/C1 全 ok；写回 faults repro 2 failed（备份 ENOSPC: changed=['Fig1.pdf'], "
            "leftovers=['.Fig1.png.updating'], 500；staging 失败 500）；后端 102 passed；vitest 17 passed；变异红→还原绿",
        ),
        "artifacts": [],
        "first_failed_invariant": "写回 commit：任一目标失败时已替换的目标必须回滚、不留 .updating（备份阶段 ENOSPC 时未回滚）",
        "mutation_check": {
            "target_file": "src/tavotto/engine/atomicio.py",
            "mutation": "write_bytes 写失败时不 _discard(tmp)",
            "expected_red_case": "docs/qa/2026-09-24/sci/repro/sci05_autosave_crash.py D1_disk_full.new_tmp_leftovers",
            "observed": "red",
            "restored_green": True,
            "first_failed_assertion": "D1_disk_full.new_tmp_leftovers = ['d1.json.<pid>.2.tmp']",
        },
        "new_tests": [],
        "product_bugs": [
            {
                "summary": "写回事务 commit 阶段 shutil.copy2 备份在 try 之外：第二个目标备份失败（ENOSPC 等 OSError）时已替换的 PDF 不回滚、"
                "`.Fig1.png.updating` 残留、回 500（app.py _write_source_files 的 commit 循环）",
                "repro": f"{PY} {R}/test_sci05_writeback_faults.py -s -k backup",
                "severity": "高（写回是全工具唯一覆盖用户原件的操作；PDF/PNG 版本分裂且无回滚）",
            },
            {
                "summary": "自动保存进程在写完临时文件、os.replace 前被杀，留下的 `<doc>.json.<pid>.<n>.tmp` 永不回收"
                "（_prune_autosave_slots 只数 .json）；不会被当成文档，仅磁盘泄漏",
                "repro": f"{RUNPY} {R}/sci05_autosave_crash.py（看 K1_kill_before_commit.orphan_tmp）",
                "severity": "低",
            },
        ],
        "gaps": [
            "「autosave 排队时杀进程」只在前端假端口层验证（diskWriter.test.ts），没有真浏览器 + 真后端被杀的 E2E",
            "文件占用（Windows 独占锁）只以 monkeypatch 模拟",
            "恢复后的撤销历史 / 文档 history 未验证（只验了盘上内容与 GET）",
        ],
    },
    {
        "case_id": "SCI-06",
        "title": "导出参数：尺寸 / DPI / 透明 / 裁剪 / 原图范围，读取产物复核",
        "coverage_status": "已有完整断言",
        "existing_tests": [
            "tests/test_export_pipeline.py::test_original_export_ignores_the_layout_scale（原图不套画布 x/y/w/h）",
            "tests/test_export_pipeline.py::test_canvas_export_is_faithful_to_the_canvas / test_pdf_and_png_come_from_one_snapshot",
            "tests/test_export_pipeline.py::test_canvas_tiff_is_the_same_page_as_png_pixel_for_pixel（独立 TIFF 解析器 + 分辨率标签）",
            "tests/test_export_pipeline.py::test_transparent_background_actually_leaves_the_background_transparent",
            "tests/test_export_inspection.py::test_strict_inspection_blocks_on_required_unknown_or_failure_but_standard_only_notes / "
            "test_an_inspector_crash_is_unknown_not_verified / test_a_page_of_the_wrong_actual_size_is_caught_not_reported_from_the_request",
            "web/src/lib/artifactInspection.test.ts（unknown 不画绿、rejected 永不 verified）",
        ],
        "expected_outcome": "PDF 页面盒 = 画布 mm（两把独立尺子）；面板（含 crop）落位矩形在 PDF 栅格 / PNG / TIFF 中与测试侧独立换算一致（±2px@300ppi）；"
        "位图像素数与 dpi 标签 = ppi；透明背景空白处 alpha=0、对象处不透明；严核失败 / 未知不显示 verified。",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "既有后端 153 passed、vitest artifactInspection 8 passed；新增 tests/test_export_anchor_readback.py 4 passed"
        "（锚点、crop、页面盒、dpi、透明）。编写中一次红来自我方输入形状错误（crop 传了 list，产品只认 {x,y,w,h} dict，"
        "list 被静默忽略）——已按前端 CropRect 形状改正，记为缺口（未校验的 crop 形状被静默当成无裁剪）。",
        "source_sha": SOURCE_SHA,
        "environment": ENV,
        "fixtures": fx("tests/test_export_anchor_readback.py", "tests/support/pdfread.py"),
        "entry": "pytest（Flask test client /api/export，RenderCore 默认后端）+ vitest",
        "commands": [
            f"{PY} tests/test_export_pipeline.py tests/test_export_request.py tests/test_export_endpoint.py "
            "tests/test_export_inspection.py tests/test_export_identity.py tests/test_tiffwrite.py tests/test_epsfile.py "
            "tests/test_mcp_export_inspection.py tests/test_export_phase_labels.py",
            "bash docs/qa/2026-09-24/sci/repro/vitest.sh src/lib/artifactInspection.test.ts",
            f"{PY} tests/test_export_anchor_readback.py",
            f"{MUT} SCI-06 panel-y-not-flipped src/tavotto/rendercore/plan.py '    rect = (x, page_h - y - h, w, h)' "
            "'    rect = (x, y, w, h)' -- tests/test_export_anchor_readback.py",
        ],
        "exit_codes": {
            "backend_existing": 0,
            "vitest": 0,
            "new_test": 0,
            "mutation": 1,
            "restored": 0,
        },
        "evidence": log(
            "SCI-06",
            "后端 153 passed；vitest 8 passed；新测 4 passed；变异 3 failed → 还原 4 passed",
        ),
        "artifacts": [],
        "first_failed_invariant": None,
        "mutation_check": {
            "target_file": "src/tavotto/rendercore/plan.py",
            "mutation": "面板矩形漏掉 y 轴翻转（page_h - y - h → y）",
            "expected_red_case": "tests/test_export_anchor_readback.py::test_panel_anchor_lands_where_the_canvas_put_it_in_every_format",
            "observed": "red",
            "restored_green": True,
            "first_failed_assertion": "PDF（PyMuPDF 栅格）的 y0：产物 661px，独立换算 307.1px",
        },
        "new_tests": [
            "tests/test_export_anchor_readback.py::test_panel_anchor_lands_where_the_canvas_put_it_in_every_format[whole-page]",
            "tests/test_export_anchor_readback.py::test_panel_anchor_lands_where_the_canvas_put_it_in_every_format[left-half-crop]",
            "tests/test_export_anchor_readback.py::test_transparent_background_is_transparent_outside_the_box_and_opaque_inside",
            "tests/test_export_anchor_readback.py::test_page_box_is_read_by_a_reader_that_shares_no_code_with_the_writer",
        ],
        "product_bugs": [],
        "gaps": [
            "crop 为非 dict 形状（如 list）时 rendercore/plan.py 静默当作无裁剪，不报错（前端只发 dict，未判为缺陷）",
            "字体维度只由既有 test_export_endpoint 的斜体 / CJK 用例覆盖，本次未扩",
            "旋转 / 翻转与 crop 组合下的锚点未量",
            "EPS 只跑了既有用例",
        ],
    },
    {
        "case_id": "SCI-07",
        "title": "导出版本：编辑后立即导出、慢渲染 + 第二次编辑、旧版本不冒充最新",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_export_pipeline.py::test_two_concurrent_override_renders_do_not_share_one_intermediate_file（假 worker，只判中间路径不同）",
            "tests/test_export_pipeline.py::test_document_revision_is_echoed_back",
            "tests/test_export_pipeline.py::test_a_raster_panel_with_overrides_is_re_rendered_not_copied（假 resolve）",
        ],
        "expected_outcome": "编辑后立即导出的产物含该编辑；作业 1（版本 A）被放慢期间完成的第二次编辑 + 作业 2（版本 B）互不串：A 文件只有 A、"
        "B 文件只有 B，各自回显自己的 document_revision；慢作业不覆盖 B；§9 变异「导出使用旧版本」必须让用例红。",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "新增 tests/test_export_version_snapshot.py 的两条 SCI-07 用例 passed；四个新测试文件连跑 5 轮全绿（非靠重试）。"
        "§9 反证：_execution_source 丢掉 overrides（导出编辑前的旧版本）→ 3 failed（Version A 不在产物里）→ 还原全绿。",
        "source_sha": SOURCE_SHA,
        "environment": ENV,
        "fixtures": fx("tests/test_export_version_snapshot.py"),
        "entry": "pytest（Flask test client /api/export、/api/export/start + /state；真 worker）",
        "commands": [
            f"{PY} tests/test_export_version_snapshot.py -k 'slow or immediate'",
            "bash docs/qa/2026-09-24/sci/repro/stability.sh",
            f"{MUT} SCI-07 export-uses-pre-edit-version src/tavotto/app.py "
            "'    overrides = obj.get(\"overrides\") or []' '    overrides = []' -- tests/test_export_version_snapshot.py",
        ],
        "exit_codes": {"new_test": 0, "stability_x5": 0, "mutation": 1, "restored": 0},
        "evidence": log("SCI-07", "新测 2 passed；稳定性 5 轮 rc=0；变异 3 failed → 还原 3 passed"),
        "artifacts": [],
        "first_failed_invariant": None,
        "mutation_check": {
            "target_file": "src/tavotto/app.py",
            "mutation": "_execution_source: overrides = [] （导出使用编辑前的旧版本，§9 反证表）",
            "expected_red_case": "tests/test_export_version_snapshot.py::test_edit_then_immediate_export_ships_the_edit_not_the_disk_file / "
            "::test_a_slow_export_of_version_a_never_ships_version_b",
            "observed": "red",
            "restored_green": True,
            "first_failed_assertion": "assert 'Version A' in text_a",
        },
        "new_tests": [
            "tests/test_export_version_snapshot.py::test_a_slow_export_of_version_a_never_ships_version_b",
            "tests/test_export_version_snapshot.py::test_edit_then_immediate_export_ships_the_edit_not_the_disk_file",
        ],
        "product_bugs": [],
        "gaps": [
            "慢渲染以包装 _serialize_figure_with_worker 注入（worker 本身未变慢）",
            "前端侧「导出期间文档又被编辑」的提示（document_revision 比对）未在 UI 层验证",
            "预览缓存（/api/engine/preview_png、render cache）旧响应不参与导出路径，未单测",
        ],
    },
    {
        "case_id": "SCI-08",
        "title": "快照与重算：出图后数据改变，继续快照 vs 明确重新计算",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_execution_receipt.py::test_binding_check_compares_expected_with_observed_and_never_pretends（matched 三档，纯单元）",
            "tests/test_preparation_api.py::test_reusing_a_hot_session_after_the_data_changed_is_ready_but_says_it_is_a_snapshot（准备接口：matched=False + note「旧快照」；不经导出）",
            "docs/rules/backend/preparation-and-receipts.md 合同：复用热态会话时不一致不是错误，不自动重算，重算走 /api/engine/invalidate",
        ],
        "expected_outcome": "改数据后普通导出保留编辑且仍是旧数据，回执 binding.matched=False；/api/engine/invalidate 后导出用新数据、编辑重放、"
        "binding.matched=True；导出不擅自重跑脚本。",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "新增 test_data_change_exports_the_explicit_snapshot_until_the_user_rebuilds passed：快照导出 DATA 0_9_2_10 + Version A、"
        "manifest provenance binding.matched 含 False；invalidate 后 DATA 0_2_9_10 + Version A、matched 全 True。"
        "反证：导出前擅自 invalidate（静默重跑）→ 红在「快照导出擅自用了新数据」→ 还原绿。",
        "source_sha": SOURCE_SHA,
        "environment": ENV,
        "fixtures": fx("tests/test_export_version_snapshot.py"),
        "entry": "pytest（Flask test client /api/engine/render、/api/export、/api/engine/invalidate；真 worker）",
        "commands": [
            f"{PY} tests/test_export_version_snapshot.py -k data_change",
            f"{MUT} SCI-08 export-silently-reruns-script src/tavotto/app.py "
            '\'        worker = _safe_worker(info["script"], info["entry"], stem)\' '
            "'<前插 engine_pool.invalidate(info[\"script\"], str(require_project()))>' "
            "-- tests/test_export_version_snapshot.py -k data_change",
        ],
        "exit_codes": {"new_test": 0, "mutation": 1, "restored": 0},
        "evidence": log("SCI-08", "新测 1 passed；变异 1 failed → 还原 1 passed"),
        "artifacts": [],
        "first_failed_invariant": None,
        "mutation_check": {
            "target_file": "src/tavotto/app.py",
            "mutation": "_serialize_figure_with_worker 导出前 engine_pool.invalidate（导出擅自重跑脚本）",
            "expected_red_case": "tests/test_export_version_snapshot.py::test_data_change_exports_the_explicit_snapshot_until_the_user_rebuilds",
            "observed": "red",
            "restored_green": True,
            "first_failed_assertion": "快照导出擅自用了新数据",
        },
        "new_tests": [
            "tests/test_export_version_snapshot.py::test_data_change_exports_the_explicit_snapshot_until_the_user_rebuilds"
        ],
        "product_bugs": [],
        "gaps": [
            "「显示语义切换」只验了后端回执字段，前端是否把 binding.matched=False 呈现为「旧快照」未验证",
            "只覆盖带 override 的面板（执行侧源）；无 override 面板导出取磁盘原件，属另一条快照语义，未单测",
            "native 会话（invalidate 回 invalidated:false）未覆盖",
        ],
    },
]


def main() -> None:
    for c in CASES:
        c.setdefault("fixtures", [])
    (SEC / "ledger.json").write_text(
        json.dumps(CASES, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(CASES)} cases")


if __name__ == "__main__":
    main()
