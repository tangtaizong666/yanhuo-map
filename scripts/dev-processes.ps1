# Hold this stream from before the first port check through readiness, or through
# the entire stop operation. FileShare.None is exclusive across PowerShell processes;
# closing the stream (including process termination) releases it. Do not delete it.
function Enter-DevProcessLock {
    param([string]$Root)
    $lockDirectory = Join-Path $Root '.runtime'
    New-Item -ItemType Directory -Path $lockDirectory -Force | Out-Null
    $lockPath = Join-Path $lockDirectory 'processes.lock'
    try {
        return [System.IO.File]::Open($lockPath, [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    } catch [System.IO.IOException] {
        throw 'Another startup or stop operation is already running for this workspace. Wait for it to finish before trying again.'
    }
}

# Persist each launch before attempting the next one. A partial startup remains
# stoppable, and surviving workers must not be replaced by a second set.
function New-DevProcessRegistry {
    param([string]$Root, [string]$LanUrl)
    $statePath = Join-Path $Root '.runtime\processes.json'
    if (Test-Path -LiteralPath $statePath) {
        $previous = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($previous.root -ne $Root) { throw 'Process record belongs to another workspace.' }
        foreach ($record in $previous.processes) {
            $process = Get-Process -Id $record.id -ErrorAction SilentlyContinue
            if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq ([datetime]$record.started).ToUniversalTime().Ticks) {
                throw 'Previously recorded Yanhuo processes are still running. Use scripts/stop-dev.ps1 before restarting this workspace.'
            }
        }
    }
    return @{root=$Root; processes=@(); lan_url=$LanUrl}
}

function Register-DevProcess {
    param([hashtable]$Registry, $Process)
    $Registry.processes += @{id=$Process.Id; started=$Process.StartTime.ToUniversalTime().ToString('o')}
    $statePath = Join-Path $Registry.root '.runtime\processes.json'
    $temporary = "$statePath.tmp"
    $Registry | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $statePath -Force
}
