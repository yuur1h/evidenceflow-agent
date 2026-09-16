$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
& "$PSScriptRoot\.venv\Scripts\python.exe" -m uvicorn agent:app --host 127.0.0.1 --port 8000
