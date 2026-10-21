$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
py -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать окружение Python' }
& '.venv/Scripts/python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить зависимости' }
& '.venv/Scripts/python.exe' -m playwright install chromium
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить Chromium' }
Write-Host 'Установка завершена. Запустите ./start.ps1'
