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
$skillsRootFull = [IO.Path]::GetFullPath($skillsRoot).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
$destinationFull = [IO.Path]::GetFullPath($destination)
if (-not $destinationFull.StartsWith($skillsRootFull, [StringComparison]::OrdinalIgnoreCase) -or
    [IO.Path]::GetFileName($destinationFull) -ne "agent-call-governor") {
    throw "Refusing to replace an unexpected skill destination: $destinationFull"
}
if (Test-Path -LiteralPath $destinationFull) {
    Remove-Item -LiteralPath $destinationFull -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $destination | Out-Null

$sourceFull = [IO.Path]::GetFullPath($source).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
Get-ChildItem -LiteralPath $source -Recurse -File | ForEach-Object {
    $relative = $_.FullName.Substring($sourceFull.Length)
    $segments = $relative -split '[\\/]'
    $excluded = $segments | Where-Object {
        $_ -eq "__pycache__" -or $_ -eq ".pytest_cache" -or $_ -like "*.egg-info"
    }
    if (-not $excluded -and $_.Extension -notin @(".pyc", ".pyo")) {
        $target = Join-Path $destination $relative
        New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $target -Force
    }
}

Write-Host "Installed Agent Call Governor to $destination"
Write-Host "Restart Codex to load the skill."
