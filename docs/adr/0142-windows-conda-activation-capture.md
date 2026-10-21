# ADR 0142：Windows 上的 Conda 激活捕获——与 POSIX 同构的“激活一次、捕获环境、直接启动”

日期：2026-10-21 · 状态：**Proposed**（risk:high；Win11 真机验收：第 1/2/4/5/6/8 项已通过，第 7 项近似通过（真「开始菜单」启动未验），第 3 项改由 #1282 承接）
相关：规则全文 `docs/rules/backend/figure-capture-and-execution.md`「成功启动上下文与 Conda 激活」（P09，#975）；
[0021 tavotto run 产品契约](0021-tavotto-run-product-contract.md)；[0125 桌面拥有的 native 进程](0125-desktop-owned-native-process.md)。

## 问题

#975 的捕获方案只在 POSIX 启用，Windows 的 `launcher_for_interpreter` 恒 None（没有真机验证）。依赖 `activate.d`
（`ROOTSYS`、`PATH`、`Library\bin` 的 DLL 搜索路径……）的环境（ROOT 一类）在 Windows 上被裸起，import 必然失败。
用户裁决（2026-10-21）：照 macOS 方案扩到 Windows，**真机验收后默认开**。

## 决策

1. **同一机制，不另起炉灶**：`conda.exe run --no-capture-output --prefix <p> <python.exe> -I -S <脚本> <结果文件>`，把 `os.environ`
   写进 0600 结果文件，取相对当前环境的增量，进程内缓存，绝不持久化；之后所有启动点直接起解释器，只换 env。缓存键、`prefix_state`、单飞、
   预算、`no_capture` / `prewarm`、fail-closed 与稳定码 `conda_launcher_unavailable` 全部复用，**零分叉**。
2. **选 `Scripts\conda.exe`，不选 `condabin\conda.bat`，也不选 `cmd /c call activate && python`**：`.bat` 路线要自己拼 cmd.exe 引号，
   而中文 / 空格路径、`^ & % !` 在 cmd 里的转义规则是历史上最多出错的一块（#975 同类的“包装进程”问题面）。
   `conda.exe` 经 `subprocess.Popen(list)` → CreateProcess，Tavotto 这一层没有任何 shell 解析；`conda run` 自己如何写临时 `.bat`
   是 conda 的责任，也是真机验收项。其余候选（解析 `conda shell.cmd.exe activate` 输出）要自己解析 `SET` 脚本与代码页，不如读 Python 的 `os.environ` 可靠。
3. **捕获脚本落成文件**：`conda run` 在 Windows 把命令行写进临时 `.bat`，多行 / 带引号的 `-c` 参数会被 cmd 破坏，所以 Windows 把固定的捕获源码写成
   `probe-results/capture-*.py`（用完即删，登记在途清理），argv 里只有路径。POSIX 继续 `-c`（不变）。
4. **编码**：结果文件由 Python 以 UTF-8 写、以 UTF-8 读，不经 stdout，所以控制台代码页（chcp 936 / 65001）不影响增量；conda 的 stdout/stderr 丢弃。
   Windows 环境变量名不分大小写，`os.environ` 已规范成大写，增量的比较沿用。
5. **超时收尾**：Windows 无进程组 / 信号，用 `taskkill /PID <conda> /T /F` 收整棵树（输出丢弃，不解码），再 `proc.kill()` + 有期限的 `wait`。
6. **默认开，保留逃生口**：`TAVOTTO_CONDA_ACTIVATION=0` 在 Windows 上同样生效。信任边界不变：只激活用户自己的 conda 安装根（`Scripts\conda.exe` + `conda-meta`）
   及其 `envs\<名字>` 下的环境，路径比较 `normcase`。
7. **安装根的第三个来源：安装程序写入的注册表卸载项**（2026-10-21 真机验收发现的缺口）。Miniconda 默认不加 PATH、也可装在 `D:\anaconda3`
   这类自定义位置，不设 `CONDA_EXE` 时 `CONDA_EXE` / PATH / 标准位置都找不到，会被当成“不可信”而裸起。所以 Windows 上额外读
   `HKCU` 与 `HKLM`（含 WOW6432Node）`Software\Microsoft\Windows\CurrentVersion\Uninstall\*`：`DisplayName` 以 Miniconda / Anaconda / Miniforge / Mambaforge / Micromamba 开头（不分大小写）的项，
   取 `InstallLocation`，没有则取 `UninstallString`（去引号）所在目录（真机上 Miniconda 的卸载项就只有后者）；另加 PEP 514 `Software\Python\ContinuumAnalytics\*\InstallPath`。
   **理由**：卸载项由用户自己运行的安装程序写在用户 / 机器注册表里，项目目录改不了它——与 `environments.txt`（项目可伪造、会过期）不同；
   且它只产出**候选**，仍必须过 `_is_install_root`（`conda-meta` + `Scripts\conda.exe`），伪造或过期的条目不会被信任。读取有界（每处最多 512 项），读取器可注入（测试用假读取器），任何读取错误只是少一个候选。
8. **可信根里的 conda 不可用 = 瞬时失败，不是“非 Conda 环境”**（真机验收第 5 项发现）。环境在某个**可信来源指出的安装根**（`CONDA_EXE` / PATH / 标准位置 / 注册表，
   且有 `conda-meta`）下——根本身或 `envs\<名字>`——但该根的 `Scripts\conda.exe`（POSIX `bin/conda`）不可用（改名 / 删除）时，`activate_env` 与 `prewarm` 抛
   `LauncherUnavailable`（`conda_launcher_unavailable`，不缓存），不再落回直接启动：这类环境不激活就会在运行时崩（r314 缺 `Library\bin`，退出码 0xC06D007F），
   而裸起的体检可能偏偏通过。找不到任何可信根的 Conda 形状目录（克隆项目自带的 `conda-meta/`、`conda create -p` 到任意目录）仍直接启动，既定行为不变。
   判据在 `launcher.raise_if_trusted_root_unusable`；`launcher_for_interpreter` / `context_key` 仍是不抛的纯函数。
9. **不碰**：mamba / micromamba、用户环境发现（userenvs）、depplan。

## Win11 真机验收清单

状态（2026-10-21，Miniconda 26.9.3 / py314，自定义根 `C:\Users\Jiaqi\tavotto verify\miniconda3`，带空格）：

1. 带空格路径 + 含 matplotlib 的环境出图——**已通过**。
2. activate.d 设变量 / 横幅不干扰——**已通过**。
3. ROOT——改由 #1282 承接（conda-forge 无 win-64 ROOT，Windows ROOT 走官方包叠加，已真机通过）。
4. activate.d 卡住 → 约 60 s 后 `conda_launcher_unavailable`，无遗留进程、`probe-results` 为空——**已通过**。
5. 改名 `Scripts\conda.exe`：应得到瞬时 `conda_launcher_unavailable`（不是“可用”）——首次真机**未通过**（体检 ok:true、运行时崩），已按决策 8 修复，**已通过**（2026-10-21 Win11 真机复验：改名后瞬时 `conda_launcher_unavailable`、改回后恢复）。
6. 代码页 936 / 65001——**已通过**。
7. 无 `CONDA_EXE`、PATH 只留 System32 下经注册表找到根——**近似通过**（真“开始菜单启动”未验）。
8. `conda install` / 增删 activate.d 后重新捕获——**已通过**（缓存命中 0.07 s，重新捕获 1.75 s）。
另：注册表来源（决策 7）不设 `CONDA_EXE`、conda 不在 PATH 时已由第 7 项的场景覆盖。
