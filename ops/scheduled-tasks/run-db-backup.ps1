$ErrorActionPreference = "Stop"
$etlDir = "C:\Users\mullu\.openclaw\workspace\etl\new-etf_insight\etl"
$logDir = Join-Path $etlDir "logs"
$date = Get-Date -Format "yyyyMMdd"
New-Item -Force -ItemType Directory $logDir | Out-Null
Set-Location $etlDir
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
# Monthly DB backup to external drive (overwrite). Script sends its own notify (success/failure/drive missing).
$ErrorActionPreference = "Continue"
& ".venv\Scripts\python.exe" "scripts\backup_db.py" *> (Join-Path $logDir "db-backup-$date.log")
$code = $LASTEXITCODE
$ErrorActionPreference = "Stop"
if ($code -ne 0) { exit 1 }
exit 0
