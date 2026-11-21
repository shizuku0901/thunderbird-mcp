<#
.SYNOPSIS
  Registers the thunderbird-mcp Native Messaging host for the current user.

.DESCRIPTION
  A human runs this script (it is never run by the development assistant).
  It does three things:
    1. Writes a copy of scripts\native-host\thunderbird_mcp_host.json with an absolute "path"
       to %LOCALAPPDATA%\thunderbird-mcp\native-host\ (UTF-8 without BOM).
    2. Points HKCU:\Software\Mozilla\NativeMessagingHosts\thunderbird_mcp_host at that copy.
    3. Prints the remaining manual steps (installing the extension, restarting Thunderbird).
  It does not start Thunderbird and does not install the extension.

  Use -WhatIf to see what would change, and -Unregister to undo the registration.

.EXAMPLE
  .\scripts\register_native_host.ps1 -WhatIf
  .\scripts\register_native_host.ps1
  .\scripts\register_native_host.ps1 -Unregister
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [switch]$Unregister
)

$ErrorActionPreference = 'Stop'

$HostName = 'thunderbird_mcp_host'
$RegistryKey = "HKCU:\Software\Mozilla\NativeMessagingHosts\$HostName"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Template = Join-Path $RepoRoot 'scripts\native-host\thunderbird_mcp_host.json'
$Launcher = Join-Path $RepoRoot 'scripts\native-host\thunderbird_mcp_host.cmd'
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$TargetDir = Join-Path $env:LOCALAPPDATA 'thunderbird-mcp\native-host'
$TargetManifest = Join-Path $TargetDir 'thunderbird_mcp_host.json'

if ($Unregister) {
  if ($PSCmdlet.ShouldProcess($RegistryKey, 'Remove the registry key')) {
    if (Test-Path $RegistryKey) { Remove-Item -Path $RegistryKey -Recurse }
  }
  if ($PSCmdlet.ShouldProcess($TargetManifest, 'Remove the generated host manifest')) {
    if (Test-Path $TargetManifest) { Remove-Item -Path $TargetManifest }
  }
  Write-Host 'Unregistered. The IPC key under %LOCALAPPDATA%\thunderbird-mcp was left untouched.'
  return
}

foreach ($required in @($Template, $Launcher)) {
  if (-not (Test-Path $required)) { throw "Missing file: $required" }
}
if (-not (Test-Path $Python)) {
  throw "Missing $Python. Run 'uv sync' in $RepoRoot first."
}

$manifest = Get-Content -Raw -Encoding UTF8 -Path $Template | ConvertFrom-Json
if ($manifest.name -ne $HostName) { throw "Unexpected host name in $Template" }
$manifest.path = $Launcher
$json = $manifest | ConvertTo-Json -Depth 5

if ($PSCmdlet.ShouldProcess($TargetManifest, 'Write the host manifest (UTF-8 without BOM)')) {
  New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
  [System.IO.File]::WriteAllText($TargetManifest, $json, (New-Object System.Text.UTF8Encoding($false)))
}

if ($PSCmdlet.ShouldProcess($RegistryKey, "Register the host manifest ($TargetManifest)")) {
  New-Item -Path $RegistryKey -Force | Out-Null
  Set-Item -Path $RegistryKey -Value $TargetManifest
}

Write-Host ''
Write-Host 'Registered. Remaining manual steps:'
Write-Host '  1. In Thunderbird: Tools > Developer Tools > Debug Add-ons > Load Temporary Add-on,'
Write-Host "     then choose $RepoRoot\extension\manifest.json"
Write-Host '     (a permanent install needs a signed package or a build that allows unsigned add-ons).'
Write-Host '  2. Check that the permission prompt lists no permission that removes or transmits mail.'
Write-Host '  3. Restart Thunderbird if the extension was already loaded, then call health_check.'
