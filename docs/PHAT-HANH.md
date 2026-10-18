# Phát hành Đọc Voice (một lệnh)

Cách phát hành này do chủ dự án chốt cho Đọc Voice. **Muốn đổi cách này phải báo chủ dự án trước.**

## Quy tắc đã chốt (không tự ý đổi)

1. Bản đưa lên GitHub luôn là **bản Arthur nhúng dấu giọng**: dựng bằng `dong-goi.ps1 -ArthurNhung`, ra `dist\arthur\DocVoice.exe`. Exe chỉ nhúng dấu giọng `giong_nhung\arthur\se.npy` (256 số, 1 KB, tạo bằng `tao_dau_giong.py`, từ 1.6.0 nằm trong git để ai clone kho cũng có Arthur làm giọng mặc định), chỉ chế độ Nhẹ dùng được. Tuyệt đối không chứa mẫu âm thanh `ref.wav`, `ref.txt`, `giong_mau`, `*.dgvoice`. Bước quét `quet-ban-team.ps1` chạy trong `dong-goi.ps1`, thấy một trong các thứ đó thì xoá exe và dừng. Bản `-KhongGiongMau` (không giọng nào) vẫn dựng được để gửi riêng cho một nhóm, nhưng không phải bản phát hành.
2. Không bao giờ đưa lên Releases bản đóng gói có kèm mẫu âm thanh (bản dựng không kèm cờ nào, `dist\DocVoice.exe`).
3. Tên asset phải đúng `DocVoice.exe`, tag dạng `v1.6.0`, release không nháp, không prerelease. Bản cũ chỉ tìm đúng tên này trong `releases/latest`.
4. Tự cập nhật không đè lựa chọn người dùng: giọng đã chọn và nhập, ngôn ngữ, mức cảm xúc, thư mục xuất, cài đặt, `giong_clone\`, `cai_dat.json` đều nằm ở `%APPDATA%\DocVoice`; bộ cập nhật chỉ thay file exe. Mặc định chỉ đặt một lần qua cờ `da_gioi_thieu_*`. Test bảo vệ nằm trong lớp `KiemTraGiongNhapVaGiuLuaChon` của `kiem_tra_doc_voice.py`.
5. Không dùng GPU, không bật tiếng khi kiểm. Một tiến trình nặng một lúc, test không chạy song song.

## Phát hành một lệnh

Chuẩn bị (kiểm, test, dựng bản Arthur nhúng dấu giọng, sha256, chưa đăng gì):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File phat-hanh.ps1 -Ban 1.6.0
```

Đăng (thêm `-Dang`; cần `gh auth login` bằng tài khoản có quyền ghi kho `ducdg88/doc-voice`):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File phat-hanh.ps1 -Ban 1.6.0 -Dang
```

Trước khi chạy `-Dang` lần đầu của mỗi bản: mở exe vừa dựng trong APPDATA tách biệt (đặt `APPDATA` và `LOCALAPPDATA` sang thư mục tạm), đọc thử một câu bằng Edge và một câu chế độ Nhẹ với gói giọng thử không phải Arthur, và kiểm mục lục gói exe không có `ref.wav`, `ref.txt`, `*.dgvoice`.

Script không làm gì ngoài các bước kiểm số bản, kiểm `docs\RELEASE-v<Ban>.md`, chạy test, dựng và quét bản phát hành, tính sha256; chỉ khi có `-Dang` mới tạo tag và release, rồi đọc lại `releases/latest` để đối chiếu tag, tên asset, digest và tiền tố link tải.

## Bản cũ nào tự lên được

Bộ cập nhật (`kiem_ban_moi`, `thay_exe`) giống nhau từng ký tự từ v1.1.0 đến v1.5.0 (đã so bằng ast). Đường đi: gọi `https://api.github.com/repos/ducdg88/doc-voice/releases/latest`, so `tag_name` với `PHIEN_BAN` theo từng số (1.10 lớn hơn 1.9), tìm asset tên `DocVoice.exe` có link bắt đầu `https://github.com/ducdg88/doc-voice/releases/download/`, tải về, kiểm kích thước, sha256 (digest do GitHub tính) và đầu file `MZ`, đổi tên exe đang chạy thành `DocVoice.old.exe`, đặt exe mới vào chỗ cũ rồi mở lại; lỗi ở bước nào thì trả exe cũ về. Bản mới mở lên tự xoá `DocVoice.old.exe`.

| Bản | Tự lên được? | Ghi chú |
|---|---|---|
| v1.1.0 trở lên | Có | Lớp test `KiemTraTuCapNhat` (mock, không ghi mạng) chạy bản trích nguyên văn từ tag v1.1.0 lên v1.5.0, giữ `cai_dat.json` |
| v1.0.0 (Đọc Giọng, `DocGiong.exe`) | **Không** | Trỏ kho `ducdg88/doc-giong`; GitHub chuyển hướng sang `doc-voice` nhưng link tải nằm dưới `doc-voice`, còn bản này chỉ chấp nhận link dưới `doc-giong`, nên luôn thấy "không có bản mới". Không sửa được từ phía bản mới |

Phương án cho v1.0.0 (cần chủ dự án duyệt vì tạo kho công khai mới): tạo kho `ducdg88/doc-giong` mới với release có asset `DocGiong.exe` là bản 1.5.0 (tên cũ khi đó không còn chuyển hướng). Cách nhẹ hơn: báo người dùng v1.0.0 (nếu có) tải tay ở trang Releases. v1.0.0 phát hành 14/10/2026 nên số người dùng thực tế rất ít.

## Khi phát hành nhầm

Đăng ngay bản số cao hơn đã sửa; không xoá asset đã có người tải. Chỉ gỡ (`gh release delete vX.Y.Z --cleanup-tag`) khi chủ dự án yêu cầu.
