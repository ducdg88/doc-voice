# Doc Voice v1.6.0

**Tiếng Việt**

- Giọng Arthur luôn có sẵn và được chọn sẵn ngay khi mở app, kể cả khi chưa tải được danh sách giọng Edge (mất mạng, proxy lỗi). Bản 1.5.0 trên máy mới mà mạng lỗi lúc mở thì ô Ngôn ngữ và ô Giọng trống.
- App tự bỏ proxy trỏ về máy này (127.0.0.1) đã chết lúc mở, nên không còn báo "Cannot connect to host 127.0.0.1:...". Proxy còn sống hoặc proxy ở máy khác vẫn giữ.
- Kiểm chất lượng bản đọc: ĐẠT hoặc KHÔNG ĐẠT kèm mốc thời gian từng lỗi, tự đọc lại phần lỗi, báo cáo json và html. Chạy nhiều kịch bản ra một bảng tổng.
- Kiểm kịch bản trước khi đọc: báo câu dài, ký hiệu lạ, số điện thoại, ngày giờ, viết tắt.
- Đọc số, giờ, ngày, điện thoại, đơn vị chuẩn hơn; đọc lại phần lỗi bằng biến thể khác thay vì lặp đúng âm thanh cũ.
- Bộ kiểm đọc (whisper) không còn làm app đứng ở "Bật bộ kiểm đọc": đọc trước file model bằng khối lớn nên nạp nhanh cả khi ổ cứng HDD đang bận, nạp từ bộ nhớ đệm trước, quá thời gian thì bỏ kiểm và vẫn đọc tiếp.
- Dòng tiến độ khi đang đọc: phần trăm, số phần, thời gian đã chạy và còn lại.
- Cập nhật từ 1.1.0 trở lên: app tự lên bản này, giữ nguyên giọng, cài đặt và lựa chọn của bạn.

**English**

- The Arthur voice is always available and pre-selected as soon as the app opens, even when the Edge voice list cannot be loaded (offline, broken proxy). On 1.5.0 a fresh install that opened without network showed empty Language and Voice boxes.
- The app drops a dead local proxy (127.0.0.1) at startup, so "Cannot connect to host 127.0.0.1:..." no longer appears. Working proxies and proxies on other machines are kept.
- Quality check per take: PASS or FAIL with a timestamp for each defect, automatic re-read of bad parts, json and html reports. Batch scripts produce one summary table.
- Script pre-check before reading: long sentences, odd symbols, phone numbers, dates and times, abbreviations.
- Better reading of numbers, times, dates, phone numbers and units; bad parts are re-read with a variation instead of the same audio.
- The read checker (whisper) no longer stalls at "Bật bộ kiểm đọc": it pre-reads the model file in large blocks so loading stays fast even on a busy hard disk, loads from cache first, and skips the check after a time limit instead of hanging.
- Progress line while reading: percent, parts, elapsed and remaining time.
- From 1.1.0 on the app updates itself to this version and keeps your voices, settings and choices.

Tải / Download: `DocVoice.exe` trên trang Releases, so sha256 với `DocVoice.exe.sha256`. / `DocVoice.exe` on the Releases page; compare with `DocVoice.exe.sha256`.
