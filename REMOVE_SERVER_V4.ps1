$ErrorActionPreference = "Stop"
if (Test-Path ".\server_v4") { Remove-Item -Recurse -Force ".\server_v4" }
if (Test-Path ".\asgi_app.py") { Remove-Item -Force ".\asgi_app.py" }
Write-Host "Removed experimental Server V4 ASGI files."
