"""更新链 CI 门禁的看护：消费者保真检查 + 真实 N-1 更新验证。

背景（2026-08-25）：Windows 应用内更新从 v0.7.0 起从未成功过一次，而发布链
全绿了四个版本——每条绿灯量的都是生产者侧的替身指标（zip 存在、.sig 存在、
文件名匹配），没有一步以真实消费者（tauri-plugin-updater）的身份消费产物。
根因与产物级修复见 `scripts/make_updater_zip.py` 抬头与
`tests/test_updater_zip.py`；这里看护的是**让它逃逸的 CI** 补上的两层：

* nightly 的 `updater-consumer-fidelity`：对线上已发布产物做验签 + 插件
  同形态解包（`tools/updater-extract-probe`）；
* release.yml 的 `n1_update_windows`：发布后在 Windows runner 上装 N-1
  官方安装包、驱动真实应用内更新（壳的 `TAVOTTO_E2E_RUN_UPDATE` 触发口）。

每条用例都钉「坏掉之后会怎样」：探针的依赖形态漂了 → 假绿；触发口默认
不再关死 → 生产风险；workflow 掉了某条断言 → 门禁空转。
"""

from __future__ import annotations

import re
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
PROBE_DIR = ROOT / "tools" / "updater-extract-probe"
CI_DIR = ROOT / "scripts" / "ci"
sys.path.insert(0, str(CI_DIR))

import updater_consumer_check as UCC  # noqa: E402

# ============================================================ 探针 = 插件能力面


def _zip_dep_line() -> str:
    src = (PROBE_DIR / "Cargo.toml").read_text(encoding="utf-8")
    lines = [ln for ln in src.splitlines() if re.match(r"\s*zip\s*=", ln)]
    assert len(lines) == 1, f"探针 Cargo.toml 里应恰好一条 zip 依赖，找到 {lines}"
    return lines[0]


def test_probe_zip_dependency_mirrors_the_plugin():
    """探针的 zip 依赖必须逐字复刻 tauri-plugin-updater 的形态。

    上游对 Windows 的 zip 依赖是 `default-features = false`——deflate 解压
    feature 被关掉，插件只解得开 STORED。探针一旦被加回任何 feature
    （哪怕只是「修好」nightly 那盏红灯的 deflate），它就解得开插件解不开的
    包——门禁当场变成假绿，v0.7.0–v0.10.0 的坏更新包正是这么漏出去的。
    """
    line = _zip_dep_line()
    assert "default-features = false" in line, (
        "探针的 zip 依赖不再是 default-features = false——量的已经不是插件的能力面"
    )
    assert re.search(r"(?<![\w-])features\s*=", line) is None, (
        f"探针的 zip 依赖被加了 feature：{line.strip()}——插件没有的能力探针也不许有"
    )


def test_probe_zip_version_matches_the_shells_lockfile():
    """探针钉的 zip 版本必须与 src-tauri/Cargo.lock 里插件实际用的一致。

    壳升级把 zip 换代之后，插件的能力面就是新版本的；探针停在旧版本，
    量的又不是真实消费者了。精确版本对齐（不止主版本）——两个文件都在
    仓库里，没有理由容忍漂移。
    """
    line = _zip_dep_line()
    m = re.search(r'version\s*=\s*"=([\d.]+)"', line)
    assert m, f"探针的 zip 版本必须精确钉死（=X.Y.Z）：{line.strip()}"
    probe_ver = m.group(1)

    lock = (ROOT / "src-tauri" / "Cargo.lock").read_text(encoding="utf-8")
    lm = re.search(r'name = "zip"\nversion = "([\d.]+)"', lock)
    assert lm, "src-tauri/Cargo.lock 里找不到 zip——插件的依赖形状变了，探针要重新对齐"
    assert probe_ver == lm.group(1), (
        f"探针钉的 zip {probe_ver} ≠ 壳锁定的 {lm.group(1)}——"
        "升级壳依赖时必须同步 tools/updater-extract-probe/Cargo.toml"
    )


def test_probe_asserts_exactly_one_toplevel_exe():
    """插件解包后只在顶层 read_dir 找第一个 .exe——探针必须断言恰好一个。

    两个 exe 意味着「装哪个」取决于目录序，零个意味着更新器报
    BinaryNotFoundInArchive；只查「解得开」漏掉后一半。
    """
    src = (PROBE_DIR / "src" / "main.rs").read_text(encoding="utf-8")
    assert "read_dir" in src, "探针不再扫顶层目录了"
    assert "exes.len() != 1" in src, "探针不再断言顶层恰好一个 exe 了"


# ============================================================ 消费者检查脚本


def _probe_binary() -> Path | None:
    for profile in ("release", "debug"):
        p = PROBE_DIR / "target" / profile / "updater-extract-probe"
        if p.is_file():
            return p
        pw = p.with_suffix(".exe")
        if pw.is_file():
            return pw
    return None


@pytest.mark.skipif(
    _probe_binary() is None,
    reason="探针二进制未构建（cargo build --manifest-path tools/updater-extract-probe/Cargo.toml）",
)
def test_selftest_passes_against_the_real_probe(tmp_path):
    """真探针上跑一遍反证自检：STORED 过、deflate 红。"""
    UCC.selftest(_probe_binary(), tmp_path)


def test_selftest_refuses_a_probe_that_accepts_deflate(tmp_path, monkeypatch):
    """自检的 deflate 那半必须真的咬人。

    探针被「修好」（加了 deflate feature、或干脆换成系统 unzip）时，
    对 deflate 包它会回 0——自检必须当场报门禁自身坏了，而不是放它去给
    线上检查发假绿。
    """
    monkeypatch.setattr(UCC, "run_probe", lambda probe, z, o: (0, "EXTRACT OK"))
    with pytest.raises(UCC.SelftestFailure):
        UCC.selftest(Path("/nonexistent-probe"), tmp_path)


def test_macos_package_check_wants_the_app_bundle(tmp_path):
    good = tmp_path / "good.tar.gz"
    with tarfile.open(good, "w:gz") as tar:
        f = tmp_path / "x"
        f.write_text("hi")
        tar.add(f, arcname="Tavotto.app/Contents/Info.plist")
    UCC.check_macos_package(good, tmp_path / "w1")

    empty = tmp_path / "noapp.tar.gz"
    with tarfile.open(empty, "w:gz") as tar:
        tar.add(f, arcname="README.txt")
    with pytest.raises(UCC.AssertionFailure):
        UCC.check_macos_package(empty, tmp_path / "w2")

    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"not a tarball")
    with pytest.raises(UCC.AssertionFailure):
        UCC.check_macos_package(junk, tmp_path / "w3")


def test_bad_signature_base64_is_an_assertion_failure(tmp_path):
    """signature 字段坏掉是产物问题（P0），不是网络问题，也不该炸成 traceback。"""
    payload = tmp_path / "pkg.zip"
    payload.write_bytes(b"x")
    with pytest.raises(UCC.AssertionFailure):
        UCC.verify_minisign(
            "minisign",
            b"untrusted comment: minisign public key\nKEY\n",
            payload,
            "!!!not-base64!!!",
            tmp_path,
        )


def test_config_comes_from_the_apps_own_tauri_conf():
    """endpoint 与公钥从 tauri.conf.json 现读——不另抄一份常量。

    另抄的那份会在换 endpoint / 换钥匙时漂开，而检查照旧绿。
    """
    endpoint, pubkey = UCC.load_updater_config()
    assert endpoint.endswith("/releases/latest/download/latest.json"), endpoint
    assert b"minisign public key" in pubkey.splitlines()[0]
    src = Path(UCC.__file__).read_text(encoding="utf-8")
    assert "releases/latest" not in src, (
        "脚本里不该写死 endpoint 字面量——唯一出处是 tauri.conf.json"
    )


def test_required_platforms_match_the_updater_manifest_requirement():
    """检查的平台清单必须与发布链合成 latest.json 时的硬要求一致。

    那边加了平台这边没加，新平台的更新包就永远没人以消费者身份验过。
    """
    desktop = (WORKFLOWS / "desktop-tauri.yml").read_text(encoding="utf-8")
    m = re.search(r"--require\s+([\w,-]+)", desktop)
    assert m, "desktop-tauri.yml 里找不到 make_updater_manifest 的 --require"
    assert set(UCC.REQUIRED_PLATFORMS) == set(m.group(1).split(",")), (
        f"消费者检查的平台 {sorted(UCC.REQUIRED_PLATFORMS)} 与 latest.json 的"
        f"硬要求 {m.group(1)} 不一致"
    )


def test_network_and_assertion_failures_have_distinct_exit_codes():
    """GitHub 抓不下来是 infra 波动（可 warning），抓下来解不开是 P0。

    两者混成一个退出码的话，要么网络抖动天天红到没人看，要么真红灯被
    当成网络抖动 warning 掉——后者正是这条门禁唯一要挡的东西。
    """
    assert UCC.EXIT_NETWORK != UCC.EXIT_ASSERTION
    assert UCC.EXIT_ASSERTION != 0 and UCC.EXIT_NETWORK != 0


def test_nightly_runs_the_consumer_check():
    """nightly 真的挂了这个 job，且只有网络失败（exit 2）允许降级。"""
    src = (WORKFLOWS / "nightly.yml").read_text(encoding="utf-8")
    assert "updater-consumer-fidelity:" in src, "nightly 里没有消费者保真检查 job"
    job = src.split("updater-consumer-fidelity:", 1)[1].split("\n  compat-version-matrix:", 1)[0]
    assert "updater_consumer_check.py" in job
    assert "updater-extract-probe" in job
    assert "minisign" in job, "验签那半不见了"
    assert "continue-on-error" not in job, "断言失败必须红，不是 warning"
    # 网络失败（exit 2）单独降级；其余非零一律透传成红
    assert '"$rc" = "2"' in job, "网络失败与断言失败的分诊不见了"
    assert 'exit "$rc"' in job, "非网络失败的退出码没有透传——断言失败会被吞掉"


def test_tools_crates_stay_out_of_the_wheel():
    """CI 工具 crate 不进 wheel/sdist（与 workerd/ 同一条纪律）。"""
    src = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"tools/**"' in src, "pyproject 的 exclude 里没有 tools/**"


# ============================================================ N-1 真实更新验证


def test_shell_e2e_update_trigger_is_default_off():
    """壳的 headless 更新触发口默认关死，只认字面 "1"。

    与 `--insecure-no-auth` 同一套纪律：这是一个测试专用开关，生产路径
    不认其它取值；触发时必须在 stderr 上打警告。
    """
    src = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert 'std::env::var("TAVOTTO_E2E_RUN_UPDATE").as_deref() != Ok("1")' in src, (
        '触发口的判据不再是「只认字面 "1"」——任何真值都触发的话，'
        "用户 shell 里一个手滑的环境变量就会让应用启动即自我更新"
    )
    assert "spawn_e2e_update_if_requested(handle.clone());" in src, (
        "触发口定义了却没在 setup 里接上——等于没有"
    )
    gate_body = src.split("fn spawn_e2e_update_if_requested", 1)[1]
    assert "仅测试用" in gate_body.split("tauri::async_runtime::spawn", 1)[0], (
        "触发时的警告不见了——静默的测试开关迟早被误用"
    )


def _n1_job() -> str:
    """release.yml 里 `n1_update_windows` 那个 job 的正文。"""
    src = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    return src.split("n1_update_windows:", 1)[1].split("\n  pypi:", 1)[0]


def test_release_runs_the_n1_update_verification():
    """发布编排里真的有 N-1 更新验证 job，且形状对。"""
    src = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    assert "n1_update_windows:" in src, "release.yml 里没有 N-1 更新验证 job"
    job = src.split("n1_update_windows:", 1)[1].split("\n  pypi:", 1)[0]
    # 只能在 Release 建出来之后测：endpoint 烤死指向 releases/latest
    assert "github_release" in job.split("steps:", 1)[0], (
        "N-1 验证必须 needs github_release——发布前 latest 还指着上一版"
    )
    assert "needs.trust.outputs.publish == 'true'" in job, (
        "演练（publish=false）没有建 Release，N-1 验证无从谈起"
    )
    assert "TAVOTTO_E2E_RUN_UPDATE" in job, "不再用壳的 headless 触发口驱动了"
    # 冒烟复用既有断言，不另写一套
    assert (
        "smoke_app.py" in job and "--expect-source bundled" in job and "--expect-runtime" in job
    ), "更新后的冒烟不再复用 smoke_app 的既有断言"
    # 能力探测按二进制里的字面量判，不写死版本号：
    # 「哪一版加的触发口」在那次发布之前没人知道号码，版本常量迟早说谎
    assert 'Contains("TAVOTTO_E2E_RUN_UPDATE")' in job, (
        "N-1 有没有触发口必须按装出来的二进制探测，不许写死版本阈值"
    )
    # 等新进程出现，不等旧进程优雅退出（NSIS /R 重启、旧进程 exit(0) 不走 RunEvent::Exit）
    assert "ProductVersion" in job, "「重启后跑的是新版本」的断言不见了"


def test_n1_new_process_is_pinned_by_image_path_not_by_name():
    """#147：「新进程」的主语按**壳的映像路径**认，不许按进程名认。

    安装目录里有两个都叫 `Tavotto.exe` 的二进制——壳在安装根，sidecar 在
    `sidecar/Tavotto/Tavotto.exe`（同一个 job 的冒烟步骤正是这么找 sidecar
    的）。`Get-Process Tavotto` 按进程名匹配，两个都收；而 sidecar 是
    PyInstaller 产物，**没有版本资源**（对 v0.12.0 官方安装包实测：壳
    ProductVersion=0.12.0，sidecar 连 StringFileInfo 都没有）。选中 sidecar
    就必然读到空 ProductVersion，且**等多久都不会变**——重试窗口救不回来，
    这是量错对象，不是时序。

    坏掉之后会怎样：退回 `Where-Object { $_.Id -ne $p.Id }` 那种按名字取
    「第一个不是旧进程的」，更新链成功的那次发布照样报红（v0.12.0 的
    run 33027201414 就是这么红的）。
    """
    job = _n1_job()
    assert "Resolve-TavottoPath $exe" in job and "$shellCanon" in job, (
        "「新进程」不再按壳的映像路径认——按进程名取会把 sidecar 认成壳"
    )
    # 两侧都要过规范化：只规范化一侧＝拿规范化的尺子去量没规范化的东西
    assert "$shellCanon = Resolve-TavottoPath $shell" in job, (
        "壳这一侧没有规范化——尾部反斜杠 / 大小写 / 8.3 短名任一对不上就红在正式发布中途"
    )
    for token, why in [
        ("[System.IO.Path]::GetFullPath", "重复分隔符与 `..` 不会被归并"),
        ("GetLongPathName", "8.3 短名不会被展开"),
        ("OrdinalIgnoreCase", "Windows 路径大小写不敏感，逐字比会误判"),
    ]:
        assert token in job, f"路径规范化里少了 {token}——{why}"
    assert "Where-Object { $_.Id -ne $p.Id } | Select-Object -First 1" not in job, (
        "又回到了「第一个不是旧进程的同名进程」——那正是 #147 的红灯来源"
    )
    # 「在安装目录下」不是替代品：sidecar 也在安装目录下
    assert "$procPath.StartsWith($inst" not in job, (
        "用「映像在安装目录下」当主语判据——sidecar 就在安装目录下，它放得过去"
    )


def test_n1_version_read_is_null_safe_and_polls_as_advertised():
    """#147：ProductVersion 读空必须走**显式 fail 分支**并打印可读诊断。

    v0.12.0 发布 run 33027201414：更新链本身成功（DisplayVersion → 0.12.0、
    新进程起来了），但 `$pv.StartsWith($env:NEW_VERSION)` 在 null 上抛
    InvalidOperation，把该有诊断的断言变成裸脚本错误。

    顺带钉住「错误信息本身也是断言」：诊断里写了轮询窗口，代码里就必须真的
    有那个循环——第一版只给映像路径加了轮询，版本资源仍是读一次，而诊断已经
    写着「已轮询 15s」。
    """
    job = _n1_job()
    # 主语是**守卫**那一处，不是轮询循环里的 `-not …` break 条件——两处长得
    # 很像，锚错了就变成「循环在 StartsWith 之前」这种恒真的断言。
    guard_head = "if ([string]::IsNullOrWhiteSpace($procPv)) {"
    guard = job.find(guard_head)
    use = job.find("$procPv.StartsWith(")
    assert guard != -1, "ProductVersion 的空值守卫不见了"
    assert use != -1, "「是不是新版本」的比较不见了"
    assert guard < use, "空值守卫排在 .StartsWith 之后——null 上调方法仍会抛 InvalidOperation"
    # 空值走 throw，且诊断点名 pid / 映像路径 / 句柄读到的原始值
    empty_branch = job.split(guard_head, 1)[1].split("\n          }", 1)[0]
    assert "throw" in empty_branch, "ProductVersion 为空时不再 fail——门禁被掏空了"
    for token, why in [
        ("$procPath", "映像路径"),
        ("$($fresh.Id)", "pid"),
        ("$handlePv", "句柄读到的原始值"),
    ]:
        assert token in empty_branch, f"空值诊断里没有{why}，读的人无从判断是哪一种空"
    # 句柄那份只当诊断：必须包在 try/catch 里，读不到不构成失败
    assert "try { $handlePv = " in job and "catch { $handlePv = " in job, (
        "进程句柄的 MainModule 读值不再是「包住的诊断」——它一旦能失败就又是裸错误"
    )
    # 诊断承诺的轮询窗口，代码里必须真的有
    import re

    m = re.search(r"读不到新进程映像的 ProductVersion（\$procPath，已轮询 (\d+)s）", job)
    assert m, "空值诊断不再说明轮询了多久——读的人分不清「没等」还是「等了也没有」"
    window = m.group(1)
    assert re.search(rf"foreach \(\$i in 1\.\.{window}\) \{{\s*\n\s*\$vi = ", job), (
        f"诊断说 ProductVersion 轮询了 {window}s，代码里却没有对应的循环——"
        "错误信息本身也是断言，说了就必须兑现"
    )


def test_n1_update_step_exits_explicitly():
    """#197：pwsh 步骤的后置条件写显式 `exit 0`，不托付隐式退出码传播。

    #192 在 windows-exe-smoke 上被踢出三次：所有 throw 都没触发、脚本执行到
    最后一行、日志里没有任何异常块，步骤仍然退 1，机制至今没查清。这一步的
    契约是「三条判据都过 = 成功」，所有失败路径都是 throw，走不到 `exit 0`。
    """
    job = _n1_job()
    step = job.split("驱动真实应用内更新并断言换到了新版本", 1)[1].split("\n      - name:", 1)[0]
    assert step.rstrip().endswith("exit 0"), (
        "N-1 更新断言这一步不再以显式 `exit 0` 收尾——退出码托付给了与结论无关的命令（issue #197）"
    )


def test_release_notes_do_not_leak_powershell_backticks():
    """n1 job 的 pwsh 双引号字符串里不许出现 \\`——那是 pwsh 的转义字符。

    `` \\`T `` 会把后面的字符转义（`` `t `` 是 tab），summary 悄悄变成乱码
    而 job 照样绿。写这条是因为第一版真的写出来过。
    """
    src = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    job = src.split("n1_update_windows:", 1)[1].split("\n  pypi:", 1)[0]
    assert "\\`" not in job, "pwsh 字符串里混进了 \\`（backtick 是 pwsh 的转义字符）"
