param([switch]$NoSeed, [switch]$Lan, [string]$LanAddress, [switch]$Simulation)
$ErrorActionPreference = 'Stop'
if ($Simulation) {
    if ($env:DJANGO_ENV -eq 'production') { throw 'Simulation is only supported in the development environment.' }
    $env:DEMO_MODE = 'true'
    $env:SERVICES_SIMULATION_ENABLED = 'true'
}
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$backendDir = Join-Path $projectRoot 'backend'
$frontendDir = Join-Path $projectRoot 'frontend'
$runtimeDir = Join-Path $projectRoot '.runtime'
$frontendHost = '127.0.0.1'
$lanUrl = $null
if ($Lan -or $LanAddress) {
    if ($env:DJANGO_ENV -eq 'production') { throw 'LAN preview is only supported in the development environment.' }
    $addresses = @(Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' -and $_.NetAdapter.HardwareInterface -and $_.IPv4DefaultGateway } | ForEach-Object { $_.IPv4Address.IPAddress } | Where-Object { $_ -match '^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)' } | Select-Object -Unique)
    if ($LanAddress) {
        if ($LanAddress -notin $addresses) { throw "LAN address must belong to an active physical private network: $($addresses -join ', ')" }
    } elseif ($addresses.Count -eq 1) {
        $LanAddress = $addresses[0]
    } else {
        throw "Specify -LanAddress using your Wi-Fi or Ethernet address. Available: $($addresses -join ', ')"
    }
    $frontendHost = '0.0.0.0'
    $lanUrl = "http://${LanAddress}:5183"
}
foreach ($portNumber in @(8087, 5183)) {
    if (Get-NetTCPConnection -LocalPort $portNumber -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $portNumber is already in use. Existing applications have not been stopped. If this is Yanhuo, open http://127.0.0.1:5183; otherwise choose free ports in configuration."
    }
}
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$pythonExe = Join-Path $backendDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) {
    & python -m venv (Join-Path $backendDir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed.' }
}
& $pythonExe -m pip install -r (Join-Path $backendDir 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies could not be installed.' }
$newDatabase = -not (Test-Path -LiteralPath (Join-Path $backendDir 'db.sqlite3'))
& $pythonExe (Join-Path $backendDir 'manage.py') migrate --noinput
if ($LASTEXITCODE -ne 0) { throw 'Database migration failed.' }
if ($newDatabase -and -not $NoSeed) {
    & $pythonExe (Join-Path $backendDir 'manage.py') seed_demo
    if ($LASTEXITCODE -ne 0) { throw 'Demo data could not be prepared.' }
}
Push-Location $frontendDir
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies could not be installed.' }
} finally { Pop-Location }
$previousAllowedHosts = $env:DJANGO_ALLOWED_HOSTS
$previousCsrfOrigins = $env:CSRF_TRUSTED_ORIGINS
try {
    if ($lanUrl) {
        $configuredHosts = if ($previousAllowedHosts) { $previousAllowedHosts } else { 'localhost,127.0.0.1,testserver' }
        $configuredOrigins = if ($previousCsrfOrigins) { $previousCsrfOrigins } else { 'http://localhost:5173,http://127.0.0.1:5173,http://localhost:5183,http://127.0.0.1:5183' }
        $env:DJANGO_ALLOWED_HOSTS = (@($configuredHosts.Split(',')) + $LanAddress | Select-Object -Unique) -join ','
        $env:CSRF_TRUSTED_ORIGINS = (@($configuredOrigins.Split(',')) + $lanUrl | Select-Object -Unique) -join ','
    }
    $backendProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-u','manage.py','runserver','127.0.0.1:8087','--noreload') -WorkingDirectory $backendDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'backend.log') -RedirectStandardError (Join-Path $runtimeDir 'backend.error.log') -PassThru
} finally {
    $env:DJANGO_ALLOWED_HOSTS = $previousAllowedHosts
    $env:CSRF_TRUSTED_ORIGINS = $previousCsrfOrigins
}
if ($Simulation) {
    & $pythonExe (Join-Path $backendDir 'manage.py') prepare_simulation
    if ($LASTEXITCODE -ne 0) { throw 'Simulation services could not be prepared.' }
}
$workerProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-u','manage.py','expire_orders','--loop') -WorkingDirectory $backendDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'worker.log') -RedirectStandardError (Join-Path $runtimeDir 'worker.error.log') -PassThru
$paymentWorkerProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-u','manage.py','reconcile_payments','--loop') -WorkingDirectory $backendDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'payment-worker.log') -RedirectStandardError (Join-Path $runtimeDir 'payment-worker.error.log') -PassThru
$notificationWorkerProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-u','manage.py','process_payment_notifications','--loop') -WorkingDirectory $backendDir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeDir 'notification-worker.log') -RedirectStandardError (Join-Path $runtimeDir 'notification-worker.error.log') -PassThru
$nodeExe = (Get-Command node.exe).Source
# Detach the preview's process group and stdin from this launching terminal.
# The helper opens logs directly; no visible console or interactive CLI is needed.
$frontendId = & $pythonExe (Join-Path $PSScriptRoot 'start_frontend.py') --node $nodeExe --host $frontendHost
if ($LASTEXITCODE -ne 0) { throw 'Frontend preview could not be launched.' }
$frontendProcess = Get-Process -Id ([int]$frontendId) -ErrorAction Stop
$records = @($backendProcess,$workerProcess,$paymentWorkerProcess,$notificationWorkerProcess,$frontendProcess) | ForEach-Object { @{id=$_.Id;started=$_.StartTime.ToUniversalTime().ToString('o')} }
@{root=$projectRoot;processes=@($records);lan_url=$lanUrl} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtimeDir 'processes.json') -Encoding utf8
$ready = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8087/api/v1/health' -TimeoutSec 2
        $front = Invoke-WebRequest -Uri 'http://127.0.0.1:5183/' -TimeoutSec 2 -UseBasicParsing
        if ($health.status -eq 'ok' -and $front.StatusCode -eq 200) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 400 }
}
if (-not $ready) { throw 'Startup did not finish. Check .runtime/backend.error.log and .runtime/frontend.error.log; stop-dev.ps1 stops the recorded processes.' }
Write-Host 'Yanhuo is ready at http://127.0.0.1:5183 . Startup logs are in .runtime/.'
if ($lanUrl) {
    Write-Host "Phone (same Wi-Fi): $lanUrl"
    Write-Host "Merchant workspace: $lanUrl/merchant"
}
