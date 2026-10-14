# Đóng gói Đọc Giọng thành dist\DocGiong.exe từ môi trường sạch .venv
# Dùng: powershell -NoProfile -ExecutionPolicy Bypass -File dong-goi.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path .venv\Scripts\python.exe)) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller==6.20.0
}
.\.venv\Scripts\python.exe -m pip install -q -U -r requirements.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name DocGiong `
    --distpath dist --workpath build --specpath build doc_giong.py
$ban = (Select-String -Path doc_giong.py -Pattern 'PHIEN_BAN = "(.+)"').Matches[0].Groups[1].Value
"Xong: dist\DocGiong.exe v$ban ({0:N1} MB)" -f ((Get-Item dist\DocGiong.exe).Length / 1MB)
