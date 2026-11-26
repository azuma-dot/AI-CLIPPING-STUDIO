Write-Host "Stopping any old processes..." -ForegroundColor Yellow
taskkill /F /IM temporal.exe 2>$null
taskkill /F /IM python.exe 2>$null
Start-Sleep -Seconds 1

$temporalExe = (Get-Command temporal -ErrorAction SilentlyContinue).Source
if (-not $temporalExe) { $temporalExe = "C:\Temporal\temporal.exe" }

Write-Host "1/3 Starting Temporal Server (7233 / UI 8233)..." -ForegroundColor Cyan
Start-Process -FilePath $temporalExe -ArgumentList "server", "start-dev", "--db-filename", "C:\ai-clipping-os\storage\temporal.db" -WindowStyle Minimized
Start-Sleep -Seconds 3

$env:PYTHONPATH = "C:\ai-clipping-os\backend"
$py = "C:\ai-clipping-os\backend\venv\Scripts\python.exe"

Write-Host "2/3 Starting Temporal Worker..." -ForegroundColor Cyan
Start-Process -FilePath $py -ArgumentList "-m", "app.temporal.worker" -WorkingDirectory "C:\ai-clipping-os\backend" -WindowStyle Minimized

Write-Host "3/3 Starting FastAPI Server (8000)..." -ForegroundColor Cyan
Start-Process -FilePath $py -ArgumentList "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--reload" -WorkingDirectory "C:\ai-clipping-os\backend" -WindowStyle Minimized

Start-Sleep -Seconds 2
Write-Host "`nAll 3 services running in background!" -ForegroundColor Green
Write-Host "Temporal UI: http://localhost:8233"
Write-Host "FastAPI Docs: http://127.0.0.1:8000/docs"
