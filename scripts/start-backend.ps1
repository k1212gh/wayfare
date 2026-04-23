$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$env:PYTHONIOENCODING = "utf-8"

$port = "8008"
if (Test-Path ".env") {
    $match = Select-String -Path ".env" -Pattern '^BACKEND_PORT\s*=\s*(\d+)' | Select-Object -First 1
    if ($match) { $port = $match.Matches.Groups[1].Value }
}

Write-Host "[start-backend] root=$root port=$port"
python -m uvicorn dashboard.backend.server:app --host 127.0.0.1 --port $port --reload
