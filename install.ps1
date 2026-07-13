[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$source = Join-Path $PSScriptRoot "agent-call-governor"
$skillsRoot = if ($env:CODEX_HOME) {
    Join-Path $env:CODEX_HOME "skills"
} else {
    Join-Path $HOME ".codex\skills"
}
$destination = Join-Path $skillsRoot "agent-call-governor"

New-Item -ItemType Directory -Force -Path $skillsRoot | Out-Null
New-Item -ItemType Directory -Force -Path $destination | Out-Null
Copy-Item -Path (Join-Path $source "*") -Destination $destination -Recurse -Force

Write-Host "Installed Agent Call Governor to $destination"
Write-Host "Restart Codex to load the skill."
