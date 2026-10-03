param([switch]$Postgres, [switch]$SkipBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonExe = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Prepare backend/.venv and frontend dependencies first.' }
$arguments = @((Join-Path $PSScriptRoot 'verify_pilot.py'))
if ($Postgres) { $arguments += '--postgres' }
if ($SkipBrowser) { $arguments += '--skip-browser' }
& $pythonExe @arguments
if ($LASTEXITCODE -ne 0) { throw 'Pilot verification failed. Review the reported artifact directory.' }
