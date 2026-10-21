# ADR 0142：Windows 上的 Conda 激活捕获——与 POSIX 同构的“激活一次、捕获环境、直接启动”

日期：2026-10-21 · 状态：**Proposed**（risk:high；**Win11 真机验收后才合入**）
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
7. **不碰**：mamba / micromamba、用户环境发现（userenvs）、depplan。

## Win11 真机验收清单（合入前）

1. Miniconda 装在含空格 / 中文用户名的路径下，`conda create -n t python=3.11 matplotlib`，让 Tavotto 选用该解释器运行一个出图脚本：成功。
2. 给该环境加 `etc\conda\activate.d\x.bat`（`set TAVOTTO_PROBE=1`，并 `echo` 一行横幅）：脚本里 `os.environ["TAVOTTO_PROBE"]` 可见；横幅不污染 worker 协议。
3. 装 ROOT（conda-forge）：`import ROOT` 成功；关掉开关（`TAVOTTO_CONDA_ACTIVATION=0`）复现失败作负对照。
4. activate.d 里 `timeout /t 120`：约 60 s 后得到 `conda_launcher_unavailable`，任务管理器里无遗留 `conda.exe` / `cmd.exe` / `timeout.exe`；`probe-results\` 无残留。
5. 把 `Scripts\conda.exe` 临时改名：Conda 形状的解释器得到「找不到 conda」的瞬时错误，不是“缺 matplotlib”的结论。
6. 区域设置为中文（代码页 936）与开启“Beta：使用 UTF-8”(65001) 各跑一次 1、2。
7. 从开始菜单 / 桌面启动 Tavotto（无 `CONDA_EXE`、`PATH` 很短）也能经标准安装位置找到 conda。
8. `conda install` 一个包后再运行：重新捕获（`prefix_state` 变化）。
