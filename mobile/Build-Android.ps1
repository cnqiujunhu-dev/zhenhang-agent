param([string]$Python = '', [switch]$CheckOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath($PSScriptRoot)
if ($projectRoot -match '[^\x00-\x7f]') {
    throw '请使用英文构建路径，例如 D:\ZhenxingSubmission\mobile。'
}
$tempPath = Join-Path $projectRoot 'var\temp'
$cachePath = Join-Path $projectRoot 'var\npm-cache'
foreach ($path in @($tempPath, $cachePath, (Join-Path $projectRoot 'var\android-user'),
        (Join-Path $projectRoot 'var\avd'), (Join-Path $projectRoot 'var\android-build\compat'))) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}
$env:TEMP = $tempPath
$env:TMP = $tempPath
$env:NPM_CONFIG_CACHE = $cachePath
$env:NPM_CONFIG_UPDATE_NOTIFIER = 'false'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:ANDROID_USER_HOME = Join-Path $projectRoot 'var\android-user'
$env:ANDROID_EMULATOR_HOME = $env:ANDROID_USER_HOME
$env:ANDROID_AVD_HOME = Join-Path $projectRoot 'var\avd'
$env:GRADLE_USER_HOME = Join-Path $projectRoot 'var\gradle-cache'
$env:JAVA_TOOL_OPTIONS = '-Djava.io.tmpdir=' + $tempPath + ' -Duser.language=en -Dfile.encoding=UTF-8'

if (-not $Python) {
    $candidate = Join-Path (Split-Path $projectRoot -Parent) 'app\.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $candidate) { $Python = $candidate }
    else {
        $command = Get-Command python -ErrorAction SilentlyContinue
        if ($command) { $Python = $command.Source }
    }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) { throw '未找到Python，请通过 -Python 指定已有解释器。' }
$node = Get-Command node -ErrorAction SilentlyContinue
$java = Get-Command java -ErrorAction SilentlyContinue
if (-not $node -or -not $java) { throw '需要已有Node.js和JDK 21，请先准备构建环境。' }
$npmCli = Join-Path (Split-Path $node.Source -Parent) 'node_modules\npm\bin\npm-cli.js'
if (-not (Test-Path -LiteralPath $npmCli)) { throw '当前Node环境缺少npm。' }
$jdk = Split-Path $java.Source -Parent
if (-not (Test-Path -LiteralPath (Join-Path $jdk 'javac.exe'))) { throw '需要JDK，当前Java环境缺少javac。' }

Push-Location $projectRoot
try {
    $toolchain = Join-Path $projectRoot 'var\android-build\toolchain'
    $buildTools = @(Get-ChildItem -LiteralPath (Join-Path $toolchain 'build-tools') -Filter 'aapt2.exe' -Recurse -File -ErrorAction SilentlyContinue)
    $platform = @(Get-ChildItem -LiteralPath (Join-Path $toolchain 'platform') -Filter 'android.jar' -Recurse -File -ErrorAction SilentlyContinue)
    if (-not $buildTools.Count -or -not $platform.Count) {
        if ($CheckOnly) { throw 'Android构建组件尚未准备，请运行不带 -CheckOnly 的构建命令。' }
        Write-Host '准备Android官方构建组件，下载文件保存在本工程内。'
        & $Python (Join-Path $projectRoot 'android-client\setup_toolchain.py')
        if ($LASTEXITCODE -ne 0) { throw 'Android工具准备失败。' }
    }
    $compat = Join-Path $projectRoot 'var\android-build\compat'
    $versions = @{'@noble/hashes'='2.0.1'; 'rolldown'='1.2.11'}
    $needInstall = $false
    foreach ($name in $versions.Keys) {
        $metadata = Join-Path $compat ('node_modules\' + $name + '\package.json')
        if (-not (Test-Path -LiteralPath $metadata)) { $needInstall = $true; continue }
        $current = Get-Content -LiteralPath $metadata -Raw | ConvertFrom-Json
        if ($current.version -ne $versions[$name]) { $needInstall = $true }
    }
    if ($needInstall) {
        if ($CheckOnly) { throw '兼容脚本依赖尚未准备，请运行不带 -CheckOnly 的构建命令。' }
        Write-Host '准备固定版本的JavaScript兼容脚本依赖。'
        & $node.Source $npmCli install --prefix $compat --save-exact '@noble/hashes@2.0.1' 'rolldown@1.2.11'
        if ($LASTEXITCODE -ne 0) { throw 'JavaScript依赖安装失败。' }
    }
    $frontendPath = Join-Path $projectRoot 'web'
    $frontendOutput = Join-Path $projectRoot 'android-client\assets\web'
    foreach ($name in @('package.json', 'package-lock.json', 'vite.config.ts', 'src\main.tsx')) {
        if (-not (Test-Path -LiteralPath (Join-Path $frontendPath $name))) {
            throw "缺少本工程网页源码 web\$name。请先准备独立网页副本。"
        }
    }
    if ($CheckOnly) {
        foreach ($name in @('react', 'vite', 'typescript')) {
            if (-not (Test-Path -LiteralPath (Join-Path $frontendPath "node_modules\$name\package.json"))) {
                throw '网页构建依赖尚未准备，请运行不带 -CheckOnly 的构建命令。'
            }
        }
        Write-Host '构建环境已就绪，工具、依赖和缓存位于本工程var目录；未重新生成APK。' -ForegroundColor Green
        return
    }
    Write-Host '安装本工程锁定的网页依赖，npm缓存位于本工程var目录。'
    & $node.Source $npmCli ci --prefix $frontendPath --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw '网页依赖安装失败。' }
    & $node.Source $npmCli --prefix $frontendPath run build -- --outDir $frontendOutput
    if ($LASTEXITCODE -ne 0) { throw '网页构建失败。' }
    if (-not (Test-Path -LiteralPath (Join-Path $frontendOutput 'index.html'))) {
        throw '网页没有输出到android-client/assets/web，停止打包。'
    }
    & $node.Source (Join-Path $projectRoot 'android-client\build_compat.mjs')
    if ($LASTEXITCODE -ne 0) { throw '网页兼容脚本构建失败。' }
    & $Python (Join-Path $projectRoot 'android-client\build_apk.py')
    if ($LASTEXITCODE -ne 0) { throw 'APK构建失败。' }
    Write-Host 'APK已输出到dist。请完成安装验收后再打发行包。' -ForegroundColor Green
} finally { Pop-Location }
