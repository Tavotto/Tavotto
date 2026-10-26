# 命令行：`tavotto open` 与 `tavotto run`

> 属于 [Tavotto 自述](../../README.zh-CN.md)，这里放 README 指过来的细节。

## 收尾 AI 画的图

<p align="center">
  <img src="https://raw.githubusercontent.com/Tavotto/Tavotto/main/assets/readme/workflow.zh.svg" width="100%"
       alt="工作流：你、Claude 或 Codex 写的 Python 脚本跑 matplotlib 产出矢量 PDF；Tavotto 负责图内编辑、排版与投稿前检查；结果是 PDF 或 PNG。脚本全程是源头。">
</p>

编程助手写的第一版 matplotlib 已经相当好，剩下的都是视觉活儿——图例高了两行、
这张图想再小一点、标签压住了一个数据点。把这些用提示词描述一遍，比自己动手还慢。

一条命令把图交接过来，终端里或 agent 里都行：

```sh
tavotto open figures/Fig1_kinetics.pdf   # 给产物
tavotto open figures/fig1_kinetics.py    # 给脚本也行，产出名自动解析
tavotto open figures/                    # 或者整个图库
```

它会把这张图所在的图库当项目打开、把缺的条目补进注册表，然后把图送进桌面应用——
已经开着窗口就直接送进那一个，不会另起一套。没装桌面版则退回浏览器模式。

**用 Codex 的话**可以装插件。它让 Codex 知道「Tavotto 能接手的图」该长什么样
（脚本与产物同目录、矢量 PDF、产出名可静态解析），并把编辑器搬进 Codex 里——
安装命令与第一个会话该说什么，见下面的
[在 Codex 中第一次使用 Tavotto](../../README.zh-CN.md#在-codex-中第一次使用-tavotto)。

插件带一个 skill、一个本地 MCP server（九个工具：健康检查、打开图、应用 override、
规范化一张图、跑预检、按指定 DPI 导出真矢量 PDF/SVG/EPS 或 PNG/TIFF、校验重放、
刷新项目、关闭会话——在完全没有界面的宿主里也能用），以及一块内嵌画布，用的是
**和桌面应用同一份**前端代码，拖拽、吸附、撤销没有第二套实现。详见
[`codex-plugin/README.md`](../../codex-plugin/README.md)，其中写清了哪些部分在真实的
Codex Desktop 里验证过、哪些没有。

模型建议的路径永远不等于权限。零配置第一次打开时，支持这项能力的 Codex 会把
规范化后的本地目录展示给你确认；批准只在当前 Tavotto MCP 连接内有效。

## 把现成的项目带进来 · `tavotto run`（Beta）

`tavotto open` 假设的是 Tavotto 喜欢的形状：脚本挨着它的产物，都在一个图库目录
里。真实的论文项目常常不是这样——一个 conda 环境、一个包、几个命令行参数：

```sh
conda activate paper
python -m figures.fig3 --dataset run7
```

这一类，在你本来就在敲的那条命令前面加 `tavotto run --`：

```sh
tavotto run -- python figure.py
tavotto run -- python figure.py --sample A --temperature 800
tavotto run -- /path/to/paper/.venv/bin/python figure.py
tavotto run -- python -m paper.figures.xps --sample A
```

Tavotto 用**你给的那条 Python 命令**去跑，只接管那个进程里创建的 Matplotlib
Figure。解释器、工作目录、命令行参数、环境变量、`stdout` / `stdin`——一样都不
重建、不接管。`savefig` 照常写它本来就会写的文件；Ctrl+C 照常打断你的脚本；
`tavotto run` 返回的是**你脚本自己的退出码**。

在 Tavotto 里编辑**不会改变你的脚本看到的东西**：

```python
ax.set_title("Script")
plt.show()                          # 你在 Tavotto 里把标题改掉
assert ax.get_title() == "Script"   # 通过——你的代码是执行权威
```

脚本启动**之前**，桌面上会问一次，写明解释器路径、工作目录和目标。
**你确认之前，一行代码都不会跑。**

> 这个模式**不是沙盒**：脚本拥有与你自己运行它时完全相同的权限。只运行你信任
> 的代码。

Beta，边界是明确的：只支持 Python 脚本或 `-m` 模块、只接管那一个进程里的图、
不支持任意 shell 包装、不支持 Jupyter、不写回你的源码、不写回你脚本自己存出来
的产物、需要桌面应用。完整契约、错误码与排障见
[`docs/compatibility/tavotto-run.md`](../compatibility/tavotto-run.md)。
