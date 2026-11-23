# Регистрирует автозапуск Jarvis при входе в Windows. Права администратора НЕ нужны.
# Запуск (из корня проекта):  powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
param([string]$TaskName = "JarvisPC")

$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\pythonw.exe"   # pythonw = без окна консоли
if (-not (Test-Path $py)) { throw "Не найден $py. Сначала создай .venv (см. README)." }

$action   = New-ScheduledTaskAction -Execute $py -Argument "-m jarvis run" -WorkingDirectory $root
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Задача '$TaskName' создана. Запустить сейчас: Start-ScheduledTask -TaskName $TaskName"
Write-Host "Проверить: Get-ScheduledTaskInfo -TaskName $TaskName ; лог: data\logs\jarvis.log"
