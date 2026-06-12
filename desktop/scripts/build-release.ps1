param(
    [string]$PythonEnvRoot = "",
    [string]$ZipPath = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$desktopDir = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $desktopDir "..")).Path
$portableScript = Join-Path $PSScriptRoot "build-portable.ps1"
$runtimeBuilderScript = Join-Path $PSScriptRoot "build-python-runtime.py"
$portableDir = Join-Path $desktopDir "dist\stream-curator-win32-x64"

if ([string]::IsNullOrWhiteSpace($PythonEnvRoot)) {
    $PythonEnvRoot = [string]$env:STREAM_CURATOR_BUNDLE_ENV_ROOT
}
if ([string]::IsNullOrWhiteSpace($PythonEnvRoot)) {
    $PythonEnvRoot = "E:\Anaconda3\envs\streamcurator"
}

if ([string]::IsNullOrWhiteSpace($ZipPath)) {
    $ZipPath = Join-Path $desktopDir "dist\stream-curator-release.zip"
}

$pythonEnvRootPath = [System.IO.Path]::GetFullPath($PythonEnvRoot)
$zipOutputPath = [System.IO.Path]::GetFullPath($ZipPath)
$pythonExe = Join-Path $pythonEnvRootPath "python.exe"

if (!(Test-Path -LiteralPath $pythonExe)) {
    throw "Bundled Python env is missing python.exe at $pythonExe"
}

& powershell -ExecutionPolicy Bypass -File $portableScript -OutputDir $portableDir

$appRoot = Join-Path $portableDir "resources\app"
$runtimeRoot = Join-Path $appRoot "runtime"
$bundledEnvRoot = Join-Path $runtimeRoot "streamcurator-env"
$bundledBinRoot = Join-Path $runtimeRoot "bin"

if (Test-Path -LiteralPath $runtimeRoot) {
    Remove-Item -LiteralPath $runtimeRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $bundledBinRoot -Force | Out-Null

Write-Output "Building slim Python runtime from $pythonEnvRootPath"
& $pythonExe $runtimeBuilderScript `
    --source-env-root $pythonEnvRootPath `
    --target-env-root $bundledEnvRoot `
    --repo-root $repoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Slim Python runtime build failed."
}

$wrapperSpecs = @(
    @{ Name = "bili.cmd"; Module = "bili_cli.cli" },
    @{ Name = "zhihu.cmd"; Module = "zhihu_cli.cli" },
    @{ Name = "xhs.cmd"; Module = "xhs_cli.cli" }
)
foreach ($spec in $wrapperSpecs) {
    $wrapperPath = Join-Path $bundledBinRoot $spec.Name
    $wrapperLines = @(
        "@echo off",
        "`"%~dp0..\streamcurator-env\python.exe`" -X utf8 -m $($spec.Module) %*"
    )
    $wrapperLines | Set-Content -LiteralPath $wrapperPath -Encoding ascii
}

$readmeLines = @(
    "stream-curator release bundle",
    "",
    "Included in this zip:",
    "- Electron desktop shell",
    "- Bundled slim Python runtime copied from the local streamcurator env",
    "- Bundled bilibili / zhihu / xiaohongshu CLI launch wrappers",
    "",
    "LLM setup:",
    "- either set OPENCODE_API_KEY / STREAM_CURATOR_LLM_API_KEY in the environment",
    "- or open Settings in the app and save API URL / Model / API Key there",
    "",
    "Launch:",
    "- unzip",
    "- run stream-curator-win32-x64\\stream-curator.exe"
)
$readmeLines | Set-Content -LiteralPath (Join-Path $portableDir "README.release.txt") -Encoding utf8

if (Test-Path -LiteralPath $zipOutputPath) {
    Remove-Item -LiteralPath $zipOutputPath -Force
}

$zipParent = Split-Path $zipOutputPath -Parent
if (!(Test-Path -LiteralPath $zipParent)) {
    New-Item -ItemType Directory -Path $zipParent -Force | Out-Null
}

Push-Location (Split-Path $portableDir -Parent)
try {
    try {
        Compress-Archive -Path (Split-Path $portableDir -Leaf) -DestinationPath $zipOutputPath -ErrorAction Stop
    } catch {
        Write-Warning "Compress-Archive failed, falling back to tar.exe zip packaging."
        if (Test-Path -LiteralPath $zipOutputPath) {
            Remove-Item -LiteralPath $zipOutputPath -Force -ErrorAction SilentlyContinue
        }
        & tar.exe -a -c -f $zipOutputPath (Split-Path $portableDir -Leaf)
        if ($LASTEXITCODE -ne 0) {
            throw "tar.exe zip packaging failed."
        }
    }
} finally {
    Pop-Location
}

Write-Output "Release zip created at:"
Write-Output $zipOutputPath
