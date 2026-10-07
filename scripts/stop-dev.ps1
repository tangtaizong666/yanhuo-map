$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$statePath = Join-Path $projectRoot '.runtime\processes.json'
if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No processes started by start-dev.ps1 were recorded.'; exit }
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($state.root -ne $projectRoot) { throw 'Process record belongs to another workspace.' }
foreach ($record in $state.processes) {
    $process = Get-Process -Id $record.id -ErrorAction SilentlyContinue
    $recordedTime = [datetime]$record.started
    if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq $recordedTime.ToUniversalTime().Ticks) {
        # Windows venv python.exe launches a second Python process. Only stop a
        # direct child whose command still belongs to this verified workspace.
        $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($record.id)"
        foreach ($child in $children) {
            if ($child.CommandLine -and $child.CommandLine.Contains($projectRoot)) {
                Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            }
        }
        Stop-Process -Id $record.id
        Write-Host "Stopped recorded Yanhuo process $($record.id)."
    }
}
