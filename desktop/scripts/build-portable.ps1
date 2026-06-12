param(
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$desktopDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $desktopDir "..")).Path
$packageJsonPath = Join-Path $desktopDir "package.json"
$packageJson = Get-Content -LiteralPath $packageJsonPath -Raw | ConvertFrom-Json
$setExeIconScript = Join-Path $PSScriptRoot "set-exe-icon.mjs"
$sourceIconPath = Join-Path $desktopDir "assets\app-icon.ico"
$repoLicensePath = Join-Path $repoRoot "LICENSE"

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $desktopDir "dist\stream-curator-win32-x64"
}

$outputRoot = [System.IO.Path]::GetFullPath($OutputDir)
$electronDist = Join-Path $desktopDir "node_modules\electron\dist"
$electronExe = Join-Path $electronDist "electron.exe"
$localeKeep = @("en-US.pak", "zh-CN.pak")
$electronPruneFiles = @(
    "dxcompiler.dll",
    "dxil.dll",
    "vk_swiftshader.dll",
    "vk_swiftshader_icd.json",
    "vulkan-1.dll"
)

if (!(Test-Path -LiteralPath $electronExe)) {
    throw "Electron runtime is missing at $electronExe. Run npm install in desktop first."
}

if (Test-Path -LiteralPath $outputRoot) {
    Remove-Item -LiteralPath $outputRoot -Recurse -Force
}

New-Item -ItemType Directory -Path $outputRoot | Out-Null
Copy-Item -Path (Join-Path $electronDist "*") -Destination $outputRoot -Recurse -Force

$localesDir = Join-Path $outputRoot "locales"
if (Test-Path -LiteralPath $localesDir) {
    Get-ChildItem -LiteralPath $localesDir -File | Where-Object { $_.Name -notin $localeKeep } | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Force
    }
}

$defaultAppAsar = Join-Path $outputRoot "resources\default_app.asar"
if (Test-Path -LiteralPath $defaultAppAsar) {
    Remove-Item -LiteralPath $defaultAppAsar -Force
}

$chromiumLicenses = Join-Path $outputRoot "LICENSES.chromium.html"
if (Test-Path -LiteralPath $chromiumLicenses) {
    Remove-Item -LiteralPath $chromiumLicenses -Force
}

foreach ($fileName in $electronPruneFiles) {
    $filePath = Join-Path $outputRoot $fileName
    if (Test-Path -LiteralPath $filePath) {
        Remove-Item -LiteralPath $filePath -Force
    }
}

$appRoot = Join-Path $outputRoot "resources\app"
$appDesktopDir = Join-Path $appRoot "desktop"
New-Item -ItemType Directory -Path $appRoot | Out-Null
New-Item -ItemType Directory -Path $appDesktopDir | Out-Null

Copy-Item -LiteralPath (Join-Path $repoRoot "frontend") -Destination (Join-Path $appRoot "frontend") -Recurse -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "src") -Destination (Join-Path $appRoot "src") -Recurse -Force
if (Test-Path -LiteralPath (Join-Path $desktopDir "assets")) {
    Copy-Item -LiteralPath (Join-Path $desktopDir "assets") -Destination $appDesktopDir -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $desktopDir "main.js") -Destination (Join-Path $appDesktopDir "main.js") -Force
Copy-Item -LiteralPath (Join-Path $desktopDir "preload.js") -Destination (Join-Path $appDesktopDir "preload.js") -Force
if (Test-Path -LiteralPath $repoLicensePath) {
    Copy-Item -LiteralPath $repoLicensePath -Destination (Join-Path $outputRoot "LICENSE.stream-curator.txt") -Force
    Copy-Item -LiteralPath $repoLicensePath -Destination (Join-Path $appRoot "LICENSE.stream-curator.txt") -Force
}

Get-ChildItem -Path $appRoot -Recurse -Directory | Where-Object { $_.Name -eq "__pycache__" } | ForEach-Object {
    Remove-Item -LiteralPath $_.FullName -Recurse -Force
}
Get-ChildItem -Path $appRoot -Recurse -File | Where-Object { $_.Extension -in @(".pyc", ".pyo") } | ForEach-Object {
    Remove-Item -LiteralPath $_.FullName -Force
}

$releasePackageJson = @{
    name = "stream-curator"
    productName = "stream-curator"
    version = [string]$packageJson.version
    description = [string]$packageJson.description
    main = "desktop/main.js"
}
$releasePackageJson | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $appRoot "package.json") -Encoding utf8
Set-Content -LiteralPath (Join-Path $appRoot ".portable-release") -Value "" -Encoding ascii

$readmeLines = @(
    "stream-curator portable build",
    "",
    "External runtime dependencies are still required on this machine:",
    "- Python 3.11 environment with stream-curator installed or PYTHONPATH pointed to bundled src",
    "- bili.exe / zhihu.exe / xhs.exe available, or set STREAM_CURATOR_*_EXECUTABLE env vars",
    "- OPENCODE_API_KEY in the environment",
    "",
    "Included licenses:",
    "- LICENSE = Electron runtime",
    "- LICENSE.stream-curator.txt = stream-curator",
    "",
    "Optional overrides:",
    "- STREAM_CURATOR_PYTHON_EXECUTABLE",
    "- STREAM_CURATOR_RUNTIME_ROOT",
    "- STREAM_CURATOR_BILIBILI_EXECUTABLE",
    "- STREAM_CURATOR_ZHIHU_EXECUTABLE",
    "- STREAM_CURATOR_XIAOHONGSHU_EXECUTABLE"
)
$readmeLines | Set-Content -LiteralPath (Join-Path $outputRoot "README.release.txt") -Encoding utf8

$targetExe = Join-Path $outputRoot "stream-curator.exe"
if (Test-Path -LiteralPath $targetExe) {
    Remove-Item -LiteralPath $targetExe -Force
}
Rename-Item -LiteralPath (Join-Path $outputRoot "electron.exe") -NewName "stream-curator.exe"

if ((Test-Path -LiteralPath $setExeIconScript) -and (Test-Path -LiteralPath $sourceIconPath)) {
    & node $setExeIconScript $targetExe $sourceIconPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to update stream-curator.exe icon."
    }
}

Write-Output "Portable release created at:"
Write-Output $outputRoot
Write-Output ""
Write-Output "Launch with:"
Write-Output $targetExe
