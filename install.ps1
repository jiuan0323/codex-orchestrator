$ErrorActionPreference = "Stop"
$binDir = Join-Path $HOME ".local\bin"
New-Item -ItemType Directory -Path $binDir -Force | Out-Null

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Item (Join-Path $repoRoot "bin\codex-orch.cmd") (Join-Path $binDir "codex-orch.cmd") -Force
Copy-Item (Join-Path $repoRoot "bin\codex-orch") (Join-Path $binDir "codex-orch") -Force

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($userPath -split ';' | Where-Object { $_.TrimEnd('\') -ieq $binDir.TrimEnd('\') })) {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    Write-Host "Added $binDir to User PATH. Restart terminal to take effect."
}
Write-Host "Installed codex-orch to $binDir"
