# Phát hành Đọc Voice bằng MỘT lệnh (bản 1.5.0). Chi tiết và lý do từng bước: docs\PHAT-HANH.md
# Dùng:
#   powershell -NoProfile -ExecutionPolicy Bypass -File phat-hanh.ps1 -Ban 1.6.0            (chuẩn bị: kiểm, dựng, sha256, KHÔNG đăng)
#   powershell -NoProfile -ExecutionPolicy Bypass -File phat-hanh.ps1 -Ban 1.6.0 -Dang      (như trên, rồi tạo tag + release trên GitHub)
# Muốn đổi cách phát hành này phải báo chủ dự án trước.
param(
    [Parameter(Mandatory = $true)][string]$Ban,
    [switch]$Dang
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if ($Ban -notmatch '^\d+\.\d+\.\d+$') { throw "Số bản phải dạng 1.6.0, nhận được '$Ban'" }
$tag = "v$Ban"

# 1. Số bản trong mã phải bằng số bản sắp phát hành (bộ cập nhật của máy người dùng so tag với PHIEN_BAN)
$dongBan = Select-String -Path doc_voice.py -Pattern '^PHIEN_BAN = "(.+)"'
$hienTai = $dongBan.Matches[0].Groups[1].Value
if ($hienTai -ne $Ban) {
    $noi = [System.IO.File]::ReadAllText("$PSScriptRoot\doc_voice.py", [System.Text.Encoding]::UTF8)
    $noi = $noi.Replace("PHIEN_BAN = `"$hienTai`"", "PHIEN_BAN = `"$Ban`"")
    [System.IO.File]::WriteAllText("$PSScriptRoot\doc_voice.py", $noi, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "Đã đổi PHIEN_BAN $hienTai -> $Ban trong doc_voice.py (nhớ commit)."
}
# tag mới phải lớn hơn bản mới nhất đang phát hành, không thì máy người dùng không thấy là bản mới
$moiNhat = (& gh release view --repo ducdg88/doc-voice --json tagName --jq .tagName 2>$null)
if ($moiNhat) {
    $a = [version]($moiNhat.TrimStart('v')); $b = [version]$Ban
    if ($b -le $a) { throw "Bản $Ban không lớn hơn bản đang phát hành $moiNhat" }
}
if (-not (Test-Path "docs\RELEASE-$tag.md")) { throw "Thiếu docs\RELEASE-$tag.md (ghi chú phát hành song ngữ Việt và Anh)" }
$ghiChu = Get-Content "docs\RELEASE-$tag.md" -Raw -Encoding UTF8
if ($ghiChu -match [char]0x2013 -or $ghiChu -match [char]0x2014) { throw "Ghi chú phát hành có gạch dài, xoá trước khi phát hành" }

# 2. Kiểm: cả bộ test một lần
$env:PYTHONUTF8 = '1'
& .\.venv\Scripts\python.exe -m unittest kiem_tra_doc_voice
if ($LASTEXITCODE -ne 0) { throw "Test đỏ, không phát hành" }

# 3. Dựng bản Arthur nhúng dấu giọng (không kèm âm thanh mẫu), quét chặn lộ mẫu nằm trong dong-goi.ps1
& powershell -NoProfile -ExecutionPolicy Bypass -File dong-goi.ps1 -ArthurNhung
if ($LASTEXITCODE -ne 0) { throw "Dựng bản phát hành lỗi" }
$exe = Resolve-Path 'dist\arthur\DocVoice.exe'

# 4. sha256 để người dùng tự kiểm; GitHub cũng tự tính digest cho asset, bộ cập nhật dùng digest đó
New-Item -ItemType Directory -Force phat-hanh | Out-Null
$sha = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLower()
[System.IO.File]::WriteAllText("$PSScriptRoot\phat-hanh\DocVoice.exe.sha256", "$sha *DocVoice.exe`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host ("DocVoice.exe v{0}  {1:N1} MB  sha256 {2}" -f $Ban, ((Get-Item $exe).Length / 1MB), $sha)

if (-not $Dang) {
    Write-Host "Chưa đăng (thiếu -Dang). Chạy lại với -Dang để tạo tag $tag và release."
    exit 0
}

# 5. Tạo tag và release: asset PHẢI tên đúng DocVoice.exe (bản cũ tìm đúng tên này), không nháp, không prerelease
$dangCo = (& git status --porcelain)
if ($dangCo) { throw "Còn thay đổi chưa commit, commit rồi chạy lại:`n$dangCo" }
& git tag -a $tag -m "Đọc Voice $tag"
if ($LASTEXITCODE -ne 0) { throw "Tạo tag lỗi" }
& git push origin $tag
if ($LASTEXITCODE -ne 0) { throw "Push tag lỗi" }
& gh release create $tag $exe "phat-hanh\DocVoice.exe.sha256" --repo ducdg88/doc-voice --title "Đọc Voice $tag" --notes-file "docs\RELEASE-$tag.md" --latest
if ($LASTEXITCODE -ne 0) { throw "Tạo release lỗi" }

# 6. Kiểm lại đúng cách bộ cập nhật của máy người dùng sẽ thấy
$rel = & gh api repos/ducdg88/doc-voice/releases/latest | ConvertFrom-Json
$as = $rel.assets | Where-Object { $_.name -eq 'DocVoice.exe' }
if ($rel.tag_name -ne $tag -or -not $as) { throw "Release latest không phải $tag hoặc thiếu asset DocVoice.exe" }
if ($as.digest -and $as.digest -ne "sha256:$sha") { throw "digest GitHub $($as.digest) khác sha256 máy $sha" }
if (-not $as.browser_download_url.StartsWith('https://github.com/ducdg88/doc-voice/releases/download/')) { throw "Link tải không đúng tiền tố mà bộ cập nhật chấp nhận" }
Write-Host "Đã phát hành $tag. Mọi bản từ v1.1.0 trở lên tự thấy bản này khi mở app hoặc bấm Cập nhật."
