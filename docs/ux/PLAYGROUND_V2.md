# Playground V2：从「上传一个脚本」到「亲手改一张真实科研图」

状态：已实施（2026-08-25）。相关：ADR 0011（案例优先决策）、ADR 0007
（浏览器 playground 的技术边界，本轮**一条没动**）。

## 一、修改前审计

截图（`docs/ux/playground-v2/before/`，重拍：`cd web &&
node scripts/capture-playground.mjs ../docs/ux/playground-v2/before
--states=idle,loading,editor`）：

| 状态 | 文件 |
|---|---|
| idle 中文 1440×900 | `before/idle-zh-1440x900.png` |
| idle 英文 1440×900 | `before/idle-en-1440x900.png` |
| idle 1920×1080 / 1366×768 / 1024×768 | `before/idle-zh-*.png` |
| idle iPad 768×1024 / iPhone 390×844 | `before/idle-zh-768x1024.png` 等 |
| idle reduced-motion | `before/idle-zh-1440x900-reduced-motion.png` |
| loading | `before/loading-zh-1440x900.png` |
| editor（真 Pyodide 跑完 kinetics） | `before/editor-zh-1440x900.png` |

### 现状与问题

* **上传拖放区是第一视觉主角**：约 780×300px 的虚线大框占据首屏正中，
  示例入口在其下方——一颗填色按钮加两个纯文字链接。视觉权重完全倒挂于
  真实转化路径：绝大多数第一次访客**手边没有一个能在单文件沙盒里跑通的
  `.py`**。真实科研脚本几乎都要 `read_csv` / `np.load` / 同目录模块 /
  本地字体，浏览器沙盒拿不到这些，于是第一入口的典型旅程是：
  选脚本 → 下载 Pyodide → 执行 → `missing_file` → 失败。
  用户学到的是错误结论：「Tavotto 不能处理我的图」。
* **示例不可见**：三个示例只有名字（「折线图」「散点 + 拟合」），没有
  Figure 封面，没有说明，访客无从知道点开是什么、能编辑什么、要等多久。
* **看到示例前的操作数**：0 次操作可看到示例*文字*，但看到*真实图形*
  必须先点击并等完整条 Pyodide 加载链。案例不可预览。
* **上传失败后的恢复路径弱**：失败页只有「换一个脚本」+「下载 Tavotto」，
  不引导去试内置示例——失败的用户正是最需要一条 30 秒成功路径的人。
* **上传的能力边界事后才说**：`figure.py` 的示意与「拖入一个 Matplotlib
  脚本」的措辞暗示「什么脚本都行」；单文件限制只在页脚一行小字。
* **编辑成功后没有「啊哈时刻」的收束**：进了编辑器后用户要自己猜能点什么；
  改完了也没有任何东西把「图变了 + 源文件一个字节没动」这两件事放到一起
  讲给他听（完整性徽章存在，但是一枚 11px 的角落状态，不是叙事）。
* **加载过程本身是好的**（真话阶段列表，无假进度），保留。
* **桌面版边界**：页脚说了「带数据文件的项目请用桌面版」，但用户在失败前
  不会读页脚。

### 结论：重新定义主使命

Playground 不是「完整科研项目的在线兼容性测试器」，而是**让访客在半分钟内
亲手体验 Tavotto 核心能力的交互式产品演示**。主路径应当是：

看到真实科研图案例 → 查看画出它的普通 Matplotlib 代码 → 启动（拖入中央
试验台或一次点击）→ 真实 Pyodide 执行 → 点击标题 → 亲手改字号 →
图变了、`kinetics.py` 一个字节没动（真 SHA-256 证明）→ 下载桌面版
处理自己的完整项目。

上传单文件脚本**保留但降级为次级入口**，并在上传前说清单文件边界。

## 二、技术真实性边界（本轮不许越过）

全部保留、零改动：真 Pyodide / 真 matplotlib / 真 Tavotto browser engine /
`startSession()` / `openSource(filename, source)` / 真实执行 / 真 manifest /
真 SVG / 语义选择 / override / 重渲染 / undo·redo / SHA-256 完整性验证 /
源码零上传 / 超时与 Worker 销毁 / 错误分诊 / idle prewarm / saveData 不预热 /
中英文 / 桌面版出口。

明令禁止且未发生：预烤 manifest、静态 SVG 假执行、跳过 Pyodide 的动画、
静态截图假编辑、前端伪 override、假「源码未修改」、代用户完成引导任务、
源码上传或持久化。**案例卡片封面是唯一的预生成资产，只用于首屏展示；
启动案例走的仍是 `openSource(example.filename, example.source)` 真实执行。**

## 三、新的信息架构

```
顶部导航（品牌回站 · 语言 · 下载桌面版）
标题：挑一张图，亲手改一次。
副题：这些都是普通的 Matplotlib 脚本。运行和编辑都在你的浏览器里完成。

案例库（左，≥1280）            中央试验台（右）
┌────────────┐                ┌──────────────────────┐
│ 反应动力学  │      →         │  把案例拖到这里        │
│ 校准曲线    │                │  或点击「开始体验」     │
│ 吸收光谱    │                └──────────────────────┘
└────────────┘

已有一个独立脚本？（次级，compact 上传入口 + 支持范围 disclosure）
桌面版说明
```

* <1280px：案例网格在上、试验台随之简化；<768px 单列卡片、点击为唯一
  入口、试验台退化为提示行。
* 案例卡片：真实 Figure 封面（构建期真执行生成的 webp）+ 名称 + 一句说明 +
  可编辑对象提示 + 「查看代码」/「开始体验」。
* 「查看代码」= Code Sheet（Dialog 形态的大代码页：行号、只读、复制、
  「用这个案例开始」）。不引入 Monaco，不做在线编辑器——产品主叙事是
  **直接改 Figure、代码仍是源头**。
* 启动路径五条等价：拖拽到试验台 / 卡片「开始体验」/ 卡片聚焦 Enter /
  Code Sheet 内启动 / 触屏点击。拖拽是增强，不是门槛。

## 四、案例资产生成方式

* 源码唯一真源：`web/src/playground/examples/{kinetics,calibration,spectrum}.py`
  ——`examples.ts` 用 vite `?raw` import 读同一份文件；TS 里不再抄第二份。
* 封面：`python scripts/generate_playground_examples.py` 在隔离临时目录、
  Agg backend、**钉死的 matplotlib 版本**（`packaging/playground-runtime.json`
  的 `packages.matplotlib`，与浏览器端同版本）下真实执行每个 `.py`，把真实
  Figure 存成 `web/src/playground/generated/<id>.webp`，并把**源码 sha256 +
  封面尺寸**写进 `generated/examples-manifest.json`。
* 防漂移三道闸：① `--check`（改了 .py 没重新生成封面 = 红）；
  ② `examples.test.ts` 用 node:crypto 对 bundle 里的源码重算 sha256 与
  manifest 比对；③ `build_browser_playground.py` 的源码指纹集合纳入
  `examples/*.py` 与 `generated/*`——封面或源码任何一个变了，网站仓库的
  `check-playground` 都会要求重新构建同步。
* 空白封面（像素零方差）、案例产出非恰好一张图、版本不符：生成当场失败。

## 五、动效原则

复用既有 motion 地基（`web/src/lib/motion.ts` + `index.css @theme`，
时长/缓动唯一出处）：hover 与浮层用 `--duration-fast/base`（120/180ms），
Code Sheet 展开 `--duration-slow`（240ms）档。允许：卡片 hover 抬起、
拖起 scale ≤1.03、dropzone 激活、drop 吸附、Sheet 展开、editor 淡入、
完成反馈 settle。禁止：无限浮动、粒子、3D 翻转、弹簧、bounce、自动轮播、
音效。`prefers-reduced-motion` 全程尊重：不缩放不位移，拖放状态只用
边框与文字表达，功能零删减（index.css 有全局兜底，JS 动画走 `tween()`）。

## 六、上传脚本的新定位

* 首页底部「已有一个独立脚本？」区块：一行边界说明（仅适合不依赖本地
  数据、同目录模块或本地资源的单文件脚本）+ 「上传独立脚本」按钮 +
  「查看支持范围」disclosure（适合/不适合清单）+ 桌面版指引。
* 校验链不变：.py 扩展名 / 256KiB / UTF-8 / 隐私说明 / 哈希验证。
* 失败页三出口：返回案例库 / 试试反应动力学 / 下载桌面版。文案诚实区分
  「浏览器沙盒限制」与「Tavotto 能力」。

## 七、状态机变化

`Stage` 保留 `idle/loading/pick/nofigure/edit/failed`；`loading/pick/
nofigure/edit/failed` 增加来源 `origin: {kind:'example', exampleId} |
{kind:'upload'}`——驱动加载页的案例名、编辑器的返回文案（「换一个案例」
vs「加载另一个脚本」）、首次引导（只对内置案例）、失败页的案例推荐。
会话生命周期不变：任何启动先 `teardownSession`，绝不并行两个 Worker；
`sessionRef` 仍是唯一权威。

## 八、修改后截图（`docs/ux/playground-v2/after/`）

| 状态 | 文件 |
|---|---|
| idle 中文/英文 1440×900 | `after/idle-zh-1440x900.png` / `after/idle-en-1440x900.png` |
| idle 1920×1080 / 1366×768 / 1024×768 / iPad / iPhone | `after/idle-zh-*.png` |
| idle reduced-motion | `after/idle-zh-1440x900-reduced-motion.png` |
| 卡片 hover / 拖起 / dropzone active | `after/card-hover.png` / `after/card-dragging.png` / `after/dropzone-active.png` |
| Code Sheet | `after/code-sheet.png` |
| loading（真实阶段） | `after/loading.png` |
| 引导第一步 / 第二步 / 完成 | `after/guided-task-step*.png` / `after/guided-task-completed.png` |
| compact 上传区 | `after/compact-upload.png` |
| missing-file 失败三出口 | `after/failure-missing-file.png` |

人工检查结论：卡片是白底纸面上的真实 Figure 样张（构建期真执行），不是
SaaS 模板；中央试验台一眼可见且 active 态明确说出案例名（文案在台面顶部，
不被拖动中的卡片盖住）；拖与点两条路都在首屏写明；代码页行号 + 高亮可读；
动画全部 ≤240ms、无循环装饰；首屏 5 秒内说清「挑一张图，亲手改一次」；
单文件限制在上传前常驻一行；桌面版出口在页脚与完成面板两处自然出现。

## 九、性能与体积

Playground bundle（`dist-playground/`，gzip 前）：

| | 修改前 | 修改后 | 变化 |
|---|---|---|---|
| 主 JS | ≈866 KiB | 886 KiB | +20 KiB（新组件 + 案例源码 ?raw + 高亮器） |
| CSS | ≈50 KiB | 51 KiB | ≈0 |
| 案例封面（3×webp） | — | 70 KiB | 新增，本地静态、显式宽高防 CLS |
| engine.zip / worker / html | 136 KiB | 136 KiB | 0 |
| 合计 | 1027 KiB（5 文件） | 1117 KiB（8 文件） | +90 KiB |

交互前网络请求：本页静态资源（html/js/css/3 webp）+ idle prewarm 的
Pyodide 核心五条（saveData / slow-2g 下零条，e2e 看护）。首屏不执行任何
Python；打开 Code Sheet 零额外请求（e2e 断言 wheel 零条）；未引入
Monaco / 动画库 / 运行时高亮大包 / 外部字体 / 外部图片。

## 十、验收结果（量化标准逐条）

§三十一的 26 条全部满足，逐条验证方式：

1. 首屏主角是案例库——after 截图 + e2e「首屏视觉主角」断言 ✅
2. 无需上传即可一次点击开始——卡片「开始体验」✅
3. 三个案例真实 Figure 封面（构建期真执行 + sha 绑定）✅
4. 每案例可查看完整代码（Code Sheet）✅
5/6. 拖到试验台可启动 / 点击可启动（e2e 两条路都真跑）✅
7. 拖放不是唯一入口（Enter / 触屏点击 / Code Sheet 共五条）✅
8. 启动后真实执行 Pyodide（黄金路径 10/10）✅
9. 不用预烤 manifest（openSource 真执行，e2e 网络断言）✅
10. 首次任务存在且非阻塞 ✅
11. 完成后看到真实完整性证明（verifySourceIntegrity 结论）✅
12/13. 上传保留但降级、上传前可见单文件限制 ✅
14. missing file / unsupported import 失败可返回案例库 ✅
15. 手机不依赖 drag（e2e 390×844）✅
16. reduced-motion 功能完整（e2e 专项）✅
17/18. 零新增网络泄露 / 零持久化（哨兵测试原样通过）✅
19. 不并行 Worker（launchSeq + onClient 取消路径；teardown 先行）✅
20. 既有安全测试全绿（篡改 / js 逃逸 / 无 Web Crypto / draw_event）✅
21. 中英文无溢出无裸 key（i18n:check + e2e 英文断言）✅
22. 首屏不等 Pyodide（prewarm 排 idle 回调）✅
23. 查看代码不触发 Pyodide（组件测试 + e2e 双看护）✅
24. 封面与源码哈希校验（三道闸）✅
25. 打开页面到启动 = 一次明确选择 ✅

测试结果：web 单元/组件 911 passed；`generate_playground_examples --check`
通过；`playground.spec.ts`（真 CDN Pyodide）10/10；
`playground-landing.spec.ts` 10/10；`pnpm i18n:check` / `pnpm lint` /
`pnpm build`（tsc -b）全绿。
