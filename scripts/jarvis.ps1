param(
    [ValidateSet("cli", "run", "start", "stop", "status", "install", "uninstall", "test", "logs")]
    [string]$Action = "status",
    [string]$TaskName = "JarvisPC"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$logFile = Join-Path $root "data\logs\jarvis.log"

function Get-JarvisTask {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Test-JarvisRunning {
    if (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) {
        return [bool](Get-NetTCPConnection -LocalPort 47831 -State Listen -ErrorAction SilentlyContinue)
    }

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 47831)
    try { $listener.Start(); return $false }
    catch [System.Net.Sockets.SocketException] { return $true }
    finally { $listener.Stop() }
}

switch ($Action) {
    "cli" {
        if (-not (Test-Path $python)) { throw "Не найден $python. Сначала установи проект по README." }
        Set-Location $root
        & $python -m jarvis cli
        exit $LASTEXITCODE
    }
    "run" {
        if (-not (Test-Path $python)) { throw "Не найден $python. Сначала установи проект по README." }
        Set-Location $root
        & $python -m jarvis run
        exit $LASTEXITCODE
    }
    "start" {
        if (-not (Get-JarvisTask)) {
            throw "Задача '$TaskName' не установлена. Сначала выполни: .\scripts\jarvis.ps1 install"
        }
        Start-ScheduledTask -TaskName $TaskName
        Write-Host "Jarvis запускается в фоне. Статус: .\scripts\jarvis.ps1 status"
    }
    "stop" {
        $task = Get-JarvisTask
        if ($task -and $task.State -eq "Running") {
            Stop-ScheduledTask -TaskName $TaskName
            Write-Host "Фоновый Jarvis остановлен."
        }
        elseif (Test-JarvisRunning) {
            Write-Host "Сервис работает не через задачу '$TaskName'. Останови его в окне запуска сочетанием Ctrl+C."
        }
        else {
            Write-Host "Jarvis уже остановлен."
        }
    }
    "status" {
        $task = Get-JarvisTask
        if ($task) {
            Write-Host "Задача '$TaskName': $($task.State)"
        }
        else {
            Write-Host "Задача автозапуска '$TaskName' не установлена."
        }
        if (Test-JarvisRunning) {
            Write-Host "Сервис: работает (слушает локальный порт 47831)."
        }
        else {
            Write-Host "Сервис: не работает."
        }
        if (Test-Path $logFile) {
            Write-Host "Лог: $logFile"
        }
    }
    "install" {
        & (Join-Path $PSScriptRoot "install_autostart.ps1") -TaskName $TaskName
    }
    "uninstall" {
        & (Join-Path $PSScriptRoot "uninstall_autostart.ps1") -TaskName $TaskName
    }
    "test" {
        if (-not (Test-Path $python)) { throw "Не найден $python. Сначала установи проект по README." }
        Set-Location $root
        & $python -m pytest
        exit $LASTEXITCODE
    }
    "logs" {
        if (-not (Test-Path $logFile)) { throw "Лог ещё не создан: $logFile" }
        Get-Content -Path $logFile -Tail 50 -Wait
    }
}
