$ErrorActionPreference = 'Stop'
Push-Location (Join-Path $PSScriptRoot '..')
try {
    python main.py init
    if ($LASTEXITCODE -ne 0) { throw 'init failed' }
    python main.py schedule --config config/discovery.json
    if ($LASTEXITCODE -ne 0) { throw 'schedule failed' }
    python main.py work --max-jobs 20
    if ($LASTEXITCODE -ne 0) { throw 'worker failed; inspect status' }
    python main.py report
    if ($LASTEXITCODE -ne 0) { throw 'report failed' }
} finally { Pop-Location }
