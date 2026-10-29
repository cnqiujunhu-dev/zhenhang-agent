param([string]$Address = '', [int]$Port = 18969)
# Optional, explicit local administrator action. Start-ComputerService never invokes this.
$ErrorActionPreference = 'Stop'
$admin = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $admin.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw '请由用户在管理员PowerShell中运行此可选脚本。普通启动工具不会调用它。'
}
if (-not $Address) {
    $infoPath = Join-Path $PSScriptRoot 'var\connection.json'
    if (-not (Test-Path -LiteralPath $infoPath)) { throw '先运行启动工具，或用 -Address 明确指定本机IP。' }
    $info = Get-Content -LiteralPath $infoPath -Raw | ConvertFrom-Json
    $Address = ([Uri]$info.address).Host
}
$local = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $Address -ErrorAction Stop
$bytes = [Net.IPAddress]::Parse($Address).GetAddressBytes()
$private = ($bytes[0] -eq 10 -or ($bytes[0] -eq 192 -and $bytes[1] -eq 168) -or
    ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31))
if (-not $private -or $local.PrefixLength -lt 16 -or $local.PrefixLength -gt 30 -or $Port -lt 1024 -or $Port -gt 65535) {
    throw '只允许本机私网IPv4、/16至/30局域网子网和1024以上端口。'
}
$network = for ($i = 0; $i -lt 4; $i++) {
    $bits = [Math]::Max(0, [Math]::Min(8, $local.PrefixLength - $i*8))
    $mask = (255 -shl (8-$bits)) -band 255
    $bytes[$i] -band $mask
}
$subnet = ($network -join '.') + '/' + $local.PrefixLength
$name = "ZhenxingApkLocal-$Port-" + $Address.Replace('.', '-')
if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) {
    Write-Host '该本机地址的专用规则已存在。'; exit 0
}
New-NetFirewallRule -Name $name -DisplayName "Zhenxing APK local $Address port $Port" -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $Port -LocalAddress $Address -RemoteAddress $subnet -Profile Any | Out-Null
Write-Host "已允许 $subnet 访问本机 ${Address}:$Port。"
Write-Host "如需移除：Remove-NetFirewallRule -Name '$name'"
