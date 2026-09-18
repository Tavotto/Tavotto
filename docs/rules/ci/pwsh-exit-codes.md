# pwsh 步骤的退出码（issue #197）

> 原文出自 `.github/AGENTS.md`「pwsh 步骤的退出码（issue #197，2026-09-14 查清）」（2026-09-18 指导文档治理时迁出，正文逐字未改）。
> 这里是这一主题规则的**唯一全文**；`.github/AGENTS.md` 只留速查行。改规则改这里，并同步那一行。

- **pwsh / powershell 步骤里，最后一条原生命令故意非零退出（「期望用法错误退 2」
  那类判据）时，脚本必须以显式 `exit 0`（或 `$global:LASTEXITCODE = 0`）结尾。**
  否则断言全过、最后一行 `✓` 都打印了，步骤仍然退 1——不是 PowerShell 抛了什么，
  是两层机制叠在一起：
  * runner 会**改写**每个 pwsh 步骤的脚本：前置 `$ErrorActionPreference = 'stop'`，
    **后置** `if ((Test-Path -LiteralPath variable:\LASTEXITCODE)) { exit $LASTEXITCODE }`
    （actions/runner `src/Runner.Worker/Handlers/ScriptHandlerHelpers.cs` 的
    `FixUpScriptContents`）。你写的最后一行之后还有它这一行在跑，`$LASTEXITCODE`
    是哪条命令留下的它不管。
  * 步骤由 `pwsh -command ". '{0}'"` 起（同一文件的 `_defaultArguments`；Windows
    runner 未指定 `shell` 时默认就是 pwsh），而 `-Command` 会把非 0/1 的退出码折成 1
    （about_pwsh「-Command | -c」一节：「…an exit code other than 0 or 1, that exit
    code is converted to 1 for process exit code」）——所以看见的永远是 1，不是那个 2。
  显式 `exit 0` 在追加的那一行**之前**退出；失败路径全是 `throw`（Stop → 1），
  走不到它，所以不掩盖任何真失败。`ErrorRecord` 转换与
  `$PSNativeCommandUseErrorActionPreference` 都与此无关（2026-08-29 两轮实测证伪）。
  现有两处：ci.yml `windows-exe-smoke` 的「console 版 CLI」步骤、release-publish.yml
  （`n1_update_windows`，F.2 之前在 release.yml）的更新链验证步骤。
