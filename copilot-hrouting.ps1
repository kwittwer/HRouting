<#
.SYNOPSIS
    HRouting KI-Assistent über die GitHub Copilot CLI.

.DESCRIPTION
    Startet die Copilot CLI mit dem HRouting MCP-Server, sodass
    Copilot direkt .hrp-Projektdateien lesen und bearbeiten kann.

.PARAMETER Prompt
    Optionaler Prompt für nicht-interaktiven Modus.
    Ohne Prompt startet der interaktive Modus.

.PARAMETER Project
    Pfad zur .hrp-Projektdatei. Wird automatisch geladen.

.EXAMPLE
    # Interaktiver Modus
    .\copilot-hrouting.ps1

.EXAMPLE
    # Mit Projekt
    .\copilot-hrouting.ps1 -Project "mein_projekt.hrp"

.EXAMPLE
    # Nicht-interaktiv mit Prompt
    .\copilot-hrouting.ps1 -Prompt "Welche Heizkreise gibt es?" -Project "beispiel.hrp"

.EXAMPLE
    # Kurzform
    .\copilot-hrouting.ps1 -p "Berechne die Heizlast" -Project "haus.hrp"
#>

param(
    [Alias("p")]
    [string]$Prompt,

    [string]$Project
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# MCP-Config Pfad
$McpConfig = Join-Path $ScriptDir ".copilot\mcp-config.json"
if (-not (Test-Path $McpConfig)) {
    Write-Error "MCP-Config nicht gefunden: $McpConfig"
    exit 1
}

# Copilot CLI prüfen
$CopilotCmd = Get-Command copilot -ErrorAction SilentlyContinue
if (-not $CopilotCmd) {
    Write-Error @"
GitHub Copilot CLI nicht gefunden.
Installation: winget install GitHub.Copilot
Anmeldung:    copilot auth login
"@
    exit 1
}

# Argumente aufbauen
$args_list = @(
    "--additional-mcp-config", "@$McpConfig"
    "-C", $ScriptDir
)

# Projekt als Kontext-Prompt vorbereiten
$projectPrompt = ""
if ($Project) {
    $ProjectPath = Resolve-Path $Project -ErrorAction Stop
    $projectPrompt = "Öffne zuerst das Projekt mit open_project('$($ProjectPath -replace '\\', '\\')'), dann "
}

if ($Prompt) {
    # Nicht-interaktiver Modus
    $fullPrompt = $projectPrompt + $Prompt
    $args_list += @("-p", $fullPrompt, "--allow-all-tools")
    Write-Host "🤖 HRouting Copilot: $Prompt" -ForegroundColor Cyan
    & copilot @args_list
} else {
    # Interaktiver Modus
    if ($projectPrompt) {
        $args_list += @("-i", "${projectPrompt}zeige eine Projektübersicht.")
    }
    Write-Host ""
    Write-Host "╔══════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "║  HRouting KI-Assistent (Copilot CLI)     ║" -ForegroundColor Cyan
    Write-Host "║  Tippe 'exit' zum Beenden                ║" -ForegroundColor Cyan
    Write-Host "╚══════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    & copilot @args_list
}
