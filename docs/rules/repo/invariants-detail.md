# 跨仓库不变量：展开

> 原文出自根 `AGENTS.md`「不可破坏的跨仓库不变量」（2026-10-24 根文件瘦身时迁出，正文逐字未改）。
> 这里是这一主题的**唯一全文**；根 `AGENTS.md` 只留一句索引。改规则改这里，并同步那一句。

- **安全边界**：会话认证（ADR 0008）不许被任何新端点绕过；worker 沙盒与
  `Path.unlink` 守卫不放松（safe 档的 cwd 可按项目显式切到脚本目录，ADR 0047——
  守卫原样，变的只是相对路径写到哪，且要用户按项目确认；项目用的是用户自己的 Python 时默认即脚本目录，
  那是他「像终端里那样跑」的意思表示，ADR 0107）。
- **渲染闭包零 PyMuPDF（ADR 0072，2026-09-22 起）**：Tavotto 的应用 / 发行 / runtime 闭包里
  没有 pymupdf / fitz——PDF 的读、写、栅格全经 `pdfbackend/` 契约层走 `rendercore/`
  （pikepdf / HarfBuzz / PDFium render child 与批准字体）。看护是 `scripts/ci/retirement_scan.py`
  的五把尺子（应用源码 AST / 声明依赖闭包 / 干净进程阻断器 / 产物 native 名单 + wheel METADATA /
  SBOM，`tests/test_retirement_scan.py` + package / exe smoke 那三步）；主语是闭包不是硬盘：
  测试的独立读取器、`scripts/dev/`、用户科学脚本自己的 fitz 都不算残留。`TAVOTTO_RENDER_BACKEND=pymupdf`
  是明确的 `backend_retired` 错误，不是回退。
- **1.0 收敛纪律**（退出条件与缺陷分级见 `docs/1.0-release-readiness.md`）：
  除非 correctness / safety / compatibility / release blocker，禁止扩大产品
  能力，禁止趁机重写已稳定模块。新增核心不变式测试提交前必须手工反证一次
  （空门禁比没有门禁更坏）。统一实施包（`docs/implementation/tavotto-foundation/`）的
  RenderCore / 首开两条主线是**登记例外**（ADR 0053/0072；全表见 1.0-release-readiness §1.8）：
  它们替换的是渲染闭包与环境准备，按实施包的 registry / enrollment 逐项取得资格，不是「趁机重写」。
