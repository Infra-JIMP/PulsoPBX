[CmdletBinding()]
param(
    [switch]$ValidateOnly,
    [switch]$AllowDirty
)

# Publica o codigo versionado na copia local com preflight, backup e rollback.
# Nao copia nem remove .env, .venv, logs, data ou outros estados locais.
$ErrorActionPreference = "Stop"
$taskName = "RamaisMonitor"
$src = $PSScriptRoot
$dst = "C:\Users\eduardo.p\ramais_monitor"
$backupRoot = "C:\Users\eduardo.p\ramais_monitor_backups"
$backupRetention = 5
$excludedDirectories = @(
    ".git", ".venv", "__pycache__", "logs", "data", "output",
    ".claude", ".playwright-cli", "tests"
)
$excludedFiles = @("*.pyc", ".env", "ramais_nomes.json", "work_calendar.json")

function Invoke-SafeRobocopy {
    param(
        [Parameter(Mandatory)] [string]$Source,
        [Parameter(Mandatory)] [string]$Destination,
        [Parameter(Mandatory)] [ValidateSet("/E", "/MIR")] [string]$Mode
    )
    $arguments = @(
        $Source, $Destination, $Mode,
        "/R:2", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NC", "/NS",
        "/XD"
    ) + $excludedDirectories + @("/XF") + $excludedFiles
    & robocopy @arguments | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "Robocopy falhou com codigo $LASTEXITCODE ao copiar '$Source' para '$Destination'."
    }
}

function Test-RamaisMonitorCode {
    param(
        [Parameter(Mandatory)] [string]$Root,
        [Parameter(Mandatory)] [string]$Python,
        [switch]$SkipUnitTests
    )
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "Python do ambiente virtual nao encontrado: $Python"
    }
    $previousBytecode = $env:PYTHONDONTWRITEBYTECODE
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Push-Location -LiteralPath $Root
    try {
        if (-not $SkipUnitTests) {
            $testsRoot = Join-Path $Root "tests"
            if (-not (Test-Path -LiteralPath $testsRoot -PathType Container)) {
                throw "Pasta de testes nao encontrada: $testsRoot"
            }
            & $Python -m unittest discover -s $testsRoot -t $Root -q
            if ($LASTEXITCODE -ne 0) { throw "Testes falharam em $testsRoot" }
        }
        & $Python -c "from config import load_config; load_config(); import main, web"
        if ($LASTEXITCODE -ne 0) { throw "Smoke test de configuracao/imports falhou em $Root" }
        & $Python -m pip check
        if ($LASTEXITCODE -ne 0) { throw "Dependencias inconsistentes em $Root" }
    }
    finally {
        Pop-Location
        $env:PYTHONDONTWRITEBYTECODE = $previousBytecode
    }
}

function Stop-RamaisMonitor {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*ramais_monitor*main.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
}

function Start-RamaisMonitor {
    Start-ScheduledTask -TaskName $taskName
}

function Remove-ExpiredBackups {
    param(
        [Parameter(Mandatory)] [string]$Root,
        [Parameter(Mandatory)] [ValidateRange(1, 100)] [int]$Keep
    )
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return
    }
    $expectedRoot = [IO.Path]::GetFullPath("C:\Users\eduardo.p\ramais_monitor_backups").TrimEnd("\")
    $actualRoot = [IO.Path]::GetFullPath($Root).TrimEnd("\")
    if ($actualRoot -ne $expectedRoot) {
        throw "Raiz de backups recusada por seguranca: $actualRoot"
    }
    $expired = @(
        Get-ChildItem -LiteralPath $actualRoot -Directory |
            Sort-Object LastWriteTime -Descending |
            Select-Object -Skip $Keep
    )
    foreach ($directory in $expired) {
        $target = [IO.Path]::GetFullPath($directory.FullName).TrimEnd("\")
        if (-not $target.StartsWith($actualRoot + "\", [StringComparison]::OrdinalIgnoreCase)) {
            throw "Backup recusado por seguranca: $target"
        }
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    if ($expired.Count -gt 0) {
        Write-Output "Retencao de backups: $($expired.Count) copia(s) antiga(s) removida(s); $Keep preservada(s)."
    }
}

if (-not (Test-Path -LiteralPath $src -PathType Container)) {
    throw "Origem nao encontrada: $src"
}
if (-not (Test-Path -LiteralPath $dst -PathType Container)) {
    throw "Destino nao encontrado: $dst"
}
$expectedDestination = [IO.Path]::GetFullPath("C:\Users\eduardo.p\ramais_monitor").TrimEnd("\")
$actualDestination = [IO.Path]::GetFullPath($dst).TrimEnd("\")
if ($actualDestination -ne $expectedDestination) {
    throw "Destino recusado por seguranca: $actualDestination"
}
if (Test-Path -LiteralPath (Join-Path $src ".git") -PathType Container) {
    $gitStatus = @(& git -c safe.directory='*' -C $src status --porcelain)
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel consultar o estado Git da origem."
    }
    if ($gitStatus.Count -gt 0) {
        if (-not $AllowDirty) {
            throw "Deploy recusado: a origem possui alteracoes nao commitadas. Use -AllowDirty somente para uma publicacao revisada e intencional."
        }
        Write-Warning "Publicacao autorizada com alteracoes nao commitadas; elas permanecerao visiveis no Git da origem."
    }
    $revision = & git -c safe.directory='*' -C $src rev-parse --short HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel identificar a revisao Git da origem."
    }
    Write-Output "Revisao preparada para deploy: $revision"
}

Write-Output "Preflight: testes, configuracao, imports e dependencias..."
Test-RamaisMonitorCode -Root $src -Python (Join-Path $src ".venv\Scripts\python.exe")
if ($ValidateOnly) {
    Write-Output "Validacao concluida. Nenhum arquivo ou servico foi alterado."
    exit 0
}

$stageBase = [IO.Path]::GetFullPath((Join-Path $env:TEMP "RamaisMonitor-deploy"))
$stage = [IO.Path]::GetFullPath((Join-Path $stageBase ([guid]::NewGuid().ToString("N"))))
if (-not $stage.StartsWith($stageBase + "\", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Diretorio de staging recusado por seguranca: $stage"
}
$backup = Join-Path $backupRoot (Get-Date -Format "yyyyMMdd-HHmmss")
$backupCreated = $false
$serviceStopped = $false

try {
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    Write-Output "Preparando pacote de staging..."
    Invoke-SafeRobocopy -Source $src -Destination $stage -Mode "/MIR"

    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    Write-Output "Criando backup recuperavel em $backup..."
    Invoke-SafeRobocopy -Source $dst -Destination $backup -Mode "/E"
    $backupCreated = $true

    Write-Output "Parando o servico..."
    Stop-RamaisMonitor
    $serviceStopped = $true

    Write-Output "Instalando o pacote validado..."
    Invoke-SafeRobocopy -Source $stage -Destination $dst -Mode "/MIR"
    $destinationPython = Join-Path $dst ".venv\Scripts\python.exe"
    Write-Output "Sincronizando dependencias fixadas..."
    & $destinationPython -m pip install --disable-pip-version-check -r (Join-Path $dst "requirements.lock.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Nao foi possivel instalar as dependencias fixadas no ambiente de producao."
    }
    Test-RamaisMonitorCode -Root $dst -Python $destinationPython -SkipUnitTests
    $dashboardPort = & $destinationPython -c "from config import load_config; print(load_config().dashboard_port)"
    if ($LASTEXITCODE -ne 0 -or -not $dashboardPort) {
        throw "Nao foi possivel determinar DASHBOARD_PORT apos o deploy."
    }

    Write-Output "Iniciando o servico e validando o endpoint de saude..."
    Start-RamaisMonitor
    $serviceStopped = $false
    Start-Sleep -Seconds 5
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$dashboardPort/api/health" -TimeoutSec 10
    if ($health.ready -ne $true) {
        throw "Endpoint de saude respondeu sem ready=true."
    }
    Write-Output "Deploy concluido. Backup preservado em: $backup"
    try {
        Remove-ExpiredBackups -Root $backupRoot -Keep $backupRetention
    }
    catch {
        Write-Warning "Deploy concluido, mas a retencao de backups falhou: $($_.Exception.Message)"
    }
}
catch {
    Write-Error "Deploy falhou: $($_.Exception.Message)"
    if ($backupCreated) {
        Write-Output "Restaurando a versao anterior..."
        Stop-RamaisMonitor
        Invoke-SafeRobocopy -Source $backup -Destination $dst -Mode "/MIR"
        Start-RamaisMonitor
        $serviceStopped = $false
        Write-Output "Rollback concluido."
    }
    throw
}
finally {
    if ($serviceStopped) {
        Start-RamaisMonitor
    }
    if (
        (Test-Path -LiteralPath $stage) -and
        $stage.StartsWith($stageBase + "\", [StringComparison]::OrdinalIgnoreCase)
    ) {
        Remove-Item -LiteralPath $stage -Recurse -Force
    }
}
