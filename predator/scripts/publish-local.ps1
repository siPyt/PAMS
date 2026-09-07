# Build Predator once, then publish that SAME build two ways so they never drift:
#   1) a per-user desktop app at %LOCALAPPDATA%\Programs\Predator (+ Desktop shortcut)
#   2) the distributable Desktop\Predator-App\ folder and Predator-App.zip
# No admin required. Run:  npm run publish:local   (from the predator/ folder)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot            # predator/
$repo = Split-Path -Parent $root                    # repo root
Set-Location $root

Write-Host 'Closing any running Predator...'
Get-Process Predator, electron -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 600

Write-Host 'Syntax-checking JS (renderer must parse or the app is a dead shell)...'
foreach ($f in @('renderer\app.js', 'main.js', 'preload.js')) {
    node --check $f
    if ($LASTEXITCODE -ne 0) { throw "Syntax error in $f - aborting build" }
}
Write-Host '  JS OK'

Write-Host 'Building (electron-builder)...'
npm run dist | Out-Null
$unpacked = Join-Path $root 'dist\win-unpacked'
if (-not (Test-Path (Join-Path $unpacked 'Predator.exe'))) { throw 'build failed: win-unpacked/Predator.exe missing' }

# 1) Per-user app, always replaced with the fresh build (no admin)
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\Predator'
Write-Host "Publishing app -> $installDir"
if (Test-Path $installDir) { Remove-Item $installDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item "$unpacked\*" $installDir -Recurse -Force

$desktop = [Environment]::GetFolderPath('Desktop')
$lnk = Join-Path $desktop 'Predator.lnk'
$sh = New-Object -ComObject WScript.Shell
$sc = $sh.CreateShortcut($lnk)
$sc.TargetPath = Join-Path $installDir 'Predator.exe'
$sc.WorkingDirectory = $installDir
$sc.IconLocation = (Join-Path $installDir 'Predator.exe')
$sc.Description = 'Predator - PAMS cockpit'
$sc.Save()
Write-Host "Desktop shortcut -> $lnk"

# 2) Distributable folder + zip, from the SAME build
$pkg = Join-Path $desktop 'Predator-App'
$docs = Join-Path $pkg 'docs'
New-Item -ItemType Directory -Force -Path $docs | Out-Null
Get-ChildItem $root 'dist\Predator Setup *.exe' | Copy-Item -Destination $pkg -Force
Get-ChildItem $root 'dist\Predator *.exe' | Where-Object { $_.Name -notlike 'Predator Setup*' } | Copy-Item -Destination $pkg -Force
if (Test-Path (Join-Path $root 'build\READ-ME-FIRST.txt')) {
  Copy-Item (Join-Path $root 'build\READ-ME-FIRST.txt') (Join-Path $pkg 'READ-ME-FIRST.txt') -Force
}
Copy-Item (Join-Path $repo 'README.md') (Join-Path $docs 'PAMS-System-README.md') -Force
Copy-Item (Join-Path $root 'README.md') (Join-Path $docs 'Predator-README.md') -Force
Copy-Item (Join-Path $repo 'deploy\PAMS_BACnet_MSTP_Integration.md') $docs -Force
Copy-Item (Join-Path $repo 'deploy\PAMS_CheatSheet.txt') $docs -Force
Copy-Item (Join-Path $repo 'deploy\PAMS_Commands.txt') $docs -Force

$zip = Join-Path $desktop 'Predator-App.zip'
if (Test-Path $zip) { Remove-Item $zip -Force }
# Zip into TEMP first, then move - avoids transient OneDrive sync file locks.
$tmpZip = Join-Path $env:TEMP 'Predator-App.zip'
if (Test-Path $tmpZip) { Remove-Item $tmpZip -Force }
Compress-Archive -Path (Join-Path $pkg '*') -DestinationPath $tmpZip -CompressionLevel Optimal
Move-Item $tmpZip $zip -Force

Write-Host ''
Write-Host 'Published. Desktop app + zip are now the same build:' -ForegroundColor Green
Write-Host "  app : $installDir\Predator.exe  (Desktop shortcut 'Predator')"
Write-Host "  zip : $zip"
Write-Host ''
Write-Host 'One-time cleanup: uninstall the OLD "Predator" (C:\Program Files\Predator)'
Write-Host 'via Settings > Apps > Installed apps, so only this current version remains.'
