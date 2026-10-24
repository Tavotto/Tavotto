---
name: windows-lab
description: 在常驻 Windows Lab（腾讯云 Windows Server 2025）上验证 Tavotto 的 Windows 问题——用户说「去 Windows Lab 验证」「在 Windows 上跑一下」「Windows 真机试试」，或改动涉及 Windows 路径 / 编码 / 进程 / 打包时用。只经 scripts/winlab/winlab.py，不直接 ssh。
---

# Windows Lab

规则全文 `docs/rules/ci/windows-lab.md`，运维 `docs/ci/windows-lab.md`——动手前读规则那份。

1. 先 `python3 scripts/winlab/winlab.py doctor`：`remote.result` 不是 passed 就停下，报告哪一项，
   不要绕。连不上（本机没有 `tavotto-win-lab` 别名 / 云端会话没有本机密钥）就说明 Lab 不可用。
2. 选任务（`agent.py` 的 `TASKS` 是闭集，`winlab.py test -h` 看全）：
   - 指定用例：`test pytest --tests tests/test_x.py::name --ref HEAD`
   - 渲染 / 导出 / 保存重载：`test render-roundtrip --ref HEAD`
   - 全量分片（慢，约同 CI）：`test pytest-shard --shard N/4 --ref HEAD`
   - 网络：`network --mode all`；AI 宿主 L2/L3：`test hosts --ref HEAD`
   - 真宿主 L4/L5（真 Claude Code + 国内模型，花模型费用，先问用户）：`test hosts-live`
   `--ref` 会先过可信度检查再同步代码：只有 origin/main 或你本人提交的分支能上 Lab。
3. 长任务用后台运行，结论以输出 JSON 的 `result` 为准；不是 passed 就读 `summary` / `counts`，
   需要原始日志时说明它在 Lab 的 `C:\TavottoLab\runs\<run_id>\evidence\`。
4. 失败先分清产品缺陷还是 Lab 环境问题（`doctor`、`environment` 字段）；修产品就按
   `.github/AGENTS.md` 先写 `tests/test_windows_regressions.py` 用例，再修，再回 Lab 复验。
   不许降低断言、删用例、加 skip 让它变绿。
5. 向用户汇报时附 run id、提交、`result`，并照抄报告里的 `unverified`。

不做：`admin …` 动词（要用户在场）、改 Lab 的系统配置、经代理下载大文件、碰 DMIT。
