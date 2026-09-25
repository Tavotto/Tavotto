"""生成 docs/qa/2026-09-24/path/ledger.json：逐用例台账（期望在执行前写定，结果取自 logs/ 的真实输出）。

    python docs/qa/2026-09-24/path/repro/build_ledger.py

日志与夹具的 sha256、pytest 计数都在这里现算（读 logs/*.log 的汇总行与 exit_code 行），不手抄。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
QA = ROOT / "docs" / "qa" / "2026-09-24" / "path"
LOGS = QA / "logs"
BASE_SHA = "62af729c2f84652bf3979c25b0c3117fcfa756a5"
T = "tests/test_first_open_paths_d1.py"
RUN = "bash docs/qa/2026-09-24/path/repro/qa_pytest.sh"
REPRO = "bash docs/qa/2026-09-24/path/repro/qa_repro.sh"
MUT = "/Volumes/Projects/Tavotto/.venv/bin/python docs/qa/2026-09-24/path/repro/mutation_check.py"

ENV = {
    "os": "macOS 27.0 (26A428)",
    "arch": "arm64",
    "app_python": "3.13.11（/Volumes/Projects/Tavotto/.venv，Flask 侧，无 matplotlib；PYTHONPATH=<worktree>/src）",
    "worker_python": "3.13.11（/opt/homebrew/opt/python@3.13，产品自选；matplotlib 3.10.8 / numpy 2.4.3 / pandas 3.0.1）",
    "project_venv_for_PATH-06_readers": "3.13.11 uv venv（离线缓存）：matplotlib 3.11.2 / numpy 2.5.3 / pandas 3.0.6 / h5py 3.16.0 / HDF5 2.0.0",
    "node": "未使用（本节无前端 / 浏览器入口）",
    "browser": "未使用",
    "render_backend": "rendercore（默认）",
}


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def log_entry(name: str) -> dict:
    p = LOGS / f"{name}.log"
    text = p.read_text(encoding="utf-8")
    m = re.findall(r"^=+ (.*?) in [\d.]+s", text, re.M)
    rc = re.findall(r"^# exit_code: (\d+)", text, re.M)
    counts = {}
    if m:
        for part in m[-1].split(","):
            n, _, what = part.strip().partition(" ")
            if n.isdigit():
                counts[what] = int(n)
    return {
        "path": str(p.relative_to(ROOT)),
        "sha256": sha(p),
        "summary": counts or None,
        "exit_code": int(rc[-1]) if rc else None,
    }


FIXTURES = [
    {
        "path": T,
        "sha256": sha(ROOT / T),
        "note": "D1 夹具由用例在 tmp 里现造（_d1 / _csv / ENTRY / LABMOD）",
    },
    {
        "path": "docs/qa/2026-09-24/path/repro/make_d1.py",
        "sha256": sha(QA / "repro" / "make_d1.py"),
        "note": "同形状的独立生成器",
    },
    {
        "path": "D1 data/points.csv（x=0..3, y=[0,9,2,10]，运行时生成）",
        "sha256": hashlib.sha256(b"x,y\n0,0\n1,9\n2,2\n3,10\n").hexdigest(),
    },
    {
        "path": "D1 干扰 scripts/data/points.csv（y=[0,2,9,10]，运行时生成）",
        "sha256": hashlib.sha256(b"x,y\n0,0\n1,2\n2,9\n3,10\n").hexdigest(),
    },
    {
        "path": "D1 项目外 abs_points.csv（y=[1,8,3,7]，运行时生成）",
        "sha256": hashlib.sha256(b"x,y\n0,1\n1,8\n2,3\n3,7\n").hexdigest(),
    },
]


def mut(mid: str, target: str, mutation: str, case: str) -> dict:
    red = log_entry(f"MUT-{mid}-mutated")
    green = log_entry(f"MUT-{mid}-restored")
    return {
        "id": mid,
        "target_file": target,
        "mutation": mutation,
        "expected_red_case": case,
        "observed": "red" if red["exit_code"] not in (0, None) else "green",
        "restored_green": green["exit_code"] == 0,
        "command": f"{MUT} {mid}",
        "logs": [red, green],
    }


BUGS = {
    "prepare_ready": {
        "id": "QA-PATH-B1",
        "summary": "准备接口对一张图都没捕获到的面板给 ready（error=None、receipt.descriptors=[]），只有渲染入口报 no_figures_captured",
        "repro": f"{REPRO} PATH-06-bug-prepare-ready-without-figure docs/qa/2026-09-24/path/repro/repro_path06_prepare_ready_without_figure.py",
        "severity": "中（接口层：当前 web / MCP 未消费准备接口，但 FirstOpenBench 与 ADR 0053 把 ready 当执行成功的终局；FO-054 要求 ready_editable 有实际图）",
        "evidence": "docs/qa/2026-09-24/path/logs/PATH-06-bug-prepare-ready-without-figure.log",
        "code_pointer": "src/tavotto/engine/preparation.py 执行线程末尾 `self._finish(entry, STATUS_READY)`：只看 build 没抛异常，不核请求的 stem 是否在 descriptors 里",
    },
    "observer_venv": {
        "id": "QA-PATH-B2",
        "summary": "项目自带 .venv 在项目根内（ADR 0057 首开第 4 条的标准形态）时，回执 inputs.files 把 .venv/site-packages 下的库文件（.so / 字体 / mplstyle）当成数据输入，预算 256 被占满（truncated=true）",
        "repro": f"{REPRO} PATH-06-bug-observer-venv-pollution docs/qa/2026-09-24/path/repro/repro_path06_observer_venv_pollution.py",
        "severity": "中（数据身份证据被稀释；预算耗尽后才读的数据文件会从回执里消失，依赖回执 inputs 的「执行后数据变更」核对随之失明）",
        "evidence": "docs/qa/2026-09-24/path/logs/PATH-06-bug-observer-venv-pollution.log；PATH-06-readers.log 里每个脚本 inputs_venv_count 41–67",
        "code_pointer": "src/tavotto/engine/figcapture.py InputObserver._note：只按「在项目根内」过滤，不排除解释器 prefix / site-packages",
    },
    "gate_live_worker": {
        "id": "QA-PATH-B3",
        "summary": "失败 build 留下的会话在静态证据变化后仍被渲染入口复用：准备接口回 needs_input，渲染入口却在旧沙盒会话里重跑脚本、报 script_error（作废后才回到同一道门）",
        "repro": f"{REPRO} PATH-07-bug-gate-vs-live-worker docs/qa/2026-09-24/path/repro/repro_path07_gate_vs_live_worker.py",
        "severity": "低（不会读错数据：沙盒回退够不到项目根的同名文件；但四类入口对同一时刻给出两个不同答案，违背 ADR 0057 §三「门只有一处、同一个 code」的表述）",
        "evidence": "docs/qa/2026-09-24/path/logs/PATH-07-bug-gate-vs-live-worker.log",
        "code_pointer": "src/tavotto/engine/pool.py：workdir.resolve_mode 只在 _new_worker() 里调；已存在但 build 失败的会话 override → ensure_built 直接重跑",
    },
}

CASES = [
    {
        "case_id": "PATH-01",
        "title": "同目录：脚本与 CSV 同目录，从与脚本不同的 cwd 发起",
        "coverage_status": "已有完整断言",
        "existing_tests": [
            "tests/test_foundation_first_open.py::test_fo01_same_directory_csv_opens_automatically（只量 ylim ± 5%，同目录，服务 cwd=work；无重放 / 导出）",
            "tests/test_databinding.py::test_default_ok_when_the_script_directory_has_the_data（静态判决 default_ok）",
        ],
        "expected_outcome": "自动成功：不问（default_ok）、cwd_origin=sandbox 经只读回退读到脚本旁那份；首开、重放（新一代 worker）与导出的全点序列 = [0,9,2,10]，回执输入 hash = 真值；harness 不 chdir",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "新用例三把尺子（manifest 折线换算的全序列 / 回执 inputs sha256 / 导出 PDF 内容流 4 点折线的归一化序列）在首开、invalidate 后新一代与导出全部 = 真值；generation 递增证明真的重放。基线时 FO01 只量 ylim（部分覆盖），本次补齐。无意外 skip。",
        "entry": "HTTP（python -m tavotto + 会话认证，真实 worker）",
        "logs": ["PATH-01"],
        "commands": [
            f"{RUN} PATH-01 {T}::test_path01_same_directory_csv_from_a_foreign_cwd_first_open_replay_and_export {T}::test_the_decoy_is_indistinguishable_by_extrema_mean_and_ylim tests/test_foundation_first_open.py::test_fo01_same_directory_csv_opens_automatically tests/test_databinding.py::test_default_ok_when_the_script_directory_has_the_data -vv"
        ],
        "artifacts": [
            {
                "path": "<tmp>/paper/tavottofile/export/path01.pdf",
                "note": "每次运行现生成，折线归一化序列 [0,0.9,0.2,1.0] 由用例断言",
            }
        ],
        "first_failed_invariant": None,
        "mutation": [
            "M3-fallback-off",
            "src/tavotto/engine/figcapture.py",
            "_fallback_path 的 `if os.path.exists(name):` → `if True:`（只读回退整体失效）",
            f"{T}::test_path01_…（status == ready）",
        ],
        "new_tests": [
            f"{T}::test_path01_same_directory_csv_from_a_foreign_cwd_first_open_replay_and_export",
            f"{T}::test_the_decoy_is_indistinguishable_by_extrema_mean_and_ylim",
        ],
        "bugs": [],
        "gaps": [
            "服务进程 cwd ≠ 脚本目录由 foundation_app 结构保证（cwd=work），未另外读进程 cwd 断言",
            "桌面壳入口未测（本节只走 HTTP）",
        ],
    },
    {
        "case_id": "PATH-02",
        "title": "分离目录：scripts/ 与 data/ 分离、原始 cwd 为项目根",
        "coverage_status": "已有完整断言",
        "existing_tests": [
            "tests/test_foundation_first_open.py::test_fo02_scripts_and_data_split_asks_once_then_runs_at_the_project_root（问一次、推荐项目根、选前渲染被拦、选后 cwd_origin=project.root、二开不问；只量 ylim）",
            "tests/test_first_open_workdir.py（17 条：决定 / 门 / 第三档 / 真 worker 在中文空格项目根运行）",
            "tests/test_databinding.py::test_project_root_when_only_the_root_has_the_data",
        ],
        "expected_outcome": "引导后成功：首次 needs_input(project_root_evidence, recommended=project_root)，选择前渲染回 workdir_confirmation_required；PATCH project_root 后 cwd_origin=project.root（不是 script.parent），全序列 / 输入 hash = 真值；关服务重开同一数据目录后不再问、带一次编辑导出仍 = 真值；脚本字节不变",
        "product_outcome": "引导后成功",
        "test_verdict": "pass",
        "verdict_reason": "新用例覆盖了 FO02 缺的维度：全序列、本地模块 labmod 被观察到、服务重启后的决定持久化、重开后编辑 + 导出折线。§9 反证 M1（把项目根当脚本目录）使该用例在 `status == ready` 处红（script_error: data/points.csv 不存在），还原后回绿。",
        "entry": "HTTP（两次独立服务进程，同一 TAVOTTO_DATA_DIR / CONFIG_DIR）",
        "logs": ["PATH-02"],
        "commands": [
            f"{RUN} PATH-02 {T}::test_path02_split_directories_ask_once_remember_project_root_and_survive_a_restart tests/test_foundation_first_open.py::test_fo02_scripts_and_data_split_asks_once_then_runs_at_the_project_root tests/test_first_open_workdir.py tests/test_databinding.py::test_project_root_when_only_the_root_has_the_data -vv"
        ],
        "artifacts": [
            {
                "path": "<tmp>/paper/tavottofile/export/path02.pdf",
                "note": "重开后带标题编辑导出，折线 = 真值",
            }
        ],
        "first_failed_invariant": None,
        "mutation": [
            "M1-root-as-scriptdir",
            "src/tavotto/engine/execspec.py",
            "safe_spec 里 CWD_PROJECT_ROOT 分支的 cwd 换成脚本所在目录",
            f"{T}::test_path02_…",
        ],
        "new_tests": [
            f"{T}::test_path02_split_directories_ask_once_remember_project_root_and_survive_a_restart"
        ],
        "bugs": [],
        "gaps": [
            "「编辑重开」用的是服务进程重启 + 同一数据目录，不含 .tavotto 文档保存 / 桌面壳完全退出",
            "界面确认框本身（web WorkdirRow）不在本节",
        ],
    },
    {
        "case_id": "PATH-03",
        "title": "__file__ / 项目外有效绝对路径 / 旧机器失效的硬编码绝对路径",
        "coverage_status": "已有完整断言",
        "existing_tests": [
            "tests/test_foundation_first_open.py::test_fo03_file_relative_data_and_local_package_open_automatically（__file__ + 本地包；ylim）",
            "tests/test_databinding.py::test_absolute_paths_are_never_touched / test_a_literal_that_escapes_the_project_root_is_never_touched",
            "tests/test_compat_capture_parity.py::TestAbsolutizedRelativeRead（6 条：沙盒外绝对路径永不改道等，进程内 figcapture 单元）",
        ],
        "expected_outcome": "__file__ 指真实脚本（标题 = scripts/file_rel.py）且全序列 = 真值；项目外绝对路径原样读取（全序列 = [1,8,3,7]），不拷进项目 / 数据目录；旧机器绝对路径（basename 与项目内 data/points.csv 相同）明确失败（error、receipt None、渲染被拒），不按同名映射",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "三种形态均按期望：__file__ 与项目外绝对路径自动成功；失效绝对路径为安全停止（script_error，FileNotFoundError 原文），项目里同名文件未被顶替。安全停止部分不计入自动成功。反证 M6（沙盒外绝对路径按 basename 改道到 ../data/）使失效路径那一步变成 ready → 红。",
        "entry": "HTTP",
        "logs": ["PATH-03"],
        "commands": [
            f'{RUN} PATH-03 {T}::test_path03_file_relative_and_absolute_outside_are_kept_and_a_stale_absolute_path_fails tests/test_foundation_first_open.py::test_fo03_file_relative_data_and_local_package_open_automatically tests/test_databinding.py::test_absolute_paths_are_never_touched tests/test_databinding.py::test_a_literal_that_escapes_the_project_root_is_never_touched tests/test_compat_capture_parity.py -k "path03 or fo03 or absolute or escapes or abs" -vv'
        ],
        "artifacts": [],
        "first_failed_invariant": None,
        "mutation": [
            "M6-absolute-remapped",
            "src/tavotto/engine/figcapture.py",
            "_fallback_path：沙盒外绝对路径 rel=None 时改成 ../data/<basename>",
            f"{T}::test_path03_…（stale 那步 status == error）",
        ],
        "new_tests": [
            f"{T}::test_path03_file_relative_and_absolute_outside_are_kept_and_a_stale_absolute_path_fails"
        ],
        "bugs": [],
        "gaps": [
            "项目外绝对路径的输入不进回执 inputs（观察只记项目内，如实 partial）——数据身份靠不了回执",
            "失效绝对路径没有「重新定位」引导（ADR 0057：任意绝对路径的重新定位归 X01，未实现）",
        ],
    },
    {
        "case_id": "PATH-04",
        "title": "同名干扰：脚本目录与项目根各一份同名、不同点序列、极值均值相同",
        "coverage_status": "已有完整断言",
        "existing_tests": [
            "tests/test_foundation_first_open.py::test_fo07_same_name_data_in_two_places_asks_and_honours_the_choice（干扰值 [601,1201,2401] 与正确值极值不同——ylim 能分；D1 的 [0,2,9,10] 它分不出）",
            "tests/test_databinding.py 四条歧义 / 不歧义 / 各一半 / 不搜同名",
            "tests/test_foundation_join.py::test_fo32_wrong_choice_reads_the_decoy_and_the_identities_say_so（本机 skip：TAVOTTO_FOUNDATION_PROJECT_PYTHON 未设，not_run）",
        ],
        "expected_outcome": "引导后成功：needs_input(ambiguous_data)、recommended=None、没有任何选项被预选、conflicts=[data/points.csv]、选择前渲染被拦且未执行；选项目根 → 全序列 / 输入 hash / 导出折线 = 正确那份；改选脚本目录 → 全部 = 干扰那份",
        "product_outcome": "引导后成功",
        "test_verdict": "pass",
        "verdict_reason": "前提用例证明 D1 两份数据极值 / 均值 / 排序后序列相同（ylim 判不出）；新用例用全序列先量，再核 hash 与导出。反证 M1（项目根当脚本目录）→ 在全序列断言处红（读到 [0,2,9,10]）；M2（歧义不再问）→ 在 needs_input 断言处红。两条都还原回绿。FO32 的 h5py 联合实例本机 skip（not_run，已单列）。",
        "entry": "HTTP",
        "logs": ["PATH-04"],
        "commands": [
            f"{RUN} PATH-04 {T}::test_path04_same_name_decoy_asks_without_preselection_and_reads_only_the_chosen_file {T}::test_the_decoy_is_indistinguishable_by_extrema_mean_and_ylim tests/test_foundation_first_open.py::test_fo07_same_name_data_in_two_places_asks_and_honours_the_choice tests/test_databinding.py::test_ambiguous_when_both_places_have_a_same_name_file_with_different_bytes tests/test_databinding.py::test_identical_bytes_in_both_places_is_not_ambiguous tests/test_databinding.py::test_ambiguous_when_the_two_places_hold_different_halves_of_the_reads tests/test_databinding.py::test_unknown_when_nothing_is_found_anywhere_and_no_same_name_search_happens tests/test_foundation_join.py::test_fo32_wrong_choice_reads_the_decoy_and_the_identities_say_so -vv"
        ],
        "artifacts": [
            {
                "path": "<tmp>/paper/tavottofile/export/path04.pdf",
                "note": "折线归一化 [0,0.9,0.2,1.0]",
            }
        ],
        "first_failed_invariant": None,
        "mutation": [
            "M1-root-as-scriptdir|M2-ambiguity-suppressed",
            "src/tavotto/engine/execspec.py | src/tavotto/engine/databinding.py",
            "M1：project_root 的 cwd 换成脚本目录；M2：`elif conflicts:` 的判决 ambiguous → default_ok",
            f"{T}::test_path04_…",
        ],
        "new_tests": [
            f"{T}::test_path04_same_name_decoy_asks_without_preselection_and_reads_only_the_chosen_file"
        ],
        "bugs": [],
        "gaps": [
            "FO32（h5py + 项目 venv 另一 minor）本机 not_run：uv 离线缓存里没有 cp312 的 h5py",
            "界面上「选项不预选」的呈现不在本节（只核结构化载荷）",
        ],
    },
    {
        "case_id": "PATH-05",
        "title": "参数 / 环境变量 / 特殊路径（中文、空格、引号、长路径）",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_first_open_workdir.py::test_project_root_mode_really_runs_at_the_project_root_with_cjk_and_spaces（中文 + 空格，进程内 pool.build；ylim）",
            "tests/native/test_run_cli_integration.py::test_the_script_keeps_the_users_stdout_cwd_env_and_argv（tavotto run 档：argv / env / cwd 原样）",
        ],
        "expected_outcome": "中文 / 空格 / 单双引号 / 总长 > 260 的项目与数据文件名：首开自动成功、全序列 = 真值、导出一致；服务环境里的密钥不出现在准备 / 渲染 / 环境状态的公开响应里；safe 档没有传参入口 → 要参数的脚本明确 script_needs_arguments（安全停止）；环境变量指定的文件：记录行为",
        "product_outcome": "自动成功",
        "test_verdict": "partial",
        "verdict_reason": "macOS 上特殊路径全部自动成功（含导出）；密钥未出现在 4 份公开响应里；需参数脚本 = script_needs_arguments（明确不支持，安全停止，不计自动成功）；tavotto run 档 argv / env 原样由既有用例证明；环境变量指定数据：safe worker 继承启动进程环境，首开与重放一致（repro 观测，未入测试集）。partial 的原因：Windows MAX_PATH / 盘符大小写、safe 档传参与环境变量的导出一致性未测。",
        "entry": "HTTP + CLI（tavotto run）",
        "logs": ["PATH-05", "PATH-05-env-var"],
        "commands": [
            f'{RUN} PATH-05 {T}::test_path05_cjk_space_quotes_long_path_and_no_env_secret_in_public_responses tests/test_first_open_workdir.py::test_project_root_mode_really_runs_at_the_project_root_with_cjk_and_spaces tests/native/test_run_cli_integration.py::test_the_script_keeps_the_users_stdout_cwd_env_and_argv tests/test_workdir_mode.py -k "path05 or cjk or argv or golden or payload" -vv',
            f"{REPRO} PATH-05-env-var docs/qa/2026-09-24/path/repro/repro_path05_env_var.py",
        ],
        "artifacts": [
            {
                "path": "<tmp>/…/paper project/tavottofile/export/path05 导出.pdf",
                "note": "折线 = 真值",
            }
        ],
        "first_failed_invariant": None,
        "mutation": None,
        "mutation_reason": "未做：特殊路径用例的核心断言是全序列 / 输入 hash（已由 M1/M3 在同形状用例上证明是活的）；密钥断言是否定式，无对应的单点产品变异点（公开投影没有一处把环境变量写进响应的代码可翻转）",
        "new_tests": [
            f"{T}::test_path05_cjk_space_quotes_long_path_and_no_env_secret_in_public_responses"
        ],
        "bugs": [],
        "gaps": [
            "Windows：> 260 字符路径、cp936、盘符大小写未执行（本机 macOS）",
            "safe 档没有「给脚本传参」的入口（设计如此：argv 恒空）；需要参数的脚本只能走 tavotto run",
            "密钥检查只覆盖 HTTP 公开响应，未查 worker.log / 诊断包（诊断包归 REL-05）",
            "环境变量指定文件未做导出 / 写回一致性",
        ],
    },
    {
        "case_id": "PATH-06",
        "title": "先探测后读取：exists/glob/listdir + pandas.read_csv / numpy.loadtxt / h5py",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_workdir_mode.py::test_sandbox_mode_still_hides_relative_data_from_exists_and_glob / test_project_mode_runs_the_script_where_it_lives（真 worker；exists/glob/listdir 在沙盒为假、项目模式为真）",
            "tests/test_zero_capture.py（3 条：零捕获 → no_figures_captured / silent）",
            "tests/test_compat_capture_parity.py::TestRelativeFileIO 的 numpy DataSource 四条（进程内）",
        ],
        "expected_outcome": "默认沙盒：numpy.loadtxt / pandas.read_csv 直读经回退自动成功（全序列 = 真值）；先 exists 再读 → 盲区，按合同是引导型安全停止（no_figures_captured + 脚本输出），不得计自动成功；h5py 原生打开在沙盒读不到 → 明确错误；授予「脚本目录」后三个探测一致、三种读取器全序列 = 真值",
        "product_outcome": "安全停止",
        "test_verdict": "partial",
        "verdict_reason": "沙盒：pandas / numpy 直读自动成功；exists 先判的脚本渲染入口报 no_figures_captured（带 `probe exists=False glob=0 listdir=False`）；h5py 在沙盒 script_error（HDF5 原生 open 不经回退，且静态判 default_ok 不问——无「改在脚本目录运行」入口）。脚本目录模式下三种读取器 + 三种探测一致、全序列 = 真值（引导后成功）。partial：发现两处产品缺陷——① 准备接口对零捕获给 ready（QA-PATH-B1）；② 项目内 .venv 的库文件污染回执 inputs 并触发 truncated（QA-PATH-B2）；HDF5 部分只在 repro 里跑（依赖 uv 离线缓存，不进测试集）。",
        "entry": "HTTP",
        "logs": [
            "PATH-06",
            "PATH-06-readers",
            "PATH-06-bug-prepare-ready-without-figure",
            "PATH-06-bug-observer-venv-pollution",
        ],
        "commands": [
            f'{RUN} PATH-06 {T}::test_path06_probe_then_read_is_a_guided_stop_in_the_sandbox_and_correct_in_project_mode tests/test_workdir_mode.py::test_sandbox_mode_still_hides_relative_data_from_exists_and_glob tests/test_workdir_mode.py::test_project_mode_runs_the_script_where_it_lives tests/test_zero_capture.py tests/test_compat_capture_parity.py -k "path06 or sandbox_mode or project_mode_runs or numpy or zero or no_figures or empty" -vv',
            f"{REPRO} PATH-06-readers docs/qa/2026-09-24/path/repro/repro_path06_readers.py",
            f"{REPRO} PATH-06-bug-prepare-ready-without-figure docs/qa/2026-09-24/path/repro/repro_path06_prepare_ready_without_figure.py",
            f"{REPRO} PATH-06-bug-observer-venv-pollution docs/qa/2026-09-24/path/repro/repro_path06_observer_venv_pollution.py",
        ],
        "artifacts": [],
        "first_failed_invariant": "准备接口终局：对未捕获到图的面板 status 应 ≠ ready（实得 ready）",
        "mutation": [
            "M3-fallback-off",
            "src/tavotto/engine/figcapture.py",
            "只读回退整体失效",
            f"{T}::test_path06_…（np_only 那步 status == ready）",
        ],
        "new_tests": [
            f"{T}::test_path06_probe_then_read_is_a_guided_stop_in_the_sandbox_and_correct_in_project_mode"
        ],
        "bugs": ["prepare_ready", "observer_venv"],
        "gaps": [
            "默认沙盒的 exists / glob / listdir / 原生读取器（h5py、C 扩展）是合同里的已知盲区：h5py 那条只有 script_error，没有指向「在脚本目录里运行」的引导",
            "HDF5 用例依赖本机 uv 缓存，未进测试集",
            "h5py 读取不进回执 inputs（native_io 未观察，如实 partial）",
        ],
    },
    {
        "case_id": "PATH-07",
        "title": "链接 / 跨盘 / 网络路径",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_databinding.py::test_a_symlink_inside_the_project_pointing_outside_is_outside（静态证据）",
            "tests/test_compat_capture_parity.py::TestRelativeFileIO::test_reads_outside_the_project_are_not_redirected（进程内回退）",
            "tests/test_input_observer_paths.py（9 条：Windows 形态的 within / 跨盘 exclude_dir，纯函数）",
        ],
        "expected_outcome": "沙盒：项目内→项目内的软链接经回退读到目标（全序列 = 真值）；逃出项目的软链接不被回退跟出去（明确失败、不发布、不改读项目根同名文件）；项目根出现同名文件时问一次；用户授予脚本目录后行为 = 终端（读外部那份）。Windows 跨盘 / 网盘暂断：未执行",
        "product_outcome": "安全停止",
        "test_verdict": "partial",
        "verdict_reason": "POSIX symlink 部分全部按期望（M4 反证：回退不核 realpath → 逃出的软链接被读成功 → 红，还原回绿）。但：① 越界拒绝的报错是泛化的 script_error「No such file or directory: 'link_out.csv'」，没说明是「越出项目被拒」、也没有授权入口，「清楚拒绝」只算部分满足；② 附带发现 QA-PATH-B3（失败会话绕过新证据的门）。Windows 跨盘、网盘暂断未执行（非 Windows 机器、无网盘）。",
        "entry": "HTTP",
        "logs": ["PATH-07", "PATH-07-bug-gate-vs-live-worker"],
        "commands": [
            f"{RUN} PATH-07 {T}::test_path07_symlinks_inside_are_read_and_escaping_ones_are_not_followed_by_the_fallback tests/test_databinding.py::test_a_symlink_inside_the_project_pointing_outside_is_outside tests/test_compat_capture_parity.py::TestRelativeFileIO::test_reads_outside_the_project_are_not_redirected tests/test_input_observer_paths.py -vv",
            f"{REPRO} PATH-07-bug-gate-vs-live-worker docs/qa/2026-09-24/path/repro/repro_path07_gate_vs_live_worker.py",
        ],
        "artifacts": [],
        "first_failed_invariant": "证据变化后渲染入口应回 workdir_confirmation_required（实得 script_error，复用旧会话）",
        "mutation": [
            "M4-fallback-follows-escaping-symlink",
            "src/tavotto/engine/figcapture.py",
            "_fallback_path 的 `commonpath([real, root]) != root` 判据改成 `False`",
            f"{T}::test_path07_…（link_out 那步 status == error）",
        ],
        "new_tests": [
            f"{T}::test_path07_symlinks_inside_are_read_and_escaping_ones_are_not_followed_by_the_fallback"
        ],
        "bugs": ["gate_live_worker"],
        "gaps": [
            "Windows 跨盘符（C:/D:）、UNC / 网盘暂断：未执行（需要 Windows 腿）",
            "逃出项目的软链接：确认载荷的 project 选项 found=[]，不展示 outside 候选——用户看不到「脚本目录里其实有一条指向外部的链接」",
            "越界拒绝的错误文案不区分「真的不存在」与「越界被拒」",
        ],
    },
    {
        "case_id": "PATH-08",
        "title": "数据丢失与移动：删除 / 撤权限 / 卸载外部盘后重开、重放、导出",
        "coverage_status": "部分覆盖",
        "existing_tests": [
            "tests/test_foundation_join.py::test_fo30_data_changed_after_execution_is_an_explicit_snapshot_until_the_user_rebuilds（改内容：旧快照明示 → 重建读新值；真实入口）",
            "tests/test_preparation_api.py::test_data_changed_after_the_plan_makes_it_stale_before_any_script_runs / test_data_changed_between_spawn_and_read_is_caught_by_the_observed_inputs（fake pool）",
        ],
        "expected_outcome": "删掉正确那份后重建：明确失败（script_error 点名 data/points.csv）、不发布、带编辑的导出失败、不改读脚本目录里的同名干扰；不带编辑的导出按 FO-065 交出磁盘原件（折线仍是原件的真值）；chmod 000 同样明确失败；外部盘卸载 → 明确失败，重新挂载 → 真值；恢复后回到真值",
        "product_outcome": "安全停止",
        "test_verdict": "partial",
        "verdict_reason": "删除 / 撤权限 / 真实卸载（hdiutil 挂载的磁盘映像）三种都明确失败、从未读到干扰；带编辑导出 export_failed 且 outputs=[]；静态导出交出原件（原绑定保留）；恢复 / 重新挂载后回到真值。partial：未做「保存 .tavotto 文档 → 完全退出 → 重开」这一段（本用例用的是同一服务进程里 invalidate 重建），也未做项目整体移动。",
        "entry": "HTTP",
        "logs": ["PATH-08", "PATH-08-unmount"],
        "commands": [
            f"{RUN} PATH-08 {T}::test_path08_deleted_or_unreadable_data_fails_explicitly_and_never_falls_back_to_the_decoy tests/test_foundation_join.py::test_fo30_data_changed_after_execution_is_an_explicit_snapshot_until_the_user_rebuilds tests/test_preparation_api.py::test_data_changed_after_the_plan_makes_it_stale_before_any_script_runs tests/test_preparation_api.py::test_data_changed_between_spawn_and_read_is_caught_by_the_observed_inputs -vv",
            f"{REPRO} PATH-08-unmount docs/qa/2026-09-24/path/repro/repro_path08_unmount.py",
        ],
        "artifacts": [
            {
                "path": "<tmp>/paper/tavottofile/export/path08-static.pdf",
                "note": "数据删除后的静态导出：折线 = 原件真值",
            }
        ],
        "first_failed_invariant": None,
        "mutation": None,
        "mutation_reason": "未做：在 project_root 模式下没有可让产品「改读同名干扰」的单点——回退只在沙盒模式装；同名干扰被读入的反证已由 PATH-04 的 M1 覆盖",
        "new_tests": [
            f"{T}::test_path08_deleted_or_unreadable_data_fails_explicitly_and_never_falls_back_to_the_decoy"
        ],
        "bugs": [],
        "gaps": [
            "保存文档 → 完全退出 app / worker → 重开 → 导出 这一段未执行",
            "项目整体移动（路径变化后项目设置里的 workdir 决定是否跟随）未执行",
            "静态导出成功而数据已丢：导出 manifest 未显式标注「来自磁盘原件、未重跑」（checks 里 text_layer / fonts 为 unknown），用户可能不知道这不是当次执行",
        ],
    },
    {
        "case_id": "PATH-09",
        "title": "输入与写入隔离：读数据又写中间结果，分别授予沙盒 / 脚本目录 / 项目根",
        "coverage_status": "已有完整断言",
        "existing_tests": [
            "tests/test_workdir_mode.py（7 条：project 模式相对写落项目、守卫拦删除 / 改名、沙盒空、三条 spawn 路径同源、切换重建会话）",
            "tests/test_preparation_api.py::test_a_plan_whose_grant_changed_before_execution_is_stale_and_runs_nothing（撤销授权 → preparation_plan_stale，runner 0 次；fake pool）",
            "tests/test_first_open_workdir.py::test_project_root_mode_really_runs_at_the_project_root_with_cjk_and_spaces（project_root 相对写落项目根）",
        ],
        "expected_outcome": "三种模式读取真值都正确；沙盒 → 中间结果只在沙盒（项目里零个）；脚本目录 → scripts/interm_out.txt；项目根 → interm_out.txt；每次换授权都新建会话（不复用旧 cwd），撤销回沙盒后写回到沙盒；旧计划在撤销后不执行",
        "product_outcome": "自动成功",
        "test_verdict": "pass",
        "verdict_reason": "新用例 sandbox → project → project_root → sandbox 四轮：每轮 created_runtime=True、全序列 / 输入 hash = 真值、写入位置与授权逐一对应；沙盒那份确实落在数据目录的沙盒里。旧计划不执行由既有 fake-pool 用例证明（真实入口的竞态窗口难以稳定摆出）。M5（project 模式 cwd 换成项目根）→ 写入位置断言红，还原回绿。",
        "entry": "HTTP + 进程内（fake pool）",
        "logs": ["PATH-09"],
        "commands": [
            f"{RUN} PATH-09 {T}::test_path09_relative_writes_land_only_where_the_current_grant_allows tests/test_workdir_mode.py tests/test_preparation_api.py::test_a_plan_whose_grant_changed_before_execution_is_stale_and_runs_nothing tests/test_preparation_api.py::test_a_decision_for_sandbox_is_remembered_and_never_asked_again tests/test_first_open_workdir.py::test_project_root_mode_really_runs_at_the_project_root_with_cjk_and_spaces -vv"
        ],
        "artifacts": [],
        "first_failed_invariant": None,
        "mutation": [
            "M5-project-mode-at-root",
            "src/tavotto/engine/execspec.py",
            "CWD_PROJECT 分支的 cwd 换成项目根",
            f"{T}::test_path09_…（写入位置）",
        ],
        "new_tests": [f"{T}::test_path09_relative_writes_land_only_where_the_current_grant_allows"],
        "bugs": [],
        "gaps": [
            "「撤销后旧计划不能执行」只在 fake pool 下验证；真实 worker 下只验证了会话被重建",
            "绝对路径写（写到项目外）不受 cwd 模式约束，本节未测（守卫只拦删除 / 改名）",
        ],
    },
]


def main() -> None:
    ledger = []
    for c in CASES:
        m = c.pop("mutation")
        reason = c.pop("mutation_reason", None)
        if m is None:
            mc = None
        else:
            mids = m[0].split("|")
            parts = [mut(mid, m[1], m[2], m[3]) for mid in mids]
            mc = {
                "target_file": m[1],
                "mutation": m[2],
                "expected_red_case": m[3],
                "observed": "red" if all(p["observed"] == "red" for p in parts) else "green",
                "restored_green": all(p["restored_green"] for p in parts),
                "runs": parts,
            }
        logs = [log_entry(n) for n in c.pop("logs")]
        bugs = [BUGS[b] for b in c.pop("bugs")]
        entry = {
            "case_id": c["case_id"],
            "title": c["title"],
            "coverage_status": c["coverage_status"],
            "existing_tests": c["existing_tests"],
            "expected_outcome": c["expected_outcome"],
            "product_outcome": c["product_outcome"],
            "test_verdict": c["test_verdict"],
            "verdict_reason": c["verdict_reason"],
            "source_sha": BASE_SHA,
            "environment": ENV,
            "fixtures": FIXTURES,
            "entry": c["entry"],
            "commands": c["commands"],
            "exit_codes": [lg["exit_code"] for lg in logs],
            "evidence": logs,
            "artifacts": c["artifacts"],
            "first_failed_invariant": c["first_failed_invariant"],
            "mutation_check": mc if mc is not None else {"value": None, "reason": reason},
            "new_tests": c["new_tests"],
            "product_bugs": bugs,
            "gaps": c["gaps"],
        }
        ledger.append(entry)
    (QA / "ledger.json").write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(ledger)} cases")


if __name__ == "__main__":
    main()
