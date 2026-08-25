# ADR 0011：Playground 案例优先——上传降级为次级入口

状态：已实施（2026-08-25）
相关：[0007 浏览器 playground](0007-browser-playground.md)（技术边界，本轮零改动）；
设计全文与前后对照在 `docs/ux/PLAYGROUND_V2.md`。

## 背景

ADR 0007 把 `/try` 的第一入口定为「拖入一个 Matplotlib `.py`」，示例是平级
的第二条路。但真实科研脚本几乎都依赖浏览器沙盒拿不到的东西：本地
CSV / `np.load` / 同目录模块 / 本地字体 / 相对路径 / Conda 里的额外依赖。
于是第一入口的典型旅程是 `选脚本 → 下载 Pyodide → 执行 → missing_file →
失败`，用户学到的是错误结论——「Tavotto 不能处理我的图」，而问题只是
沙盒没拿到完整项目。

## 决策

**Playground 的主使命从「在线跑你的脚本」改为「半分钟内亲手体验语义
改图」。** idle 首屏的主角是案例库：三张构建期真实执行生成的 Figure
封面卡 + 中央试验台；上传降级为页面底部的「已有一个独立脚本？」，
单文件边界在上传**之前**写明。

* **案例源码单一真源**：`web/src/playground/examples/*.py`。前端经 vite
  `?raw` import 读同一份文件；封面由 `scripts/generate_playground_examples.py`
  在钉死的 matplotlib 版本（`packaging/playground-runtime.json`）下真实
  执行生成，manifest 记源码 sha256，`--check` + `examples.test.ts` +
  构建指纹三道闸防漂移。
* **封面只用于卡片展示**。任何一条启动路径（拖入试验台 / 「开始体验」/
  聚焦 Enter / Code Sheet 内启动 / 触屏点击）都走
  `openSource(example.filename, example.source)` → 真 Pyodide 真执行。
  ADR 0007 的「不许预烤 manifest」一个字没松。
* **拖拽是增强不是门槛**：Pointer Events + capture 自实现（三张卡不值一个
  DnD 框架），只认鼠标指针；触屏与键盘走点击/Enter。reduced-motion 下
  不位移不缩放，拖动状态只用边框与文字表达，功能零删减。
* **会话来源进状态机**（`PlaygroundOrigin`：example / upload）：加载页写
  案例名、返回按钮分「换一个案例 / 换一个脚本」、失败页三出口（返回
  案例库 / 试试主推案例 / 桌面版）、首次引导只对内置案例出现。
* **首次引导只观察不代劳**：两步任务（点标题 → 9pt 改 12pt）的完成判据
  全是真实状态——选中 gid、fontsize override 达标、渲染落定、
  `verifySourceIntegrity` 真跑完且 unchanged 才说「一个字也没动」。
* **「查看代码」是只读 Code Sheet**，不是在线编辑器：产品主叙事是直接改
  Figure、代码仍是源头；第一屏同时鼓励改代码会模糊产品差异。语法高亮是
  ~60 行零依赖 tokenizer，不引入 Monaco。

## 不变的边界

Worker 协议、engine、manifest、override schema、document schema、隐私边界
（源码零上传、零持久化、e2e 哨兵）、超时与 Worker 生命周期、prewarm 与
saveData 纪律：全部原样。任何新启动先 `teardownSession`，加载可取消
（`startSession` 的 `onClient` 让取消能 dispose 在途 Worker），绝不并行
两个 Pyodide Worker。

## 验证

`web/src/playground/examples.test.ts`（数据与哈希绑定）、
`components/*.test.tsx`（landing / code sheet / 拖放手势 / loading /
guided task 判据矩阵）、`e2e/playground-landing.spec.ts`（六视口响应式 +
真鼠标拖放 + reduced-motion）、`e2e/playground.spec.ts`（真 CDN Pyodide
黄金路径：案例库 → 查看代码 → 启动 → 点标题 → 改字号 → override →
重渲染 → 真哈希 → 完成提示 → undo → 换一个案例；哨兵/篡改/超时用例
全部保留）。
