# Đóng gói Đọc Voice thành dist\DocVoice.exe từ môi trường sạch .venv
# Dùng: powershell -NoProfile -ExecutionPolicy Bypass -File dong-goi.ps1
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path .venv\Scripts\python.exe)) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller==6.20.0
}
.\.venv\Scripts\python.exe -m pip install -q -U -r requirements.txt
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name DocVoice `
    --distpath dist --workpath build --specpath build doc_voice.py
$ban = (Select-String -Path doc_voice.py -Pattern 'PHIEN_BAN = "(.+)"').Matches[0].Groups[1].Value
"Xong: dist\DocVoice.exe v$ban ({0:N1} MB)" -f ((Get-Item dist\DocVoice.exe).Length / 1MB)
