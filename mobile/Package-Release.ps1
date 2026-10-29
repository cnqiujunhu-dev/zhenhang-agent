param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$VerifiedApkSha256,
    [string]$OutputPath = 'dist\圳行Agent-安卓本地连接版.zip'
)
# This is an explicit final packaging action; no automatic build/test approval.
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
function Get-LocalSha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
$distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'dist'))
$buildInfo = Get-Content -LiteralPath (Join-Path $distRoot '构建信息.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ([IO.Path]::GetFileName($buildInfo.apk) -ne $buildInfo.apk -or [IO.Path]::GetExtension($buildInfo.apk) -ne '.apk') {
    throw '构建信息中的APK文件名无效。'
}
$apk = Join-Path $distRoot $buildInfo.apk
$actual = Get-LocalSha256 $apk
if ($actual -ne $VerifiedApkSha256.ToLowerInvariant() -or $actual -ne $buildInfo.sha256.ToLowerInvariant()) {
    throw '当前APK与已验收哈希或构建信息不同，请重新核对后封包。'
}
if ($buildInfo.signature_verified -ne $true -or $buildInfo.zip_alignment_verified -ne $true) {
    throw 'APK签名或对齐检查尚未通过。'
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$apkArchive = [IO.Compression.ZipFile]::OpenRead($apk)
try {
    if (-not $apkArchive.GetEntry('assets/web/index.html') -or
            -not @($apkArchive.Entries | Where-Object { $_.FullName -like 'assets/web/assets/*.js' }).Count) {
        throw 'APK尚未包含独立网页构建产物，请重新构建后再封包。'
    }
} finally { $apkArchive.Dispose() }
$output = [IO.Path]::GetFullPath((Join-Path $projectRoot $OutputPath))
if (-not $output.StartsWith($distRoot + '\', [StringComparison]::OrdinalIgnoreCase) -or
        [IO.Path]::GetExtension($output) -ne '.zip') { throw '发行ZIP必须输出在本工程dist目录。' }
if (Test-Path -LiteralPath $output) { throw '目标ZIP已存在。请使用新的 -OutputPath 保存，避免覆盖已有版本。' }

$files = @('README.md','连接说明.md','启动电脑服务.cmd','Start-ComputerService.ps1','Allow-LanConnection.ps1',
    'android-client\lan_gateway.py',('dist\' + $buildInfo.apk),'dist\构建信息.json')
foreach ($relative in $files) {
    $source = Join-Path $projectRoot $relative
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { throw "缺少发行文件：$relative" }
}
$stream = [IO.File]::Open($output, [IO.FileMode]::CreateNew)
try {
    $archive = New-Object IO.Compression.ZipArchive($stream, [IO.Compression.ZipArchiveMode]::Create, $false, ([Text.Encoding]::UTF8))
    try {
        foreach ($relative in $files) {
            [void][IO.Compression.ZipFileExtensions]::CreateEntryFromFile($archive, (Join-Path $projectRoot $relative),
                $relative.Replace('\','/'), [IO.Compression.CompressionLevel]::Optimal)
        }
    } finally { $archive.Dispose() }
} finally { $stream.Dispose() }
$check = [IO.Compression.ZipFile]::OpenRead($output)
try {
    if ($check.Entries.Count -ne $files.Count) { throw 'ZIP条目数不符。' }
    foreach ($entry in $check.Entries) {
        if ($entry.FullName -notin @($files | ForEach-Object { $_.Replace('\','/') })) { throw 'ZIP含未声明文件。' }
        $entryStream = $entry.Open()
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try { $entryHash = ([BitConverter]::ToString($algorithm.ComputeHash($entryStream))).Replace('-', '').ToLowerInvariant() }
        finally { $entryStream.Dispose(); $algorithm.Dispose() }
        $sourceHash = Get-LocalSha256 (Join-Path $projectRoot $entry.FullName)
        if ($entryHash -ne $sourceHash) { throw 'ZIP条目与源文件哈希不一致。' }
    }
} finally { $check.Dispose() }
Write-Host '发行小包已生成：' -ForegroundColor Green
Write-Host $output
[PSCustomObject]@{ Path = $output; SHA256 = (Get-LocalSha256 $output) }
