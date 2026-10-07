param(
    [ValidateSet('deployment', 'acceptance', 'all')][string]$Stages = 'all',
    [switch]$FullRegression,
    [string]$Distribution = 'Ubuntu-24.04'
)
$ErrorActionPreference = 'Stop'
$taskRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$taskWindowsPath = $taskRoot.Replace('\', '/')
$taskResolved = & wsl.exe -d $Distribution -u root --exec wslpath -a $taskWindowsPath
if ($LASTEXITCODE -ne 0 -or -not $taskResolved) { throw 'Cannot resolve the repository in WSL.' }
$linuxRoot = $taskResolved.Trim()
$taskArgs = @('-d', $Distribution, '-u', 'root', '--exec', 'python3', "$linuxRoot/scripts/verify_runtime.py", '--stages', $Stages)
if ($FullRegression) { $taskArgs += '--full-regression' }
& wsl.exe @taskArgs
exit $LASTEXITCODE
