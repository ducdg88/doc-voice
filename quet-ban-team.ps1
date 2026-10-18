# Quét bản team: exe hay thư mục chứa exe có lộ giọng Arthur không.
# Dùng: powershell -NoProfile -ExecutionPolicy Bypass -File quet-ban-team.ps1 -ThuMuc dist\team
# Thoát 0 khi sạch. Thấy ref.wav, ref.txt, giong_mau, Arthur.dgvoice trong exe, hay file ref.wav, ref.txt, *.dgvoice cạnh exe
# thì XOÁ DocVoice.exe, in lý do và thoát 1: không có bản nào để đưa đi.
param([Parameter(Mandatory = $true)][string]$ThuMuc)
$ErrorActionPreference = 'Stop'
$exe = Join-Path $ThuMuc 'DocVoice.exe'
if (-not (Test-Path -LiteralPath $exe)) { Write-Error "Không thấy $exe"; exit 2 }
$cam = @('ref.wav', 'ref.txt', 'giong_mau', 'Arthur.dgvoice')
$byte = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $exe).Path)
$chu = [System.Text.Encoding]::GetEncoding(28591).GetString($byte)
$thay = @($cam | Where-Object { $chu.Contains($_) })
$la = @(Get-ChildItem -LiteralPath $ThuMuc -Recurse -File | Where-Object { $_.Name -ne 'DocVoice.exe' -and ($_.Name -match 'ref\.(wav|txt)$|\.dgvoice$') })
if ($thay.Count -gt 0 -or $la.Count -gt 0) {
    Remove-Item -LiteralPath $exe -Force
    Write-Host "Bản team chứa giọng Arthur ($($thay -join ', ') $($la.Name -join ', ')): đã xoá exe, không phát hành"
    exit 1
}
Write-Host "Bản team sạch: không có $($cam -join ', ') trong exe và thư mục."
exit 0
