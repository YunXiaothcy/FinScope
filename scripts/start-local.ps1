[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$Lan,
    [string]$LanAddress,
    [switch]$Preview,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskBackendDirectory = Join-Path $taskRoot 'backend'
$taskFrontendDirectory = Join-Path $taskRoot 'frontend'
$taskPython = Join-Path $taskBackendDirectory '.venv\Scripts\python.exe'
$taskVite = Join-Path $taskFrontendDirectory 'node_modules\vite\bin\vite.js'
$taskNode = (Get-Command node -ErrorAction Stop).Source
if (!(Test-Path -LiteralPath $taskPython) -or !(Test-Path -LiteralPath $taskVite)) { throw '请先按 README 安装前后端依赖。' }
if ($Preview -and !(Test-Path -LiteralPath (Join-Path $taskFrontendDirectory 'dist/index.html'))) { throw '预览前请先在 frontend 运行 npm run build。' }
$taskPort = if ($Preview) { 4174 } else { 5174 }
$taskBind = if ($Lan) { '0.0.0.0' } else { '127.0.0.1' }
$taskBrowserHost = '127.0.0.1'
if ($Lan) {
    $taskAddresses = @(Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
        !$_.SkipAsSource -and $_.AddressState -eq 'Preferred' -and
        ($_.IPAddress -match '^10\.' -or $_.IPAddress -match '^192\.168\.' -or $_.IPAddress -match '^172\.(1[6-9]|2[0-9]|3[01])\.')
    } | Select-Object -ExpandProperty IPAddress -Unique)
    if ($LanAddress) {
        if ($LanAddress -notin $taskAddresses) { throw 'LanAddress 必须是此电脑实际分配的私有 IPv4 地址。' }
        $taskBrowserHost = $LanAddress
    } else {
        if ($taskAddresses.Count -ne 1) { throw "检测到 $($taskAddresses.Count) 个私有地址，请用 -LanAddress 指定当前 Wi-Fi/以太网 IPv4：$($taskAddresses -join ', ')" }
        $taskBrowserHost = $taskAddresses[0]
    }
}
$taskOrigins = 'http://127.0.0.1:5174,http://localhost:5174,http://127.0.0.1:4174,http://localhost:4174,http://127.0.0.1:8001'
if ($Lan) { $taskOrigins += ",http://${taskBrowserHost}:5174,http://${taskBrowserHost}:4174" }
$taskBackendListener = @(Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue)
$taskFrontendListener = @(Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue)
$taskProcesses = @()
foreach ($taskListener in ($taskBackendListener + $taskFrontendListener)) {
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($taskListener.OwningProcess)"
    $taskIsBackend = $taskListener.LocalPort -eq 8001 -and $taskProcess.CommandLine.Contains($taskBackendDirectory) -and $taskProcess.CommandLine.Contains('uvicorn app.main:app')
    $taskIsFrontend = $taskListener.LocalPort -eq $taskPort -and $taskProcess.CommandLine.Contains($taskVite)
    if (!$taskIsBackend -and !$taskIsFrontend) { throw "端口 $($taskListener.LocalPort) 被无法确认归属的进程占用，本脚本不会停止它。请先关闭对应开发服务。" }
    if (!$Restart) { throw 'FinScope 已运行。需要切换启动模式时添加 -Restart；有未完成提交时请先核对。' }
    $taskProcesses += $taskProcess.ProcessId
}
Write-Output "项目：$taskRoot"
Write-Output "访问：http://${taskBrowserHost}:$taskPort/"
Write-Output "模式：$(if ($Preview) { '生产构建预览 / 在线 PWA' } else { 'Vite 开发 / 不注册 PWA' })"
if (!$PSCmdlet.ShouldProcess($taskRoot, "启动本地服务，前端 $taskPort、后端仅 127.0.0.1:8001")) { return }
foreach ($taskProcessId in ($taskProcesses | Select-Object -Unique)) { Stop-Process -Id $taskProcessId -ErrorAction Stop }
$taskLogDirectory = Join-Path $taskRoot '.local'
New-Item -ItemType Directory -Path $taskLogDirectory -Force | Out-Null
$taskPreviousOrigins = $env:FINSCOPE_ALLOWED_ORIGINS
$taskRun = Get-Date -Format 'yyyyMMdd-HHmmssfff'
try {
    $env:FINSCOPE_ALLOWED_ORIGINS = $taskOrigins
    Start-Process -FilePath $taskPython -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8001','--no-proxy-headers' -WorkingDirectory $taskBackendDirectory -RedirectStandardOutput (Join-Path $taskLogDirectory "backend-$taskRun.out.log") -RedirectStandardError (Join-Path $taskLogDirectory "backend-$taskRun.err.log") -WindowStyle Hidden | Out-Null
    $taskArguments = @(('"' + $taskVite + '"'))
    if ($Preview) { $taskArguments += 'preview' }
    $taskArguments += @('--host', $taskBind, '--port', "$taskPort", '--strictPort')
    Start-Process -FilePath $taskNode -ArgumentList $taskArguments -WorkingDirectory $taskFrontendDirectory -RedirectStandardOutput (Join-Path $taskLogDirectory "frontend-$taskPort-$taskRun.out.log") -RedirectStandardError (Join-Path $taskLogDirectory "frontend-$taskPort-$taskRun.err.log") -WindowStyle Hidden | Out-Null
} finally {
    $env:FINSCOPE_ALLOWED_ORIGINS = $taskPreviousOrigins
}
Write-Output '已后台启动。局域网模式需手机与电脑同一网络，且防火墙允许所选前端端口；不自动修改防火墙。'
