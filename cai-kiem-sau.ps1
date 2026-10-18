# Cài "kiểm sâu" cho Đọc Voice: tạo môi trường Python riêng có faster-whisper, onnxruntime, numpy rồi ghi đường
# Python vào cài đặt của app (khoá python_kiem). Có nó, nút "Kiểm chất lượng" mới nghe lại bài bằng whisper và đo sai giọng
# từng câu; không có thì chỉ chạy tầng tín hiệu (mất tiếng, im, lặp, click, âm lượng) và báo "Chưa kiểm giọng".
# Dùng: powershell -NoProfile -ExecutionPolicy Bypass -File cai-kiem-sau.ps1 [-DryRun]
# Cần: Windows 10 trở lên, Python 3.10 đến 3.12 (cài bằng: winget install Python.Python.3.12), Internet, ổ trống khoảng 2 GB.
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$goc = Join-Path $env:LOCALAPPDATA 'DocVoice\kiem-sau'
$venv = Join-Path $goc 'venv'
$py = Join-Path $venv 'Scripts\python.exe'
$cd = Join-Path $env:APPDATA 'DocVoice\cai_dat.json'

function Tim-Python {
    foreach ($ten in 'py', 'python') {
        $c = Get-Command $ten -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notmatch 'WindowsApps') {
            $dau = @()
            if ($ten -eq 'py') { $dau = @('-3') }
            $ma = "import sys;print(str(sys.version_info[0]) + '.' + str(sys.version_info[1]))"
            $v = $null
            try { $v = (& $c.Source @dau -c $ma 2>$null | Select-Object -First 1) } catch {}
            if ($v -match '^3\.(1[0-2])$') { return @{ exe = $c.Source; dau = $dau } }
        }
    }
    return $null
}

$goi = Tim-Python
if (-not $goi) { throw 'Chưa thấy Python 3.10 đến 3.12. Cài bằng: winget install Python.Python.3.12 (rồi mở lại cửa sổ này).' }
Write-Host "Python gốc: $($goi.exe)"
Write-Host "Sẽ tạo môi trường ở: $venv"
Write-Host "Sẽ ghi python_kiem vào: $cd"
if ($DryRun) { Write-Host 'DryRun: dừng ở đây, chưa cài gì.'; exit 0 }

New-Item -ItemType Directory -Force -Path $goc | Out-Null
if (-not (Test-Path $py)) { & $goi.exe @($goi.dau) -m venv $venv; if ($LASTEXITCODE -ne 0) { throw 'Không tạo được môi trường Python' } }
& $py -m pip install -q --upgrade pip
& $py -m pip install -q 'faster-whisper==1.2.1' 'onnxruntime==1.26.0' 'numpy'
if ($LASTEXITCODE -ne 0) { throw 'Cài thư viện lỗi (xem mạng rồi chạy lại, lệnh này chạy lại an toàn)' }
Write-Host 'Đang tải model nghe lại (whisper small, khoảng 460 MB, một lần)...'
& $py -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8'); print('model san sang')"
if ($LASTEXITCODE -ne 0) { throw 'Không tải được model whisper' }

New-Item -ItemType Directory -Force -Path (Split-Path $cd) | Out-Null
$j = @{}
if (Test-Path $cd) { try { (Get-Content $cd -Raw -Encoding UTF8 | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $j[$_.Name] = $_.Value } } catch {} }
$j['python_kiem'] = $py
$j['kiem_whisper'] = $true
($j | ConvertTo-Json -Depth 6) | Set-Content -Path $cd -Encoding UTF8
Write-Host "Xong. Mở lại Đọc Voice rồi bấm Kiểm chất lượng. python_kiem = $py"
