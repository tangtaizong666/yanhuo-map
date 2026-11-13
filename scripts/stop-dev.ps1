$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'dev-processes.ps1')
$lifecycleLock = Enter-DevProcessLock -Root $projectRoot
try {
    $statePath = Join-Path $projectRoot '.runtime\processes.json'
    if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No processes started by start-dev.ps1 were recorded.'; exit }
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($state.root -ne $projectRoot) { throw 'Process record belongs to another workspace.' }
    foreach ($record in $state.processes) {
        $process = Get-Process -Id $record.id -ErrorAction SilentlyContinue
        $recordedTime = [datetime]$record.started
        if (Test-SameProcessStartTime -Process $process -RecordedTime $recordedTime) {
            # Windows venv python.exe launches a second Python process. Only stop a
            # direct child whose command still belongs to this verified workspace.
            $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($record.id)" -ErrorAction SilentlyContinue
            foreach ($child in $children) {
                if ($child.CommandLine -and $child.CommandLine.IndexOf($projectRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
                    Stop-Process -Id $child.ProcessId -Force -ErrorAction SilentlyContinue
                }
            }
            Stop-Process -Id $record.id -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped recorded Yanhuo process $($record.id)."
        }
    }
    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
} finally {
    $lifecycleLock.Dispose()
}
