param([switch]$InstallChocolatey = $false)

Write-Host "=== Setup: YouTube Shorts Auto Pipeline ==="

if ($InstallChocolatey) {
  Set-ExecutionPolicy Bypass -Scope Process -Force
  [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
  Invoke-Expression ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
}

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) { Write-Host "Install ffmpeg: choco install ffmpeg -y" -ForegroundColor Yellow }

if (-not (Test-Path .\.venv\Scripts\Activate.ps1)) { python -m venv .venv }
.\.venv\Scripts\pip install -r requirements.txt
Write-Host "Done. Edit config.yaml and put OAuth files into /auth."
