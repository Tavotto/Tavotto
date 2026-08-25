"""Windows 更新包的打包契约（scripts/make_updater_zip.py）。

看护的失败样貌（2026-08-25 用户报告，v0.7.0 起每个 Windows 更新包都中招）：
应用内更新进度条走满，然后「无法安装更新，去 Releases 手动下载」。根因是
tauri-plugin-updater 对 Windows 的 zip 依赖 **default-features = false**——
deflate 解压 feature 被关掉，只解得开 STORED（方法 0）的条目；发布链却用
Compress-Archive（默认 deflate，方法 8）重打更新包，插件解包当场报
"Compression method not supported"。确定性复现：同版本 zip crate 4.6.1 +
default-features=false 对线上 v0.10.0 更新包 EXTRACT FAILED，重打成
STORED 后 EXTRACT OK。tauri-bundler 自己的 create_zip 用的正是
CompressionMethod::Stored（tauri-cli-v2.11.4 updater_bundle.rs）——我们
因为要装 SignPath 签过名的最终安装包而必须重打，重打就得守同一个约定。

这里守三件事：脚本产物每个条目都是 STORED 且 exe 在顶层（插件解包后只在
顶层 read_dir 找 .exe）；坏输入报错而不是安静产出坏包；workflow 真的在用
这个脚本而不是 Compress-Archive。
"""
import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SPEC = importlib.util.spec_from_file_location(
    "make_updater_zip", ROOT / "scripts" / "make_updater_zip.py"
)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


def _fake_exe(tmp_path: Path, name: str = "Tavotto-0.11.0-Windows-Setup.exe") -> Path:
    exe = tmp_path / name
    # 高熵内容：真实 NSIS 安装包本来就是压过的，STORED 是对的选择
    exe.write_bytes(bytes(range(256)) * 64)
    return exe


def test_every_entry_is_stored_and_exe_is_at_top_level(tmp_path):
    """契约本体：STORED + 顶层 .exe。

    更新器插件（zip 依赖关了 deflate）解不开方法 8 的条目；解开后只在
    **顶层** read_dir 找第一个 .exe——带目录前缀的条目等于没有。
    """
    exe = _fake_exe(tmp_path)
    out = tmp_path / "Tavotto_0.11.0_x64-setup.nsis.zip"
    mod.build(exe, out)

    with zipfile.ZipFile(out) as z:
        infos = z.infolist()
        assert len(infos) == 1
        info = infos[0]
        assert info.compress_type == zipfile.ZIP_STORED, "条目必须是 STORED（方法 0）"
        assert "/" not in info.filename and "\\" not in info.filename, "exe 必须在 zip 顶层"
        assert info.filename.endswith(".exe")
        assert z.read(info.filename) == exe.read_bytes(), "重打不许动 exe 一个字节"


def test_missing_or_wrong_input_is_a_hard_error(tmp_path):
    out = tmp_path / "o.nsis.zip"
    with pytest.raises(SystemExit):
        mod.build(tmp_path / "not-there.exe", out)
    notexe = tmp_path / "Tavotto.msi"
    notexe.write_bytes(b"x")
    with pytest.raises(SystemExit):
        mod.build(notexe, out)


def test_workflow_uses_the_script_not_compress_archive():
    """发布链必须走这个脚本。

    Compress-Archive 回来一次，Windows 应用内更新就再坏一轮——而且 CI 全绿、
    清单齐全、签名有效，只有真机点「下载并安装」才见得到。
    """
    wf = (ROOT / ".github" / "workflows" / "desktop-tauri.yml").read_text(encoding="utf-8")
    # 注释里正写着「为什么不能用 Compress-Archive」，剥掉再断言，别被说明骗过去
    code = "\n".join(
        ln for ln in wf.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "make_updater_zip.py" in code, "workflow 没在用 make_updater_zip.py 打更新包"
    assert "Compress-Archive" not in code, "更新包不许用 Compress-Archive（deflate）打"
