param(
    [string]$Target = "$env:USERPROFILE\jazrespond"
)
$ErrorActionPreference = "Stop"
$Source = $PSScriptRoot
if (-not (Test-Path $Target)) { throw "Target repo not found: $Target" }
Write-Host "Restoring direct-Gemini Flask backend into $Target"
Get-ChildItem -Force $Source | Where-Object { $_.Name -notin @('RESTORE_TO_REPO.ps1','REMOVE_SERVER_V4.ps1') } | ForEach-Object {
    Copy-Item $_.FullName -Destination $Target -Recurse -Force
}
if (Test-Path "$Target\server_v4") { Remove-Item -Recurse -Force "$Target\server_v4" }
if (Test-Path "$Target\asgi_app.py") { Remove-Item -Force "$Target\asgi_app.py" }
Write-Host "Restore complete. Now run: git status; git add -A; git commit; git push"
