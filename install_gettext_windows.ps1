$ErrorActionPreference = 'Stop'

Write-Host 'Installing GNU gettext with Chocolatey (requires Administrator)...' -ForegroundColor Cyan
choco install gettext -y --no-progress

$gettextBin = 'C:\ProgramData\chocolatey\lib\gettext\tools\bin'
if (Test-Path $gettextBin) {
    $machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    if (-not $machinePath.Split(';').Contains($gettextBin)) {
        [Environment]::SetEnvironmentVariable('Path', "$machinePath;$gettextBin", 'Machine')
        Write-Host "Added to PATH: $gettextBin" -ForegroundColor Green
    } else {
        Write-Host 'gettext bin already present in PATH.' -ForegroundColor Yellow
    }
} else {
    Write-Host "gettext bin path not found: $gettextBin" -ForegroundColor Red
    exit 1
}

Write-Host 'Verifying msgfmt...' -ForegroundColor Cyan
$msgfmt = Get-Command msgfmt -ErrorAction SilentlyContinue
if ($null -eq $msgfmt) {
    Write-Host 'msgfmt not available in current shell yet. Reopen terminal and run: msgfmt --version' -ForegroundColor Yellow
    exit 0
}

msgfmt --version
Write-Host 'GNU gettext installation completed.' -ForegroundColor Green
