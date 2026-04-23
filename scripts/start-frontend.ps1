$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $root "dashboard\frontend")

# Vite reads ../../.env directly in vite.config.ts — nothing else needed here.
npm run dev
