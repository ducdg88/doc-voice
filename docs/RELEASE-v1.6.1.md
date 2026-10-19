# Doc Voice v1.6.1

**Tiếng Việt**

- Bản sửa nhỏ, cách dùng app không đổi. Giọng Arthur, chế độ Nhẹ, nghe ngay, kiểm chất lượng chạy như 1.6.0.
- Kiểm mã tự động (CI) trên GitHub xanh trở lại: sửa lỗi kiểm mã làm CI đỏ từ 1.5.0, và từ bản này mọi file mã đều được kiểm mỗi lần đẩy lên, không chỉ một file.
- Chế độ Chuẩn cao (GPU) không còn ghi cứng thư mục của một máy: app tự tìm thư mục DG Voice Studio ở gốc các ổ, hoặc dùng thư mục chọn trong Cài đặt, hoặc biến môi trường `DOCVOICE_STUDIO`. Python của VoxCPM lấy từ `config.json` của DG Voice Studio (khoá `voxcpm_python`) hoặc biến `DOCVOICE_VOXCPM_PYTHON`; không đặt thì app tự tìm python mà DG Voice Studio dùng, hoặc venv có VoxCPM ở gốc các ổ, không cần đặt gì thêm. Không tìm ra thì app báo rõ cách đặt.
- Tầng nghe lại của kiểm chất lượng tự tìm python có faster-whisper (ưu tiên python có cả resemblyzer để đo độ giống giọng): venv của `cai-kiem-sau.ps1`, biến `DOCVOICE_PYTHON_KIEM`, venv ở gốc các ổ, rồi python trên PATH.
- Kiểm chất lượng trên máy không có resemblyzer không còn dừng giữa chừng khi ghi báo cáo. Giọng Arthur đi kèm app (chỉ có dấu giọng, không có mẫu âm thanh) không còn bị chấm sai giọng: báo cáo ghi chưa đo độ giống với mẫu và vẫn đo giọng từng câu.
- Bấm Duyệt chọn thư mục DG Voice Studio rồi bấm Huỷ thì ô giữ nguyên, không bị đổi sang thư mục đang mở.
- Cập nhật từ 1.1.0 trở lên: app tự lên bản này, giữ nguyên giọng, cài đặt và lựa chọn của bạn.

**English**

- A small maintenance release; the app works the same way. Arthur, the Light engine, instant play and the quality check behave as in 1.6.0.
- The automatic code check (CI) on GitHub is green again: the lint error that kept CI red since 1.5.0 is fixed, and from now on every source file is checked on each push, not just one.
- The High quality (GPU) engine no longer hard-codes a folder from one PC: the app looks for the DG Voice Studio folder at the root of each drive, or uses the folder picked in Settings, or the `DOCVOICE_STUDIO` environment variable. The VoxCPM Python comes from DG Voice Studio's `config.json` (`voxcpm_python` key) or `DOCVOICE_VOXCPM_PYTHON`; when neither is set, the app finds the Python DG Voice Studio itself uses, or a venv with VoxCPM at the root of a drive, with nothing to set. If none is found, the app says how to set it.
- The quality check's listening stage finds a Python with faster-whisper by itself (preferring one that also has resemblyzer for the voice similarity score): the `cai-kiem-sau.ps1` venv, `DOCVOICE_PYTHON_KIEM`, a venv at the root of a drive, then the Python on PATH.
- The quality check no longer stops while writing the report on a PC without resemblyzer. The built-in Arthur voice (a voice fingerprint, no audio sample) is no longer marked as the wrong voice: the report says the match with the sample was not measured and still checks the voice sentence by sentence.
- Pressing Browse for the DG Voice Studio folder and then Cancel keeps the box as it was instead of switching to the current folder.
- From 1.1.0 on the app updates itself to this version and keeps your voices, settings and choices.

Tải / Download: `DocVoice.exe` trên trang Releases, so sha256 với `DocVoice.exe.sha256`. / `DocVoice.exe` on the Releases page; compare with `DocVoice.exe.sha256`.
