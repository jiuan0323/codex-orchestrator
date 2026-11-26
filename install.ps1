$ErrorActionPreference = "Stop"
$binDir = Join-Path $HOME ".local\bin"
New-Item -ItemType Directory -Path $binDir -Force | Out-Null

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Copy-Item (Join-Path $repoRoot "bin\codex-orch.cmd") (Join-Path $binDir "codex-orch.cmd") -Force
Copy-Item (Join-Path $repoRoot "bin\codex-orch") (Join-Path $binDir "codex-orch") -Force

$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if ($pyExe) {
    $sidecarJson = Join-Path $repoRoot "sidecars\codex-console\sidecar.json"
    if (Test-Path $sidecarJson) {
        $cfg = Get-Content $sidecarJson -Raw -Encoding UTF8 | ConvertFrom-Json
        $cfg.command = $pyExe.Replace('\', '/')
        $cfg | ConvertTo-Json -Depth 6 | Set-Content -Path $sidecarJson -Encoding UTF8
        Write-Host "Updated sidecar.json python path to $($cfg.command)"
    }
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($userPath -split ';' | Where-Object { $_.TrimEnd('\') -ieq $binDir.TrimEnd('\') })) {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$binDir", "User")
    Write-Host "Added $binDir to User PATH. Restart terminal to take effect."
}
Write-Host "Installed codex-orch to $binDir"
