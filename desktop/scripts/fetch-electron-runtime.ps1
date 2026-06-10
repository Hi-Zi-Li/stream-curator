$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$packageJsonPath = Join-Path $PSScriptRoot "..\package.json"
$packageJson = Get-Content -LiteralPath $packageJsonPath -Raw | ConvertFrom-Json
$versionSpec = [string]$packageJson.devDependencies.electron
$version = $versionSpec.TrimStart("^", "~")

$electronDir = Resolve-Path (Join-Path $PSScriptRoot "..\node_modules\electron")
$distDir = Join-Path $electronDir "dist"
$zipName = "electron-v$version-win32-x64.zip"
$zipPath = Join-Path $electronDir $zipName
$mirrorUrl = "https://npmmirror.com/mirrors/electron/v$version/$zipName"

if (!(Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}

Write-Output "Downloading $mirrorUrl"
Invoke-WebRequest $mirrorUrl -OutFile $zipPath -UseBasicParsing

Write-Output "Extracting runtime to $distDir"
Expand-Archive -LiteralPath $zipPath -DestinationPath $distDir -Force

Set-Content -Path (Join-Path $electronDir "path.txt") -Value "electron.exe" -Encoding ascii
Remove-Item -LiteralPath $zipPath -Force

Write-Output "Electron runtime is ready."
