# Fake OpenAI Responses endpoint for the #266 acceptance run (no login, no network).
#
# Codex sends the full tool list the model would see in every /responses request. This server
# records each request body to $OutDir and answers with a minimal SSE stream that ends the turn,
# so "did the Tavotto MCP tools reach the model?" becomes a file we can grep -- no credentials,
# no model call, nothing leaves the machine.
param(
    [int]$Port = 18766,
    [Parameter(Mandatory = $true)][string]$OutDir,
    [int]$Seconds = 240
)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$listener = [System.Net.HttpListener]::new()
$listener.Prefixes.Add("http://127.0.0.1:$Port/")
$listener.Start()
Set-Content -Path (Join-Path $OutDir 'ready') -Value "$PID" -Encoding ascii
$deadline = (Get-Date).AddSeconds($Seconds)
$n = 0
$sse = @(
    'event: response.created'
    'data: {"type":"response.created","response":{"id":"resp_w266"}}'
    ''
    'event: response.output_item.done'
    'data: {"type":"response.output_item.done","output_index":0,"item":{"type":"message","role":"assistant","id":"msg_w266","content":[{"type":"output_text","text":"w266-ok"}]}}'
    ''
    'event: response.completed'
    'data: {"type":"response.completed","response":{"id":"resp_w266","usage":{"input_tokens":1,"input_tokens_details":null,"output_tokens":1,"output_tokens_details":null,"total_tokens":2}}}'
    ''
    ''
) -join "`n"
$bytes = [System.Text.Encoding]::UTF8.GetBytes($sse)
try {
    while ((Get-Date) -lt $deadline) {
        $task = $listener.GetContextAsync()
        while (-not $task.Wait(500)) {
            if ((Get-Date) -ge $deadline) { break }
        }
        if (-not $task.IsCompleted) { break }
        $ctx = $task.Result
        $n++
        $reader = [System.IO.StreamReader]::new($ctx.Request.InputStream, [System.Text.Encoding]::UTF8)
        $body = $reader.ReadToEnd()
        $name = '{0:D3}-{1}.json' -f $n, ($ctx.Request.Url.AbsolutePath -replace '[^A-Za-z0-9]', '_')
        [System.IO.File]::WriteAllText((Join-Path $OutDir $name), $body)
        $ctx.Response.StatusCode = 200
        $ctx.Response.ContentType = 'text/event-stream'
        $ctx.Response.OutputStream.Write($bytes, 0, $bytes.Length)
        $ctx.Response.Close()
    }
}
finally {
    $listener.Stop()
}
