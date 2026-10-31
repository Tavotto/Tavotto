# 安装详解：桌面版、PyPI、远程服务器与 Python 环境

> 属于 [Tavotto 自述](../../README.zh-CN.md)，这里放 README 指过来的细节。

## 桌面版

到[最新发行版](https://github.com/Tavotto/Tavotto/releases/latest)下载 macOS 的
`.dmg`（Apple Silicon 选 `-macOS.dmg`，Intel 选 `-macOS-Intel.dmg`）或 Windows 的
`.exe`（x64；Windows ARM 没有构建、也没有验证过）。
装完双击即用——Tavotto 在自己的窗口里打开，之后的升级也都在软件内完成。

**你不需要自己装 Python。** 每个安装包都自带一套 Tavotto 专用的 Python 运行环境，
常用科学栈已经装好——numpy、matplotlib、pandas、scipy、seaborn、Pillow，
每个安装包锁的都是同一组版本，同一个脚本在哪台机器上都画出同一张图。装完立刻就能渲染：
不联网、不需要 Homebrew / Conda / Xcode，也不碰你已有的任何 Python。

这套内置环境也是安装包偏大的原因：**macOS 下载约 195 MB（Apple Silicon 版实测），
Windows 约 89 MB，装完约半个 GB**。只付一次，而且完全离线。

> macOS 版**按架构各发一个 `.dmg`**，各自在对应架构的机器上原生构建和冒烟；
> 按你的 Mac 选（Apple 菜单 →「关于本机」：写「芯片」的是 Apple Silicon，写「处理器 … Intel」的是
> Intel）。系统要求：**Apple Silicon 需 macOS 14 及以上，Intel 需 macOS 15 及以上**——这条下限来自
> 渲染器自带的 PDF 库（pikepdf / PDFium）。没有 Linux 安装包；Linux 走 PyPI（浏览器模式，beta）。
> Windows 安装包是否经过代码签名，以各版 Release 页的说明为准；未签名的安装包
> 首次运行会弹 **SmartScreen** 提示（点「更多信息 → 仍要运行」，或先对照
> Release 页的 `SHA256SUMS.txt` 核验下载）。支持 / beta / 不支持的唯一权威
> 清单是 [`docs/support-matrix.json`](../support-matrix.json)——发行页、
> 官网与应用内文案都以它为准（可机器核对的部分有测试看护）。

## PyPI

三个平台命令相同：

```sh
pipx install "tavotto[worker]"
tavotto
```

浏览器会打开 `http://127.0.0.1:5089`。`--figures <目录>` 直接打开某个图库，
`--port` 换端口，`--no-browser` 不自动开浏览器。

> **需要 Python 3.10 及以上。** Python 太旧（Ubuntu 20.04 自带 3.8）时，pip 只报
> `No matching distribution found for tavotto[worker] (from versions: none)`，不说原因。
> 先 `python3 --version` 看一眼；版本不够就让 [uv](https://docs.astral.sh/uv/)
> 自带一份 Python 来装——不用 sudo，也不动系统 Python：
>
> ```sh
> curl -LsSf https://astral.sh/uv/install.sh | sh
> # 新开一个终端，uv 和 tavotto 才在 PATH 上
> uv tool install --python 3.12 "tavotto[worker]"
> ```
>
> `pipx install` 失败会留下 `~/.local/pipx/venvs/tavotto[worker]`，下次再装就报
> 「Not installing to existing directory」；先删掉这个目录。

### 在远程服务器上，经 SSH

Tavotto 只监听 `127.0.0.1`，所以服务器上的那份要经 SSH 端口转发访问，而不是直接走网络。
在服务器上：

```sh
pipx install "tavotto[worker]"
tavotto --no-browser
```

它会打印一个以 `#dnonce=…` 结尾的登录地址。在你自己的电脑上转发**同一个端口号**，再在浏览器里打开那个地址：

```sh
ssh -L 5089:127.0.0.1:5089 you@server
```

- **本地端口必须和服务器的 `--port` 一致**，地址要写 `127.0.0.1`，不能写 `localhost`：
  Tavotto 会拒绝其他 `Host` 以防 DNS 重绑定，不一致时会看到 `bad_host` 错误。任一端的 5089 被占用时，
  两头一起换：`tavotto --port 5189 --no-browser` 与 `ssh -L 5189:127.0.0.1:5189 …`。
- **每个登录地址只能用一次。** 之后浏览器带着会话 cookie，保留 30 天或到服务器进程重启为止。
  Tavotto 还在运行时想要新地址，就在服务器上再跑一次同样的 `tavotto --no-browser`（同一个 `--port`）：
  它会复用正在运行的实例并打印一个新地址，有效期五分钟。
- **在桌面版里**用 **文件 → 连接远程实例…**，不用浏览器：它会开一个独立窗口，粘贴登录地址后，
  服务器上的 Tavotto 就在这个窗口里运行。该窗口有自己独立的 cookie 存储，也不能使用本机文件：
  选目录、在 Finder 中显示、拖入文件都退回浏览器的行为，因为每条路径都属于服务器。
  服务器上的 Tavotto 太旧时窗口会提示；在服务器上用 `pip install -U tavotto` 升级。
  关闭窗口即结束会话，重新连接需要新的登录地址。

## 30 秒跑通一遍

```sh
pipx install "tavotto[worker]"
git clone --depth 1 https://github.com/Tavotto/Tavotto.git
tavotto --figures Tavotto/examples/figures
```

素材栏里会出现三张图。拖一张到版面上、双击它、在元素树里点标题、
把 9 pt 改成 11、导出。`examples/figures/` 里就是两个普通的 matplotlib 脚本——
Tavotto 不要求你按任何特殊方式写它们。

<details>
<summary><b>进阶安装与渲染环境</b></summary>

**pip**（装进当前环境）：

```sh
pip install "tavotto[worker]"
tavotto
```

> **装了 astropy 8 的科学环境，别把 Tavotto `pip install` 进去。** Tavotto 依赖
> uharfbuzz，而目前所有 uharfbuzz 0.56.x 的 wheel 都和 astropy 8 的编译模块冲突
> （Cython 3.3.0 的缺陷，[cython/cython#7914](https://github.com/cython/cython/issues/7914)）。
> 在那样的环境里，只要导入过 `astropy.table`，你**自己**脚本的 `savefig(..., format="pdf")`
> 就会报 `TypeError: Shared Cython type cython_function_or_method has the wrong size`，
> 跟 Tavotto 有没有在运行无关。请用上面的 `pipx` 装 Tavotto，再按下面的方法让它使用那个环境。
> 上游跟踪：[uharfbuzz#318](https://github.com/harfbuzz/uharfbuzz/issues/318)、
> [fonttools#4354](https://github.com/fonttools/fonttools/issues/4354)。

**复用画图时用的那套环境**：去掉 `[worker]` 只装本体，再指定你自己的解释器——
这样渲染用的就是脚本当初依赖的那一套。（这是轻量 CLI 形态：没有 `[worker]` 就
没有随装的渲染栈，渲染——包括 Codex 的 MCP 集成——完全依赖你指定的那个解释器）：

```sh
pipx install tavotto
export TAVOTTO_WORKER_PYTHON=/path/to/your/env/bin/python   # Windows: setx TAVOTTO_WORKER_PYTHON "..."
tavotto
```

**贡献者：从源码开发。** 这条路只用于修改 Tavotto 本身，绝不是普通安装失败后的
退路（需要 node + pnpm 构建界面）：

```sh
git clone https://github.com/Tavotto/Tavotto.git && cd Tavotto
python -m venv .venv && .venv/bin/pip install -e ".[worker,dev]"
.venv/bin/python scripts/fetch_fonts.py   # 批准字体（不进 git）
python scripts/build_frontend.py
.venv/bin/tavotto
```

**用哪个解释器渲染**。选择顺序：`TAVOTTO_WORKER_PYTHON` → 你在设置里指定的 →
内置环境 → Tavotto 自身的解释器 → 机器上探测到的 Python / Conda。
**你显式指定的永远优先**；Tavotto 只是**启动**你指定的环境来渲染，
绝不往里面装任何东西，也绝不修改你已有的 Python / Conda。内置环境同样只读——
字节码与 matplotlib 字体缓存都改道到 Tavotto 自己的数据目录，安装目录一个字节都不写
（macOS 上往签过名的 `.app` 里写东西会直接破坏代码签名）。体检你的环境时同样不往里面
写字节码；应用在这个数据目录里替你建的环境（项目的受管环境、自备的 Python），pip 与 matplotlib
的缓存也都放在这里。

内置环境覆盖的是**常用**科学栈，**不承诺覆盖你脚本可能 import 的任意包**。
脚本要用它没有的包（rdkit、astropy、自家实验室的库）时，Tavotto 会直接说出缺的是
哪个包，并给出「换成你自己的环境」的入口（**设置 → 渲染环境**）；它不会替你把那个包
装进内置环境，也不会装进你的环境。一个可用解释器都没有时，排版、标注、导出照常，
只有图内编辑用不了。

**设置 → 隐私、诊断与 About** 始终显示当前用的是哪个解释器、来自哪里
（`bundled` / `configured` / `system` …）；用内置环境时还会显示每个包的精确版本。
诊断包里也有同一份。

**第一次打开某张图时会跑一遍你的脚本**。轻量图秒级，重的该多久就多久；
之后每次修改都是亚秒级。

</details>
