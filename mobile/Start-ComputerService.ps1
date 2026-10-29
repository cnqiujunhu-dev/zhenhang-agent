param(
    [string]$ServiceRoot = (Join-Path (Split-Path $PSScriptRoot -Parent) 'app'),
    [string]$Address = '',
    [int]$Port = 18969,
    [int]$BackendPort = 18965,
    [string]$Python = '',
    [ValidatePattern('^[a-z][a-z0-9_-]+$')][string]$ProjectName = 'zhenxing_submission',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$mobileRoot = [IO.Path]::GetFullPath($PSScriptRoot)
$ServiceRoot = [IO.Path]::GetFullPath($ServiceRoot)
$tempPath = Join-Path $mobileRoot 'var\temp'
New-Item -ItemType Directory -Force -Path $tempPath | Out-Null
$env:TEMP = $tempPath
$env:TMP = $tempPath
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'

function Test-BackendReady {
    try {
        $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$BackendPort/api/v2/health/ready")
        $request.Proxy = $null
        $request.Timeout = 3000
        $response = $request.GetResponse()
        try {
            $reader = [IO.StreamReader]::new($response.GetResponseStream())
            try { $data = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
            return $data.status -eq 'ready'
        } finally { $response.Close() }
    } catch { return $false }
}
function Test-PrivateAddress([string]$Value) {
    try { $bytes = [Net.IPAddress]::Parse($Value).GetAddressBytes() } catch { return $false }
    if ($bytes.Length -ne 4) { return $false }
    return $bytes[0] -eq 10 -or ($bytes[0] -eq 192 -and $bytes[1] -eq 168) -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31)
}
if ($Port -lt 1024 -or $Port -gt 65535 -or $BackendPort -lt 1 -or $BackendPort -gt 65535) { throw '端口无效。' }
$candidates = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.AddressState -eq 'Preferred' -and (Test-PrivateAddress $_.IPAddress) -and
        $_.InterfaceAlias -notmatch 'vEthernet|Loopback|Docker|WSL|Bluetooth' })
if (-not $Address) {
    foreach ($route in @(Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Sort-Object RouteMetric)) {
        $match = @($candidates | Where-Object { $_.InterfaceIndex -eq $route.InterfaceIndex })
        if ($match.Count) { $Address = $match[0].IPAddress; break }
    }
    if (-not $Address -and $candidates.Count) { $Address = $candidates[0].IPAddress }
}
if (-not (Test-PrivateAddress $Address) -or -not (Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Address -ErrorAction SilentlyContinue)) {
    throw '请连接可信局域网，或用 -Address 指定本机私有IPv4地址。'
}
if (-not (Test-BackendReady)) {
    if ($CheckOnly) { throw "电脑服务尚未就绪：http://127.0.0.1:$BackendPort" }
    $compose = Join-Path $ServiceRoot 'compose.yaml'
    if (-not (Test-Path -LiteralPath $compose) -or -not (Test-Path -LiteralPath (Join-Path $ServiceRoot '.env'))) {
        throw '请先按 docs/部署说明.md 生成配置并构建电脑服务。'
    }
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw '需要已启动的Docker Engine或Docker Desktop。' }
    & docker compose --project-directory $ServiceRoot -f $compose -p $ProjectName up -d --no-build --pull never
    if ($LASTEXITCODE -ne 0) { throw '未能启动现有构建，请先按部署说明构建并检查Docker。' }
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        if (Test-BackendReady) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw '服务尚未就绪，请检查API、Worker和数据库健康状态。' }
}
if (-not $Python) {
    $localPython = Join-Path $ServiceRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $localPython) { $Python = $localPython }
    else { $found = Get-Command python -ErrorAction SilentlyContinue; if ($found) { $Python = $found.Source } }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) { throw '请准备Python，或通过 -Python 指定解释器。' }
$url = "http://${Address}:$Port"
$state = [ordered]@{ address = $url; backend = "http://127.0.0.1:$BackendPort"; backend_ready = $true; check_only = [bool]$CheckOnly }
[IO.File]::WriteAllText((Join-Path $mobileRoot 'var\connection.json'), ($state | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
Write-Host "手机连接地址：$url" -ForegroundColor Cyan
if ($CheckOnly) { Write-Host '检查完成。'; exit 0 }
Write-Host '保持本窗口打开。按Ctrl+C仅停止局域网入口，后端数据保留。'
& $Python (Join-Path $mobileRoot 'android-client\lan_gateway.py') --host $Address --port $Port --upstream $BackendPort
if ($LASTEXITCODE -ne 0) { throw '局域网入口启动失败，请检查IP与端口占用。' }
