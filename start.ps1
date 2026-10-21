param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) { throw 'Сначала выполните ./setup.ps1' }
& '.venv/Scripts/python.exe' -X utf8 -m tour_api serve --port $Port
