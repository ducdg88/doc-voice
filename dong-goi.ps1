# Đóng gói Đọc Voice thành dist\DocVoice.exe từ môi trường sạch .venv
# Giọng clone: bản đóng sẵn giong_mau\arthur\ref.wav (chỉ dùng để dựng thử, không phát hành).
# Bản gửi team: thêm -KhongGiongMau, ra dist\team\DocVoice.exe không kèm giọng; người nhận bấm
# "Nhập giọng (.dgvoice)" với file Arthur.dgvoice gửi kèm.
# Máy đọc nhẹ: đồ thị ONNX may_nhe_onnx\ đi kèm exe, trọng số tải lần đầu từ Hugging Face.
# Bản Arthur mặc định: -ArthurNhung ra dist\arthur\DocVoice.exe, chỉ nhúng DẤU GIỌNG giong_nhung\arthur\se.npy
# (256 số, 1 KB, chỉ chế độ Nhẹ dùng được), KHÔNG nhúng ref.wav, ref.txt hay gói .dgvoice. Tạo se.npy: tao_dau_giong.py.
# Dùng: powershell -NoProfile -ExecutionPolicy Bypass -File dong-goi.ps1 [-KhongGiongMau | -ArthurNhung]
param([switch]$KhongGiongMau, [switch]$ArthurNhung)
if ($KhongGiongMau -and $ArthurNhung) { throw "Chọn một trong -KhongGiongMau hoặc -ArthurNhung" }
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path .venv\Scripts\python.exe)) {
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller==6.20.0
}
.\.venv\Scripts\python.exe -m pip install -q -U -r requirements.txt
$them = @(
    '--add-data', "$PSScriptRoot\may_nhe_onnx\ov_ref.onnx;may_nhe_onnx",
    '--add-data', "$PSScriptRoot\may_nhe_onnx\ov_doi.onnx;may_nhe_onnx",
    '--add-data', "$PSScriptRoot\may_nhe_onnx\ov_cong_thuc.json;may_nhe_onnx"
)
$ra = 'dist'
if ($KhongGiongMau) {
    $ra = 'dist\team'
} elseif ($ArthurNhung) {
    $ra = 'dist\arthur'
    if (-not (Test-Path "$PSScriptRoot\giong_nhung\arthur\se.npy")) { throw "Thiếu giong_nhung\arthur\se.npy (chạy tao_dau_giong.py)" }
    $them += @('--add-data', "$PSScriptRoot\giong_nhung\arthur\se.npy;giong_nhung\arthur")
} else {
    $them += @('--add-data', "$PSScriptRoot\giong_mau\arthur\ref.wav;giong_mau\arthur",
               '--add-data', "$PSScriptRoot\giong_mau\arthur\ref.txt;giong_mau\arthur")
}
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name DocVoice `
    --distpath $ra --workpath build --specpath build @them doc_voice.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller lỗi ($LASTEXITCODE), exe chưa được dựng lại" }
# file DOCVOICE_THU / DOCGIONG_THU cạnh exe bật chế độ thử (máy chủ giả); bản phát cho team không được kèm.
foreach ($t in 'DOCVOICE_THU', 'DOCGIONG_THU') {
    if (Test-Path (Join-Path $ra $t)) { Remove-Item (Join-Path $ra $t) -Force }
}
# bản team không được lộ giọng Arthur. quet-ban-team.ps1 quét tên file trong exe (bảng mục lục PyInstaller
# để chữ thường, không nén) và thư mục cạnh exe; thấy thì xoá exe và dừng, không có bản nào để đưa đi.
if ($KhongGiongMau -or $ArthurNhung) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\quet-ban-team.ps1" -ThuMuc $ra
    if ($LASTEXITCODE -ne 0) { throw "Bản chứa âm thanh mẫu Arthur (ref.wav, ref.txt, giong_mau, .dgvoice): đã xoá exe, không phát hành" }
}
$ban = (Select-String -Path doc_voice.py -Pattern 'PHIEN_BAN = "(.+)"').Matches[0].Groups[1].Value
"Xong: $ra\DocVoice.exe v$ban ({0:N1} MB)" -f ((Get-Item "$ra\DocVoice.exe").Length / 1MB)
