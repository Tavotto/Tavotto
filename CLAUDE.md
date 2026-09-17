# Tavotto — 开发约定（入口）

本文件只是入口。仓库级规则的唯一权威是根 `AGENTS.md`（任务路由、跨仓库
不变量、严格同源对、验证命令、子系统索引）——**动手前先 Read 它**，不在
这里复制一份，避免两份文档长期漂移。

- 用户要求安装/运行/试用 Tavotto 而未要求改源码：按 AGENTS.md 的任务路由，
  只读 README 的「在 Codex 中第一次使用 Tavotto」，绝不构建仓库。
- 修改任何子系统之前：按根 `AGENTS.md` 的子系统索引 Read 最近目录的
  `AGENTS.md`（`src/tavotto/` / `web/` / `src-tauri/` / `workerd/` /
  `packaging/` / `codex-plugin/` / `.github/`）。后端与前端那两份是**速查表**：
  按改动路径找到主题，再 Read 它指向的 `docs/rules/<层>/<主题>.md` 细则与
  `docs/adr/` 里对应的架构决策——全文在细则里（进程边界、协议、写回事务、
  同源对……），跳过它们直接改代码等于蒙着眼改。
- 产品名 **Tavotto**；品牌常量唯一出处 `web/src/lib/brand.ts` /
  `engine/brand.py`，界面与导出格式不得手写产品名。
