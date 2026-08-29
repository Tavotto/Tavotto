<#
.SYNOPSIS
    大图预览的 Windows 内存基准（issue #181 / ADR 0022 / Session 05 §5）。

.DESCRIPTION
    issue #181 报的那个数是 **WebView2 渲染进程 6.47 GB**。修复之后那一侧到底
    是多少，只有在 Windows 上、用真的 WebView2 量一次才知道——macOS 上的
    headless Chromium 不是同一个渲染引擎，也不是同一个平台，它的读数**不能**
    拿来宣称 §8 的内存验收项已满足。

    这个脚本做四件事：起 Tavotto、打开 #181 fixture、等预览就绪、采样渲染进程，
    并按 open/close 循环重复若干轮看有没有明显线性增长。

    ## 三条纪律

    1. **采样失败不许弄坏 Tavotto**（Session 05 §5）。拿不到计数器就报 `null`
       并继续——一个量不到内存的基准脚本是个坏基准，一个因此让应用崩掉的
       基准脚本是个 bug。
    2. **区分「没有 WebView2」与「WebView2 用了 0 字节」**。前者是 `available:
       false`，后者是 `0`。把它们合成一个值，读数据的人会得出完全相反的结论。
    3. **不做 CI hard gate**。绝对内存在托管 runner 上会飘（§6），随机红的门禁
       最后一定会被人忽略掉，比没有门禁更坏。结构性 DOM 预算那条闸在
       `web/e2e/large-figure.spec.ts`，那条才进 CI。这里出的是 JSON 产物。

.PARAMETER Exe
    Tavotto 可执行文件。不给就用 `python -m tavotto`（见 -Python）。

.PARAMETER Python
    退路解释器，默认 `python`。

.PARAMETER Cycles
    open/close 轮数，默认 5（Session 05 §7）。

.PARAMETER MeshN
    fixture 的 mesh 边长（`TAVOTTO_ISSUE181_MESH_N`），默认 470 = #181 的原始量级。

.PARAMETER Out
    JSON 落盘路径，默认 `bench-large-preview-windows.json`。

.NOTES
    **本脚本尚未在 Windows 上执行过**（写它的机器是 macOS，本机没有
    PowerShell，连语法都只做了静态审阅）。第一次在 Windows 上跑时按
    「每一步都是第一次执行」预期：路径分隔符、进程名、计数器可用性都可能
    当场失败。它进仓库是为了让那第一次有东西可跑，不是因为它被验过。
#>
[CmdletBinding()]
param(
    [string]$Exe,
    [string]$Python = 'python',
    [int]$Cycles = 5,
    [int]$MeshN = 470,
    [string]$Out = 'bench-large-preview-windows.json'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot

function Get-FreePort {
    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    return $port
}

<#
    渲染进程的采样。**主语必须是「我们这一次起的那些进程」**，不是机器上所有
    的 WebView2——用户自己的 Edge 也是 msedgewebview2.exe，把它们加起来会得到
    一个稳定、可复现、量纲正确、唯独与被测对象无关的数字（macOS 上做同一件事
    时第一版就量到了 5.7 GB，那是整台机器所有的 Chromium）。

    做法：按父进程链找出 $RootPid 的后代，再在其中挑渲染进程。
#>
function Get-DescendantIds {
    param([int]$RootPid)
    $all = Get-CimInstance Win32_Process -Property ProcessId, ParentProcessId
    $byParent = @{}
    foreach ($p in $all) {
        if (-not $byParent.ContainsKey($p.ParentProcessId)) { $byParent[$p.ParentProcessId] = @() }
        $byParent[$p.ParentProcessId] += $p.ProcessId
    }
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    $queue = [System.Collections.Queue]::new()
    $queue.Enqueue($RootPid)
    while ($queue.Count -gt 0) {
        $cur = $queue.Dequeue()
        foreach ($child in ($byParent[$cur] | Where-Object { $_ })) {
            if ($seen.Add($child)) { $queue.Enqueue($child) }
        }
    }
    return $seen
}

function Measure-Renderer {
    param([int]$RootPid)
    # 采样失败一律退化成 available:false，**绝不抛**（纪律 1）
    try {
        $ids = Get-DescendantIds -RootPid $RootPid
        $procs = Get-Process -Id $ids -ErrorAction SilentlyContinue |
            Where-Object { $_.ProcessName -match 'msedgewebview2|WebView2|chrome' }
        if (-not $procs) {
            return [ordered]@{
                available = $false
                reason    = 'no_webview2_descendant'
                note      = '这一次运行的进程树里没有 WebView2——不是「用了 0 字节」'
            }
        }
        return [ordered]@{
            available          = $true
            process_count      = @($procs).Count
            working_set_bytes  = [int64](($procs | Measure-Object WorkingSet64 -Sum).Sum)
            private_bytes      = [int64](($procs | Measure-Object PrivateMemorySize64 -Sum).Sum)
            handles            = [int](($procs | Measure-Object HandleCount -Sum).Sum)
            threads            = [int](($procs | ForEach-Object { $_.Threads.Count } |
                                        Measure-Object -Sum).Sum)
        }
    } catch {
        return [ordered]@{ available = $false; reason = 'sampling_failed'; error = "$_" }
    }
}

# --------------------------------------------------------------------------
# fixture 图库：脚本 + registry 就是一个合法项目，数据由 rng(181) 现生成
# --------------------------------------------------------------------------
$work = Join-Path ([System.IO.Path]::GetTempPath()) ("tavotto-bench-" + [guid]::NewGuid())
$figures = Join-Path $work 'figures'
New-Item -ItemType Directory -Force -Path $figures | Out-Null
Copy-Item -Recurse -Force (Join-Path $repo 'tests\fixtures\large_figures\*') $figures

$port = Get-FreePort
$env:TAVOTTO_ISSUE181_MESH_N = "$MeshN"
$env:TAVOTTO_DATA_DIR = Join-Path $work 'data'
$env:TAVOTTO_CONFIG_DIR = Join-Path $work 'config'
$env:TAVOTTO_NO_TELEMETRY = '1'

if ($Exe) {
    $proc = Start-Process -FilePath $Exe -PassThru -WindowStyle Hidden `
        -ArgumentList @('--port', "$port", '--no-browser', '--figures', $figures)
} else {
    $proc = Start-Process -FilePath $Python -PassThru -WindowStyle Hidden `
        -ArgumentList @('-m', 'tavotto', '--port', "$port", '--no-browser', '--figures', $figures)
}

$base = "http://127.0.0.1:$port"
$deadline = (Get-Date).AddSeconds(180)
while ((Get-Date) -lt $deadline) {
    try { Invoke-RestMethod "$base/api/version" -TimeoutSec 5 | Out-Null; break } catch { }
    Start-Sleep -Milliseconds 500
}

$rows = @()
try {
    for ($i = 1; $i -le $Cycles; $i++) {
        # open：真的走一次渲染（含冷 build 那一次）。这里打的是 HTTP 端点而
        # 不是驱动界面——要量的是「预览就绪之后渲染进程占多少」，而端点回来
        # 就意味着 payload 已经交给前端了。
        $t0 = Get-Date
        $panels = Invoke-RestMethod "$base/api/panels" -TimeoutSec 300
        $stem = ($panels.panels | Select-Object -First 1).stem
        $render = Invoke-RestMethod -Method Post "$base/api/engine/render" -TimeoutSec 600 `
            -ContentType 'application/json' `
            -Body (@{ stem = $stem; patches = @() } | ConvertTo-Json)
        $openMs = [int]((Get-Date) - $t0).TotalMilliseconds

        Start-Sleep -Seconds 2   # 让渲染进程把这一帧的内存真的吃下去
        $sample = Measure-Renderer -RootPid $proc.Id

        $rows += [ordered]@{
            cycle        = $i
            open_ms      = $openMs
            preview_mode = $render.preview.mode
            svg_bytes    = $render.preview.svg_bytes
            renderer     = $sample
        }
    }
} finally {
    try { Invoke-RestMethod -Method Post "$base/api/shutdown" -TimeoutSec 30 | Out-Null } catch { }
    Start-Sleep -Seconds 2
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
}

$payload = [ordered]@{
    schema        = 1
    mesh_n        = $MeshN
    cycles        = $Cycles
    os            = [System.Environment]::OSVersion.VersionString
    # **未验证标记**：读这份 JSON 的人有权知道它出自一个从没在 Windows 上
    # 跑通过的脚本，直到第一次真的跑绿为止。
    first_run     = $true
    samples       = $rows
}
$payload | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 $Out
Write-Host "已写入 $Out（$Cycles 轮）"

# 采样不可用时**照样退 0**：这是基准不是门禁（纪律 3）
exit 0
