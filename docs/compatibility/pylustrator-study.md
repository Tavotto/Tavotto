# Pylustrator clean-room 研究（Compatibility Bridge Session 1）

- **研究对象**：<https://github.com/rgerum/pylustrator>
- **研究 commit**：`b0341ee96cd3d11aab62e444ad6bf8b0abb83ce2`（v1.3.0 之后，
  2026-06-05，"Fix tick label font editing (#88)"）
- **许可证**：GPL-3.0（仓库根 `LICENSE`）。Tavotto 是 AGPL-3.0-only；即便
  许可证兼容，**本研究也不 vendoring、不复制任何代码**——只记录机制与差异，
  实现全部 clean-room。
- **研究位置**：`/tmp/pylustrator-study`（仓库外，用完即删）。
- **明确不采用**：Qt GUI、PyQt5 运行时依赖、"保存即改写源码"的默认行为、
  change tracker / 代码生成器的直接复制、把 Pylustrator 当 fallback 引擎、
  pickle Figure 跨进程。

## 它是怎么工作的（机制记录）

Pylustrator 的使用形态是：用户在**自己的脚本里**加两行——

```python
import pylustrator
pylustrator.start()
...  # 用户自己的绘图代码，原样不动
plt.show()
```

然后照常 `python their_script.py`。这个形态决定了它的全部兼容优势。

### 1. live Figure capture 与 `plt.show()` 拦截

`pylustrator.start()`（`QtGuiDrag.py::initialize`）monkeypatch 掉
`plt.show` / `plt.figure`。真正的入口是 `plt.show()` 被调用的那一刻：
`pyl_show()` 遍历 **`matplotlib._pylab_helpers.Gcf.figs`**（pyplot 的
live figure 注册表），给每一张还活着的 Figure 开一个 Qt 编辑窗口
（`window.setFigure(fig)` + `DragManager(fig)`）。

要点：

- **从 live Figure 出发，不看磁盘产物**。脚本有没有 savefig、产物在哪、
  文件叫什么，一概不影响"能不能编辑"。
- **多 Figure 天然支持**：Gcf 里有几张就开几张，一张不丢。
- Tavotto 的 `figcapture.collect_pyplot_figures()` 用
  `plt.get_fignums()`（同一个 Gcf，按创建顺序）做 pyplot 兜底，机制同源；
  差别在 Tavotto 还要给每张图一个**稳定 stem**（Pylustrator 不需要命名，
  窗口即身份）。

### 2. `savefig()` 拦截

`initialize()` 包掉 `Figure.savefig`：把 `(filename, args, kwargs)` 记进
`fig._last_saved_figure` 列表，然后**照常调用原 savefig**（透传）。它拦截
是为了记住"这张图保存到哪"，之后编辑完能按原路径重存——不是为了阻止写盘。

Tavotto 的 `worker._patched_savefig` 相反：**吞掉写盘**（沙盒纪律，build
期间一个图文件都不写），只按 stem 捕获 Figure。两者目的不同：Pylustrator
运行在用户自己的进程里、写盘本来就是用户要的；Tavotto 的 safe 模式替用户
执行、必须不碰真实目录。**native 模式（ADR 0014）里"savefig 透传"这个
选项正是从这里来的**（`ExecutionSpec.passthrough_savefig`）。

### 3. 原 cwd / argv / env / import 的天然保留

Pylustrator **没有任何一行代码**处理 cwd、argv、env、sys.path——因为脚本
是用户自己用自己的解释器、在自己的目录里、带自己的参数跑起来的，
`pylustrator.start()` 只是寄生在这个进程里。相对路径读数据、读
matplotlibrc、本地包 import、Conda 环境……全部天然成立。

这是它对"复杂老项目"兼容性碾压式领先的根本原因，也是 `tavotto run`
（native profile）要对齐的语义基准：**与其在受控 worker 里逐条修补环境
差异（相对路径回退、argv 替换、sys.path 注入……），不如提供一条"按用户
原来的方式跑"的正式路径**。差别在于 Tavotto 不能要求用户改脚本加
`import tavotto`，所以 native 模式由 Tavotto 侧组装出等价的 invocation
（解释器 / cwd / argv / env 原样），捕获仍走注入的 sitecustomize/驱动层
（Session 7 设计）。

### 4. Artist 引用（`change_tracker.py::getReference`）

`getReference(element)` 把一个 Artist 变成**一段可求值的 Python 代码串**，
按容器下标层层拼出来：

```text
Figure   → "plt.figure(1)"（或全局变量名，setFigureVariableNames 反查）
SubFigure→ parent + ".subfigs[i]"
Line2D   → axes_ref + ".lines[i]"
Collection → axes_ref + ".collections[i]"
Patch    → axes_ref / figure_ref + ".patches[i]"
Text     → ".texts[i]" / ".title" / "._left_title" / 轴标签
           ".get_xaxis().get_label()" / 逐个刻度
           ".get_major_ticks()[i].label1" …
Axes     → ".axes[i]"，有 label 时优先 '.ax_dict["label"]'
Legend   → axes_ref + ".get_legend()"
```

与 Tavotto gid（`axes_0.lines_1`）是同构的思路：**身份 = 容器 + 下标**。
差别：

- Pylustrator 的引用要在**用户下次跑脚本时**重新求值成真对象（生成的代码
  块开头先重建 `ax_dict`），所以脚本一改（多画一条线）下标就错位——这是
  它有名的脆弱点。Tavotto 的 gid 每次 build 重新 instrument，同样按下标，
  但 gid 只在"同一脚本产出的同构 figure"里要求稳定，且重放分歧有
  写回自检兜着（`replay_divergence`）。
- Tavotto 已经覆盖了它没有的族（3D、色条、刻度模型、路径几何），
  **这一项没有可吸收的增量**。

### 5. Source location / 原始属性记录

`initialize()` 还包掉 `Axes.text` / `Figure.text`：创建文字时用
`traceback.extract_stack()[-2]` 记下**创建这个元素的源码位置**
（`stack_position`），并抓一份创建时的属性快照（position/fontsize/color…）
挂在 `element._pylustrator_old_values`。change tracker 据此区分
"用户代码本来就有的" 和 "pylustrator 生成的"，也能报告"这个元素来自
第 N 行"。

Tavotto 目前完全没有这一层（override 的 originals 表记录的是**属性原值**，
不是**源码位置**）。这是未来 Session 11（Source Hints）的素材，本轮不做。

### 6. 保存 = 改写用户源码（明确不采用）

`ChangeTracker.save()` 把所有改动序列化成一段 Python 代码
（`sorted_changes()`），包在
`#% start: automatic generated code from pylustrator` / `#% end:` 注释块里，
按 `stack_position`（`pylustrator.start()` 被调用的文件与行号）**直接
insertTextToFile 改写用户的 .py 文件**。下次运行时这段生成代码重放改动。
undo/redo（`addEdit`/`backEdit`/`forwardEdit`）是进程内闭包栈，跨会话
持久化全靠那段生成代码。

Tavotto 不走这条路（override 是全量列表语义 + 写回事务改**产物**而非
源码，源码写回是显式、可验证、可回滚的独立动作）。理由在
`docs/adr/0009-write-back-pixel-verification.md` 与写回事务一节：改写用户
源码的默认行为与"0 次静默源码损坏"的完成定义直接冲突。

## 吸收对照表

| Pylustrator 机制 | Tavotto 现状 | 是否吸收 | clean-room 实现方式 |
|---|---|---|---|
| `plt.show()` 拦截 + Gcf 遍历捕获 live Figure | 已有同源机制：`figcapture.collect_pyplot_figures`（worker + browser 共用） | 已具备 | 无需新实现；Session 2 把它并进统一 CapturedFigureDescriptor |
| `savefig` 拦截**且透传**（记录保存目标，不阻止写盘） | worker 拦截且**吞掉**（safe 沙盒语义） | 吸收为 native 选项 | `ExecutionSpec.passthrough_savefig`；safe=False，native=True（ADR 0014） |
| 原解释器 / cwd / argv / env / import 天然保留（用户自己跑脚本） | 无：safe worker 沙盒 cwd + argv 替换 + 相对路径只读回退逐条修补 | **吸收（核心）** | `tavotto run` native profile：Tavotto 组装用户原样 invocation，不要求改脚本（Session 7–8，ADR 0014） |
| 多 Figure 不丢（Gcf 有几张开几张） | 引擎已支持（`MAX_PYPLOT_FALLBACK=8` 上限、丢弃有报告）；产品入口对 show-only 脚本不可达 | 吸收（产品层） | RuntimeFigureAsset + 素材库入口（Session 4–5，ADR 0013） |
| `getReference` 代码串式 Artist 引用 | gid 体系已覆盖且更广（3D/色条/刻度模型） | 不吸收 | —（无增量） |
| 创建位置 `stack_position` + 原始属性快照 | 无 | 暂不吸收 | 未来 Session 11（Source Hints）单独设计 |
| 保存 = 生成代码块改写源码 | 写回事务改产物（prepare→verify→commit），源码不动 | **不吸收** | —（与完成定义"0 次静默源码损坏"冲突） |
| Qt GUI / DragManager | 自有前端画布（web + 桌面壳共用一份） | 不吸收 | — |
| `exception_swallower` | 结构化错误码（script_error + traceback） | 不吸收 | — |

## 兼容优势与风险小结

**优势**（全部来自"寄生在用户自己的进程里"）：零环境差异、零发现问题
（不需要 entry/stem/注册表）、多 Figure 天然、任何脚本组织形态都行。

**风险**（Tavotto 必须避开的）：

1. 保存即改源码——脚本被工具生成的代码块污染，块与代码漂移后重放错位；
2. 下标式引用在脚本演化后错位（Tavotto 用每次 build 重新 instrument +
   写回自检缓解同类风险）；
3. 要求用户改脚本（加 `import pylustrator`）——Tavotto 的 native 模式必须
   做到**零脚本改动**；
4. GUI 与执行同进程——脚本死循环 = 界面卡死（Tavotto 的 worker 边界 +
   超时 kill 已经解决这一类）。
