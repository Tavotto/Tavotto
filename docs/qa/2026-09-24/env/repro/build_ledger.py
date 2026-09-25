"""把 scratch 里的原始日志收成 ``docs/qa/2026-09-24/env/logs/<CASE>.log`` 并生成 ``ledger.json``。

    python docs/qa/2026-09-24/env/repro/build_ledger.py --raw <scratch>/raw --e1 <scratch>/e1 --final <scratch>/final

* 每个 case 的日志 = 该 case 执行过的每条命令的原始输出，按节拼接；单节超过 190 行只留头 40 + 尾 150 行并注明截断；
  ``/Users/<名字>`` 一律改写成 ``~``（不留用户真实路径）。
* hash 全部现算：日志、夹具（``e1.json`` 里点名的文件 + wheel）、导出物。
* pytest 摘要（passed / failed / skipped）从日志最后的摘要行解析；探针的 PASS / FAIL 计数从 ``RESULT`` 行解析。
* 各 case 的文字字段（预期 / 结论 / 缺口）写在本文件的 ``CASES`` 里——执行前写定的预期不随结果改。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
SECTION = HERE.parent
SHA = "62af729c2f84652bf3979c25b0c3117fcfa756a5"
HOME_RE = re.compile(r"/Users/[^/\s'\"]+")

ENVIRONMENT = {
    "os": "macOS 27.0 (26A428)",
    "arch": "arm64",
    "app_python": "3.13.11（/Volumes/Projects/Tavotto/.venv，flask 3.1.3；该 .venv 实测装有 matplotlib 3.11.2 / numpy 2.5.2——与任务说明「没有 matplotlib」不符，如实记录）",
    "e1_python": "3.13.11（Homebrew python@3.13，uv 0.11.18 建 venv）",
    "e1_matplotlib": "3.10.8",
    "e1_numpy": "2.5.3",
    "node": "v26.7.0（本节未用）",
    "browser": "未用（本节无浏览器用例）",
    "render_backend": "rendercore（TAVOTTO_FONTS_DIR 指向 scratch 里经 fetch_fonts.py --check 校验过的 13 张批准字体）",
}

PYTEST = (
    "PYTHONPATH=$PWD/src PYTHONDONTWRITEBYTECODE=1 /Volumes/Projects/Tavotto/.venv/bin/python "
    "-m pytest {t} -p no:cacheprovider -rs -v"
)
PROBE = (
    "TAVOTTO_FONTS_DIR=<scratch>/fonts PYTHONPATH=$PWD/src PYTHONDONTWRITEBYTECODE=1 "
    "/Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/env/repro/{script} {case} "
    "--e1 <scratch>/e1 --out <scratch>/final"
)
MAKE_E1 = (
    "/opt/homebrew/opt/python@3.13/libexec/bin/python3 docs/qa/2026-09-24/env/repro/make_e1.py "
    "--dest <scratch>/e1"
)

# (日志节名, 原始日志文件名)
CASES: dict[str, dict] = {
    "ENV-01": {
        "title": "项目 venv 首开",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_first_open_environment.py::test_a_healthy_project_venv_is_chosen_before_the_first_run_and_remembered",
            "tests/test_project_env.py::test_replay_and_export_use_the_same_interpreter",
            "tests/test_foundation_first_open.py::test_fo01_same_directory_csv_opens_automatically",
        ],
        "expected_outcome": "自动成功：内置 / 自身解释器 import 不到 qa_probe_pkg，项目 .venv（A）装齐；HTTP 首开的准备计划在第一次执行前就选 A（source=project_venv, trigger=first_open, 回执 generation=1），首次渲染、编辑后渲染与 RenderCore 导出里脚本自报的 sys.executable / sys.prefix / 包 __file__ / 版本都是 A，完整点序列 = [0, 9, 2, 10]；A / B 两个 venv 前后包清单与文件（白名单：__pycache__）不变。",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "探针 30/30：计划 project_venv/first_open、回执 generation=1 / pid_check ok、三次执行（首渲 / 改线色 / 导出）的图内身份与完整点序列都指向 A，导出 PDF 文字层（pypdfium2 独立读取）含 A 的点序列与包路径、不含 B 的；副作用零差异。已有用例只在 pool 层钉「选 A + prefix」，不量包来源 / 点序列 / HTTP 导出，故覆盖记「部分」。辅助判据「服务日志里没有 missing_dependency」在 M2 变异下仍绿——它是弱判据，结论不依赖它（依赖 generation==1 与计划来源）。",
        "entry": "HTTP（python -m tavotto + 会话认证，端口 5303）+ pytest",
        "sections": [
            ("探针 ENV-01（已还原树）", "ENV-01.probe.log"),
            ("pytest test_first_open_environment.py", "test_first_open_environment.log"),
            ("pytest test_project_env.py", "test_project_env.log"),
        ],
        "commands": [
            MAKE_E1,
            PROBE.format(script="e1_http_probe.py", case="ENV-01"),
            PYTEST.format(t="tests/test_first_open_environment.py"),
            PYTEST.format(t="tests/test_project_env.py"),
        ],
        "artifacts_from": "ENV-01",
        "first_failed_invariant": None,
        "mutation_check": {
            "target_file": "src/tavotto/engine/pool.py",
            "mutation": "resolve_worker_python 第 1072 行 `if discover:` → `if False:`（首开不发现项目 venv）",
            "expected_red_case": "ENV-01/ENV-02 探针「计划选择来源 = project_venv」「回执 generation = 1」；tests/test_first_open_same_name_package.py::test_the_project_venv_wins_over_a_same_name_package_earlier_on_path",
            "observed": "red",
            "restored_green": True,
            "note": "与 ENV-02 共用同一变异；变异下探针 ENV-02 28/31（source=current_process、trigger 空、generation=2 = 先在内置跑错一次再接手），新用例红在 `assert 'current_process' == 'project_venv'`；还原后探针 31/31、新用例 2 passed。日志见 ENV-02.log。",
        },
        "new_tests": [],
        "product_bugs": [],
        "gaps": [
            "桌面壳入口未测（只测 HTTP 公共入口）。",
            "编辑后渲染复用同一热会话；「全新 worker 重放」由 ENV-07（重启后端）覆盖，写回 verify 的一次性 worker 未在本节单独量。",
            "回执 runtime 的公开投影不带路径（ADR 0053 §二），解释器路径证据来自脚本自报（图内文字），不是回执。",
        ],
    },
    "ENV-02": {
        "title": "同名包版本冲突",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_first_open_environment.py::test_fo17_two_venvs_on_the_same_base_are_kept_apart",
            "tests/test_first_open_same_name_package.py::test_the_project_venv_wins_over_a_same_name_package_earlier_on_path（本次新增）",
        ],
        "expected_outcome": "自动成功：B（qa_probe_pkg 2.0.0 → [1, 1, 1, 1]）在服务进程 PATH 最前、两份都 import 得到（前提独立核对），产品选 A；worker 自报身份 / 包 __file__ / 完整点序列 [0, 9, 2, 10] / 导出文字层全部指向 A，不能以 import 成功代替。",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "探针 31/31（含前提：PATH 上第一个 python 是 B 且 import 得到 qa_probe_pkg → [1, 1, 1, 1]）。本次新增 pool 层用例（同名包写进两个真实 venv、B 前置 PATH、全序列 + 包文件 + prefix）2 passed，并做了变异反证。用户环境发现（ADR 0079）按产品默认开着（服务进程未设 TAVOTTO_USER_ENV_DISCOVERY）。",
        "entry": "HTTP（端口 5303）+ pytest",
        "sections": [
            ("探针 ENV-02（已还原树）", "ENV-02.probe.log"),
            ("变异 M2 下的探针 ENV-02", "ENV-02.M2_mutated.probe.log"),
            ("变异 M2 下的新用例", "test_first_open_same_name_package.M2_mutated.log"),
            ("还原后新用例", "test_first_open_same_name_package.M3_restored.log"),
        ],
        "commands": [
            MAKE_E1,
            PROBE.format(script="e1_http_probe.py", case="ENV-02"),
            PYTEST.format(t="tests/test_first_open_same_name_package.py"),
        ],
        "artifacts_from": "ENV-02",
        "first_failed_invariant": None,
        "mutation_check": {
            "target_file": "src/tavotto/engine/pool.py",
            "mutation": "resolve_worker_python 第 1072 行 `if discover:` → `if False:`",
            "expected_red_case": "tests/test_first_open_same_name_package.py::test_the_project_venv_wins_over_a_same_name_package_earlier_on_path；探针 ENV-02",
            "observed": "red",
            "restored_green": True,
            "note": "新用例红在 `assert source == SOURCE_PROJECT_VENV`（'current_process'）；第二条新用例（显式失效→纠正）在此变异下仍绿——它的主语不是首开发现，符合预期。探针红在计划来源 / trigger / generation=2。",
        },
        "new_tests": [
            "tests/test_first_open_same_name_package.py::test_the_project_venv_wins_over_a_same_name_package_earlier_on_path"
        ],
        "product_bugs": [],
        "gaps": [
            "项目没有 .venv 时 B（PATH 前置）会不会被 ADR 0079 自动采用未测（那是另一条合同：用户环境自动改用）。",
            "新用例在 pool 层；HTTP + 导出那一腿只在 repro 探针里，不进 CI。",
        ],
    },
    "ENV-03": {
        "title": "显式失效",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_first_open_environment.py::test_fo15_an_explicit_env_var_without_matplotlib_stops_instead_of_falling_through",
            "tests/test_first_open_environment.py::test_a_configured_interpreter_that_is_gone_or_broken_stops_with_the_reason",
            "tests/test_first_open_environment.py::test_a_stale_env_var_is_still_ignored_but_a_stale_setting_is_not",
            "tests/test_first_open_environment.py::test_a_stale_env_var_and_a_stale_setting_on_the_same_path_still_stop",
            "tests/test_first_open_environment.py::test_fo16_a_user_chosen_project_interpreter_that_broke_stops_with_the_reason",
            "tests/test_foundation_first_open.py::test_fo15_explicit_interpreter_without_matplotlib_stops_with_a_reason",
            "tests/test_first_open_same_name_package.py::test_an_unusable_explicit_interpreter_stops_and_clearing_it_recovers_the_project_venv（本次新增）",
        ],
        "expected_outcome": "安全停止 + 引导后成功：(a) TAVOTTO_WORKER_PYTHON=无 matplotlib 的真 venv → explicit_python_unusable(env_override/no_matplotlib)，一行不跑、不记住项目 venv；去掉后首开选 A 成功。(b) 环境变量指向不存在路径 → 按 ADR 0057 视为没设，选 A。(c) 设置里指定的全局解释器消失 → explicit_python_unusable(configured/missing)，清除后成功。(d) 项目手选环境失效 → project_python_unusable，选回默认后可运行。",
        "product_outcome": "安全停止",
        "test_verdict": "fail",
        "verdict_reason": "(a)(a')(b)(d)(d') 全过；(c) 偏离合同：PATCH 设好全局解释器 C 后（尚无热会话）删除 C，同一后端进程里 prepare 与 render 回 `internal_error`（原始 FileNotFoundError），环境状态端点仍报 resolution_error=null、python=C；重启后端后才按合同回 explicit_python_unusable/missing。没有静默换成 A（安全停止成立），但错误码 / 原因不对、界面无出路。最小复现 repro/env03_configured_python_vanishes.py（退出码 1）。另：若删除前已有热会话，后续 prepare / render 复用仍活着的进程照常 ready——属既有「复用热会话」设计，未计为缺陷。",
        "entry": "HTTP（端口 5303）+ pytest",
        "sections": [
            ("探针 ENV-03（已还原树）", "ENV-03.probe.log"),
            ("最小复现：设置里的解释器在进程内消失", "ENV-03.vanish.probe.log"),
            ("变异 M1 下的探针 ENV-03", "ENV-03.M1_mutated.probe.log"),
            ("变异 M1 下的新用例", "test_first_open_same_name_package.M1_mutated.log"),
            ("变异 M1 下的既有用例", "test_first_open_environment.M1_mutated.log"),
            ("变异 M1 下的 FO15（HTTP）", "test_foundation_first_open.M1_mutated_fo15.log"),
            ("还原后：新用例", "test_first_open_same_name_package.M1_restored.log"),
            ("还原后：既有用例", "test_first_open_environment.M1_restored.log"),
            ("还原后：FO15", "test_foundation_first_open.M1_restored.log"),
            ("变异 M3 下（项目级手选失效）", "test_first_open_environment.M3_mutated.log"),
            ("还原后（M3）", "test_first_open_environment.M3_restored.log"),
            ("pytest test_foundation_first_open.py（基线）", "test_foundation_first_open.log"),
        ],
        "commands": [
            MAKE_E1,
            PROBE.format(script="e1_http_probe.py", case="ENV-03"),
            PROBE.format(script="env03_configured_python_vanishes.py", case="").replace("  ", " "),
            PYTEST.format(
                t="tests/test_first_open_environment.py tests/test_first_open_same_name_package.py"
            ),
            PYTEST.format(
                t="tests/test_foundation_first_open.py::test_fo15_explicit_interpreter_without_matplotlib_stops_with_a_reason"
            ),
        ],
        "artifacts_from": None,
        "first_failed_invariant": "(c) 设置里指定的全局解释器在同一进程内消失：期望 explicit_python_unusable/reason=missing，实得 internal_error（FileNotFoundError）；环境状态端点 resolution_error=null",
        "mutation_check": {
            "target_file": "src/tavotto/engine/pool.py",
            "mutation": 'M1（§9「显式环境失效仍回退」）：select_worker_python 第 962 行 `raise _explicit_unusable(source, cand, "no_matplotlib")` → `continue`；另做 M3：第 1070 行项目手选失效的 `raise _project_python_unusable(...)` → `pass`',
            "expected_red_case": "ENV-03 探针 (a)；tests/test_first_open_same_name_package.py::test_an_unusable_explicit_interpreter_stops_and_clearing_it_recovers_the_project_venv；tests/test_first_open_environment.py 的 FO15 / configured 两条；tests/test_foundation_first_open.py::test_fo15；M3 → test_fo16_a_user_chosen_project_interpreter_that_broke_stops_with_the_reason",
            "observed": "red",
            "restored_green": True,
            "note": "M1：探针 22/27，(a) 四条全红（环境状态无 resolution_error、prepare 回 missing_dependency、render 回 missing_dependency、用户配置里被写进了 trigger=missing_dependency 的自动接手——正是「静默切换」）；新用例红在 `assert 'missing_dependency' == 'explicit_python_unusable'`；既有 2 failed（DID NOT RAISE）；FO15 HTTP 1 failed。M3：FO16 DID NOT RAISE。还原后全部回绿（新用例 2 passed、既有 11 passed 2 skipped、FO15 1 passed、FO16 1 passed）。",
        },
        "new_tests": [
            "tests/test_first_open_same_name_package.py::test_an_unusable_explicit_interpreter_stops_and_clearing_it_recovers_the_project_venv"
        ],
        "product_bugs": [
            {
                "summary": "设置里指定的全局解释器在后端运行中被删除（尚未起过会话）后，prepare / render 回 internal_error（FileNotFoundError）而不是 explicit_python_unusable/missing；环境状态端点仍报旧路径且 resolution_error=null；重启后端后才正确。",
                "repro": "docs/qa/2026-09-24/env/repro/env03_configured_python_vanishes.py（退出码 1 = 复现；日志 logs/ENV-03.log「最小复现」节）",
                "severity": "P2（安全停止成立、不静默换环境；但错误分类错、原始异常外露、界面无恢复出口）",
                "suspected_cause": "pool.select_worker_python 进程内缓存 _worker_python 命中后不再核对路径是否仍存在（只在首次挑选时按来源判 missing）。",
            }
        ],
        "gaps": [
            "(c)(d) 的「失效」是在运行中制造的；「首次启动时就已失效」由既有 pool 层用例覆盖。",
            "界面文案未经前端核对；另见观察：explicit_python_unusable 文案「你在设置里指定的环境 指定的解释器用不了」标签与句式拼接重复（文案问题，未计缺陷）。",
        ],
    },
    "ENV-04": {
        "title": "项目隔离与更新",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_first_open_environment.py::test_fo17_two_venvs_on_the_same_base_are_kept_apart",
            "tests/test_first_open_environment.py::test_an_automatically_remembered_interpreter_that_broke_is_invalidated_and_rediscovered",
            "tests/test_project_env.py::test_worker_identity_includes_the_interpreter",
            "tests/test_project_env.py::test_never_mixes_site_packages",
        ],
        "expected_outcome": "A/B 两个项目各自 venv（同一后端、交替渲染）不串；pA 的 venv 在同一路径换依赖（qa_probe_pkg 1.0.0→1.1.0）或换成没有 matplotlib 的 Python 后重跑：旧体检 / 旧 worker 失效并重新验证，不沿用旧模块 / 版本；给出可解释的结论。",
        "product_outcome": "功能失败",
        "test_verdict": "fail",
        "verdict_reason": "隔离全过（pA→pB→pA 交替，身份 / 包 / 完整点序列各归各）；换依赖后：不点重新构建直接渲染仍是旧值 [0, 9, 2, 10]（热会话，产品无从得知 site-packages 变了——记为观察），点「重新构建」（/api/engine/invalidate）后正确为 1.1.0 / [3, 1, 4, 1]，pB 不受影响。偏离：同一路径把 .venv 重建成无 matplotlib 后，同一进程里「重新构建」→ 渲染与准备都回 session_dead（通用「渲染进程退出」+ 日志），准备计划仍写 discovery.ok=true、invalidated=null——沿用了旧的健康体检；重启后端才重新体检并作废自动决定（invalidated.reason=no_matplotlib，降级到默认链条并报 missing_dependency qa_probe_pkg）。进程内最小复现 repro/env04_replaced_venv_stale_probe.py（退出码 1）。",
        "entry": "HTTP（端口 5303，同一后端两个项目，?pj=）+ 进程内复现",
        "sections": [
            ("探针 ENV-04（已还原树）", "ENV-04.probe.log"),
            ("最小复现（进程内）", "ENV-04.stale_probe.probe.log"),
        ],
        "commands": [
            MAKE_E1,
            PROBE.format(script="e1_http_probe_more.py", case="ENV-04"),
            "TAVOTTO_DATA_DIR=<scratch>/final/repro-data TAVOTTO_CONFIG_DIR=<scratch>/final/repro-config PYTHONPATH=$PWD/src PYTHONDONTWRITEBYTECODE=1 /Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/env/repro/env04_replaced_venv_stale_probe.py --out <scratch>/final",
        ],
        "artifacts_from": None,
        "first_failed_invariant": "同一路径替换 Python 后同一进程内：准备计划 environment.discovery.ok 仍为 true、invalidated 为 null（旧体检被沿用），会话以 session_dead 失败",
        "mutation_check": None,
        "mutation_check_reason": "发现的是产品缺陷（判据已经在未变异的树上变红），反证的意义是「判据能红」，已由真实缺陷证明；隔离部分的核心判据与 ENV-02 同一套（图内身份 + 全序列），其变异见 ENV-02。",
        "new_tests": [],
        "product_bugs": [
            {
                "summary": "项目 .venv 在同一路径被重建成不可用环境后，同一后端进程里「重新构建」沿用旧的健康体检结论，直接在坏环境里起 worker，以 session_dead 收场，且计划仍写 discovery.ok=true / invalidated=null；只有重启后端才重新体检、作废自动决定。反复重试同样失败。",
                "repro": "docs/qa/2026-09-24/env/repro/env04_replaced_venv_stale_probe.py（退出码 1）；HTTP 形态见 logs/ENV-04.log",
                "severity": "P2（不静默出错图；但无法自愈、原因说不出，需重启应用）",
                "suspected_cause": "pool._project_python_ok（每进程每解释器只复检一次）与 projectenv.first_open_candidate 的按项目缓存没有在 session_dead / invalidate 时失效。",
            }
        ],
        "gaps": [
            "换依赖不点「重新构建」时热会话继续用旧模块且无提示：产品没有 site-packages 指纹，记为观察 / 设计缺口，未计缺陷（ADR 0053 只对数据绑定定义了「明示旧快照」）。",
            "换成**另一个 minor** 的 Python（同一路径）未测：本机没有装 matplotlib 的另一 minor 解释器。",
        ],
    },
    "ENV-05": {
        "title": "本地模块与异常分类",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_dependency_plan.py（importscan 四桶 × 上下文：local / unknown 不装不猜）",
            "tests/test_bundled_runtime.py::test_render_failure_carries_missing_module",
            "tests/test_project_env.py::test_health_probe_separates_missing_module_from_broken_env",
            "tests/test_foundation_first_open.py::test_fo03_file_relative_data_and_local_package_open_automatically",
            "tests/test_foundation_first_open.py::test_fo19_user_modules_shadow_engine_names_and_still_win",
        ],
        "expected_outcome": "本地模块 labmod 自动成功（静态计划归 local、needed=false）；可选 try/except import 与 importlib 动态 import 自动成功；包内部 ImportError、二进制扩展加载失败、已安装包内部缺子模块三类都失败且**不被归为缺包**；本地拼错的名字失败但不被解析成可装的 PyPI 包；项目 venv 包清单不变（不盲目安装）。",
        "product_outcome": "安全停止",
        "test_verdict": "fail",
        "verdict_reason": "12/13：本地 / 可选 / 动态 import 自动成功；cannot-import-name 与 dlopen 失败归 script_error（正确）；没有任何安装（包清单不变），拼错的本地名字 dependency_unresolved、静态计划归 unknown 不猜。偏离：已安装包 qa_subbroken_pkg 在自己 __init__ 里 import 不存在的子模块（报错 No module named 'qa_subbroken_pkg._missing_sub'）被归为 missing_dependency(module=qa_subbroken_pkg)，文案说「当前渲染环境里没有，可以一键装上」——包明明装着。根因在 pool.missing_module() 取点号前的顶层名；真实世界同形：numpy 二进制坏时的 No module named 'numpy.core._multiarray_umath' 会被报成「缺 numpy」（numpy 是 curated 名，修复面板会提供安装）。",
        "entry": "HTTP（端口 5303）+ 纯函数复现",
        "sections": [
            ("探针 ENV-05（已还原树）", "ENV-05.probe.log"),
            ("最小复现（分类函数）", "ENV-05.classifier.probe.log"),
            ("pytest test_dependency_plan.py", "test_dependency_plan.log"),
        ],
        "commands": [
            MAKE_E1,
            PROBE.format(script="e1_http_probe_more.py", case="ENV-05"),
            "PYTHONPATH=$PWD/src /Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/env/repro/env05_submodule_misclassified.py",
            PYTEST.format(t="tests/test_dependency_plan.py"),
        ],
        "artifacts_from": None,
        "first_failed_invariant": "fig_broken_submodule：已安装包内部缺子模块 → code=missing_dependency、module=qa_subbroken_pkg（期望：非缺包分类）",
        "mutation_check": None,
        "mutation_check_reason": "判据已在未变异树上由真实缺陷变红；本节未新增进测试集的 ENV-05 断言（会红的那条放在 repro/）。",
        "new_tests": [],
        "product_bugs": [
            {
                "summary": "已安装包内部缺子模块（No module named 'pkg.sub'）被归为「缺包」missing_dependency(module=pkg)，提示「一键装上」；真实同形如 numpy ABI 损坏的 numpy.core._multiarray_umath。",
                "repro": "docs/qa/2026-09-24/env/repro/env05_submodule_misclassified.py（退出码 1）；HTTP 形态见 logs/ENV-05.log 的 fig_broken_submodule",
                "severity": "P3（不会盲装非 curated 名；对 curated 名会引向一次无效安装与误导文案）",
                "suspected_cause": "pool.missing_module：`m.group(1).split('.')[0]`，没有区分「顶层包不在」与「包在、子模块不在」。",
            }
        ],
        "gaps": [
            "Windows DLL 加载失败形态未测（本机 macOS，dlopen 形态已测）。",
            "夹具的原件 PDF 由 QA_REFERENCE=1（跳过坏 import）产出——面板需要磁盘上的原件；用户终端里的真实结局另行记录在证据里。",
        ],
    },
    "ENV-06": {
        "title": "离线与准备事务",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_dependency_transaction.py::TestTransaction::test_cancel_during_install_leaves_no_active_environment",
            "tests/test_dependency_transaction.py::TestTransaction::test_missing_wheel_and_bad_hash_do_not_activate",
            "tests/test_dependency_transaction.py::TestTransaction::test_disk_low_is_refused_before_building",
            "tests/test_dependency_transaction.py::TestTransaction::test_read_only_environments_dir_fails_cleanly",
            "tests/test_dependency_transaction.py::TestTransaction::test_cancel_accepted_during_the_selftest_is_honored",
            "tests/test_foundation_dependencies.py::test_fo27_cancel_during_install_is_a_clean_terminal_state",
            "tests/test_dependency_repair_e2e.py::test_cancelling_leaves_the_managed_environment_marked_incomplete",
        ],
        "expected_outcome": "已有完整项目环境在断网注入下首开 → 渲染 → 导出全部成功（无需网络、无 pip / uv 调用），用户 venv 包清单与文件不变；受管环境安装中取消 / 缺 wheel（离线）/ 磁盘不足 / 只读目录：半成品不激活（incomplete）、上一代原样、可重试；默认不安装 / 升级 / 删除用户的完整环境。",
        "product_outcome": "自动成功",
        "test_verdict": "partial",
        "verdict_reason": "断网腿（注入点：服务进程环境 HTTP(S)_PROXY / ALL_PROXY = 127.0.0.1:9、PIP_NO_INDEX=1、UV_OFFLINE=1、NO_PROXY=本机回环）探针 11/11，用户 venv 零差异。受管事务腿由既有用例覆盖且本次全绿（test_dependency_transaction 44 passed；test_foundation_dependencies 5 passed / 2 skipped——FO18 / FO05 需联网，skip 不是绿；test_dependency_repair_e2e 首跑因 worktree 缺批准字体导出 backend_unavailable 1 failed，指定 TAVOTTO_FONTS_DIR 后 10 passed）。未执行：真实的安装中途磁盘写满（ENOSPC）与下载中途断网——既有用例只在建代前拒磁盘不足、以「没有 wheel」代表离线，故记 partial。",
        "entry": "HTTP（端口 5303）+ pytest",
        "sections": [
            ("探针 ENV-06（断网注入）", "ENV-06.probe.log"),
            ("pytest test_dependency_transaction.py", "test_dependency_transaction.log"),
            ("pytest test_foundation_dependencies.py", "test_foundation_dependencies.log"),
            (
                "pytest test_dependency_repair_e2e.py（首跑：缺字体）",
                "test_dependency_repair_e2e.log",
            ),
            (
                "pytest test_dependency_repair_e2e.py（TAVOTTO_FONTS_DIR）",
                "test_dependency_repair_e2e.fonts.log",
            ),
        ],
        "commands": [
            MAKE_E1,
            PROBE.format(script="e1_http_probe_more.py", case="ENV-06"),
            PYTEST.format(t="tests/test_dependency_transaction.py"),
            PYTEST.format(t="tests/test_foundation_dependencies.py"),
            "TAVOTTO_FONTS_DIR=<scratch>/fonts "
            + PYTEST.format(t="tests/test_dependency_repair_e2e.py"),
        ],
        "artifacts_from": None,
        "first_failed_invariant": None,
        "mutation_check": None,
        "mutation_check_reason": "本节对 ENV-06 未新增断言；受管事务既有用例自带负向反证（test_dependency_repair.py 十五条），本次未重复变异。",
        "new_tests": [],
        "product_bugs": [],
        "gaps": [
            "安装中途磁盘写满（真实 ENOSPC）未注入。",
            "下载中途断网未注入（既有用例以 PIP_NO_INDEX + 本地 wheelhouse 缺包代表离线）。",
            "FO18 / FO05（受管环境真装 matplotlib）需 TAVOTTO_FOUNDATION_ONLINE=1，本次 skip = not_run。",
        ],
    },
    "ENV-07": {
        "title": "持久化",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_foundation_dependencies.py::test_fo31_first_second_open_and_restart_do_not_prepare_again",
            "tests/test_first_open_environment.py::test_a_healthy_project_venv_is_chosen_before_the_first_run_and_remembered",
            "tests/test_project_env.py::test_remembered_environment_survives_a_project_move",
        ],
        "expected_outcome": "首开 → 渲染 → 导出 → 关服务 → 清数据目录里的运行缓存 → 用同一用户配置重启：prepare 之前环境状态就已是记住的 project_venv（automatic, trigger=first_open），计划与回执说得出选择原因；重启后身份 / 完整点序列 / 导出语义身份 / 回执公开身份与第一轮一致。",
        "product_outcome": "自动成功",
        "test_verdict": "partial",
        "verdict_reason": "探针 18/18：重启前后选择来源 / 原因一致，重启后 prepare 之前就读得到记住的决定，身份与完整点序列一致，两轮导出语义身份与回执 public_identity 逐字相同（重现）。partial 的原因：验收标准里的「配置代际」在回执 / 计划里找不到对应字段（回执的 generation 是 worker 代数，不是配置代际），无法判定，记缺口不记缺陷。",
        "entry": "HTTP（端口 5303，两次启动同一 TAVOTTO_CONFIG_DIR）",
        "sections": [
            ("探针 ENV-07", "ENV-07.probe.log"),
            (
                "pytest test_foundation_dependencies.py（含 FO31 重启）",
                "test_foundation_dependencies.log",
            ),
        ],
        "commands": [
            MAKE_E1,
            PROBE.format(script="e1_http_probe_more.py", case="ENV-07"),
            PYTEST.format(
                t="tests/test_foundation_dependencies.py::test_fo31_first_second_open_and_restart_do_not_prepare_again"
            ),
        ],
        "artifacts_from": None,
        "first_failed_invariant": None,
        "mutation_check": None,
        "mutation_check_reason": "未对持久化路径做变异（时间预算优先给了 §9 点名的 ENV-03）；判据与 ENV-01 同一套身份 / 全序列判据，那套判据的有效性由 ENV-02 / ENV-03 的变异证明。",
        "new_tests": [],
        "product_bugs": [],
        "gaps": [
            "「配置代际」在回执里没有可识别字段；需要产品侧说明它指哪个字段。",
            "桌面壳的完全退出（含 supervisor）未测，只重启了 HTTP 后端。",
        ],
    },
    "ENV-08": {
        "title": "Conda/其它管理器",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_user_environments.py::test_discovery_order_sources_and_labels（假的 conda / pyenv 目录布局 + environments.txt，只读磁盘记录）",
            "tests/test_user_environments.py::test_gate_adopts_the_best_user_environment_and_passes",
            "tests/test_user_environments.py::test_gate_never_overrides_a_user_decision",
        ],
        "expected_outcome": "对声明支持的发现路径（conda 命名环境 / pyenv / 登录 shell / 项目线索）用真实命名环境逐一验证：命名环境被发现并按 import 判装齐；不用 base 冒充命名环境；未实现的自动发现记 planned / 受限，不报自动成功。",
        "product_outcome": "未执行",
        "test_verdict": "not_run",
        "verdict_reason": "本机没有 conda / mamba / micromamba / pyenv（which 全部 not found；~/miniconda3、~/anaconda3 不存在），无法构造真实命名环境；按规范记未执行。既有 test_user_environments.py 17 passed，但它用假的解释器文件与目录布局测发现顺序与去重、用 monkeypatch 的体检测门的采用，不证明真实 conda 环境可用——只作部分覆盖证据，不作本条通过依据。",
        "entry": "pytest（仅既有用例）",
        "sections": [
            ("which conda mamba micromamba pyenv + ls 常见安装根", "which_conda.log"),
            ("pytest test_user_environments.py", "test_user_environments.log"),
        ],
        "commands": [
            "which conda mamba micromamba pyenv; ls -d ~/miniconda3 ~/anaconda3 ~/miniforge3 ~/.pyenv",
            PYTEST.format(t="tests/test_user_environments.py"),
        ],
        "artifacts_from": None,
        "first_failed_invariant": None,
        "mutation_check": None,
        "mutation_check_reason": "未执行（无真实环境）。",
        "new_tests": [],
        "product_bugs": [],
        "gaps": ["真实 conda 命名环境 / base 区分、pyenv 版本、Windows 注册表 Python 均未测。"],
    },
}


def scrub(text: str) -> str:
    return HOME_RE.sub("~", text)


def truncate(lines: list[str]) -> list[str]:
    if len(lines) <= 190:
        return lines
    return (
        lines[:40]
        + [f"... [截断：原始 {len(lines)} 行，保留头 40 行 + 尾 150 行] ..."]
        + lines[-150:]
    )


def summary_of(text: str) -> dict:
    out = {}
    m = re.findall(r"^=+ (.*(?:passed|failed|error|skipped).*) in [\d.]+s.*=+$", text, re.M)
    if m:
        line = m[-1]
        for key in ("passed", "failed", "skipped", "error", "errors"):
            mm = re.search(rf"(\d+) {key}\b", line)
            if mm:
                out[key] = int(mm.group(1))
    r = re.findall(r"RESULT (\S+): (PASS|FAIL) \((\d+)/(\d+) checks\)", text)
    if r:
        _, verdict, ok, total = r[-1]
        out.update({"probe": verdict, "checks_passed": int(ok), "checks_total": int(total)})
    for word in ("CONTRACT_OK", "DEVIATION"):
        if word in text:
            out["repro"] = word
    codes = re.findall(r"# exit_code: (\d+)", text)
    if codes:
        out["exit_code"] = int(codes[-1])
    return out


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", required=True)
    ap.add_argument("--e1", required=True)
    ap.add_argument("--final", required=True)
    args = ap.parse_args()
    raw, e1, final = Path(args.raw), Path(args.e1), Path(args.final)
    logs = SECTION / "logs"
    logs.mkdir(exist_ok=True)
    arts = SECTION / "artifacts"
    e1rec = json.loads((e1 / "e1.json").read_text(encoding="utf-8"))
    fixtures = [
        {"path": f"<scratch>/e1/{rel}", "sha256": digest} for rel, digest in e1rec["sha256"].items()
    ] + [{"path": "docs/qa/2026-09-24/env/repro/make_e1.py", "sha256": sha256(HERE / "make_e1.py")}]
    ledger = []
    for case_id, c in CASES.items():
        body = []
        exit_codes = []
        results = []
        for name, fname in c["sections"]:
            path = raw / fname
            if not path.is_file():
                body += [f"===== {name}（{fname}）: 原始日志缺失 ====="]
                continue
            text = scrub(path.read_text(encoding="utf-8", errors="replace"))
            s = summary_of(text)
            results.append({"section": name, "raw_log": fname, **s})
            if "exit_code" in s:
                exit_codes.append({"section": name, "exit_code": s["exit_code"]})
            body += [f"===== {name}（原始日志 {fname}）=====", *truncate(text.splitlines()), ""]
        head = [
            f"# case: {case_id} {c['title']}",
            f"# source_sha: {SHA}",
            "# 各节开头是逐字命令与时间；<worktree> 是仓库根，<scratch> 是 QA scratch 目录",
            "",
        ]
        log_path = logs / f"{case_id}.log"
        log_path.write_text("\n".join(head + body) + "\n", encoding="utf-8")
        artifacts = []
        if c.get("artifacts_from"):
            src = final / c["artifacts_from"] / "export.pdf"
            if src.is_file():
                arts.mkdir(exist_ok=True)
                dst = arts / f"{case_id}-export.pdf"
                shutil.copyfile(src, dst)
                artifacts.append(
                    {
                        "path": str(dst.relative_to(SECTION.parents[3])),
                        "sha256": sha256(dst),
                        "what": "RenderCore 导出的 PDF（独立读取器读文字层）",
                    }
                )
        mut = c.get("mutation_check")
        entry = {
            "case_id": case_id,
            "title": c["title"],
            "coverage_status": c["coverage_status"],
            "existing_tests": c["existing_tests"],
            "expected_outcome": c["expected_outcome"],
            "product_outcome": c["product_outcome"],
            "test_verdict": c["test_verdict"],
            "verdict_reason": c["verdict_reason"],
            "source_sha": SHA,
            "environment": ENVIRONMENT,
            "fixtures": fixtures if case_id != "ENV-08" else [],
            "entry": c["entry"],
            "commands": c["commands"],
            "exit_codes": exit_codes,
            "evidence": {
                "log": {
                    "path": str(log_path.relative_to(SECTION.parents[3])),
                    "sha256": sha256(log_path),
                },
                "results": results,
            },
            "artifacts": artifacts,
            "first_failed_invariant": c["first_failed_invariant"],
            "mutation_check": mut if mut else None,
            "new_tests": c["new_tests"],
            "product_bugs": c["product_bugs"],
            "gaps": c["gaps"],
        }
        if not mut:
            entry["mutation_check_reason"] = c.get("mutation_check_reason")
        ledger.append(entry)
    (SECTION / "ledger.json").write_text(
        json.dumps(ledger, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    for e in ledger:
        print(
            e["case_id"],
            e["test_verdict"],
            [
                (r["section"][:30], {k: v for k, v in r.items() if k not in ("section", "raw_log")})
                for r in e["evidence"]["results"]
            ],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
