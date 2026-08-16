# 许可证说明

Magplot 以 **AGPL-3.0-only** 发布，完整条款见仓库根目录 [`LICENSE`](../LICENSE)。

## 为什么是 AGPL

不是偏好，是依赖决定的。渲染与导出用的 [PyMuPDF](https://github.com/pymupdf/PyMuPDF)
由 Artifex 以 **AGPL-3.0** 发布，任何分发链接它的作品只有两条路：同样按 AGPL
发布，或者向 Artifex 购买商业许可。Magplot 选前者。

对绝大多数用户，这没有任何影响：

- 自己用、改、在实验室内部部署 —— 随便，AGPL 不管私下使用。
- 用它排出来的图、导出的 PDF —— **你的作品是你的**，许可证不传染到输出内容。
- 论文里用 Magplot 画图 —— 不需要开源任何东西。

真正受约束的是**分发**：如果你把改过的 Magplot 分发给别人，或者把它架成一个
别人能通过网络访问的服务，那么你要把对应的源码一并提供给这些用户（AGPL 第 5、
13 条）。

## PDF 后端是隔离的

`src/magplot/pdfbackend/` 是全仓库唯一 import PyMuPDF 的地方。边界层
（`pdfbackend/__init__.py`）定义了一组与实现无关的函数——读页面尺寸、栅格化
预览、按布局合成——HTTP 层（`app.py`）只认这些名字，一行 pymupdf 都没有。

这么做是为了让后端可替换。换成许可更宽松的 PDF 库（如 pypdfium2，Apache-2.0 /
BSD-3）只需要照契约新写一个实现模块，上层不用动。届时本项目计划转向
**MPL-2.0 的 open core 模式**。

在那之前，请按 AGPL-3.0-only 理解你的权利与义务。

## 第三方组件

| 组件 | 许可证 | 用途 |
|---|---|---|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) | AGPL-3.0 | PDF 读写、栅格化、矢量合成 |
| [Flask](https://flask.palletsprojects.com/) | BSD-3-Clause | 本地 HTTP 服务 |
| [matplotlib](https://matplotlib.org/) | PSF-based | 渲染 worker（可选依赖） |
| [React](https://react.dev/) / [Vite](https://vite.dev/) / [Tailwind](https://tailwindcss.com/) | MIT | 前端工作台 |

前端依赖的完整清单见 `web/pnpm-lock.yaml`。
