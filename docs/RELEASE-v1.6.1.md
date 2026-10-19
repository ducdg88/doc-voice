# Doc Voice v1.6.1

**Tiếng Việt**

- Bản sửa nhỏ, cách dùng app không đổi. Giọng Arthur, chế độ Nhẹ, nghe ngay, kiểm chất lượng chạy như 1.6.0.
- Kiểm mã tự động (CI) trên GitHub xanh trở lại: sửa lỗi kiểm mã làm CI đỏ từ 1.5.0, và từ bản này mọi file mã đều được kiểm mỗi lần đẩy lên, không chỉ một file.
- Chế độ Chuẩn cao (GPU) không còn ghi cứng thư mục của một máy: app tự tìm thư mục DG Voice Studio ở gốc các ổ, hoặc dùng thư mục chọn trong Cài đặt, hoặc biến môi trường `DOCVOICE_STUDIO`. Python của VoxCPM lấy từ `config.json` của DG Voice Studio (khoá `voxcpm_python`) hoặc biến `DOCVOICE_VOXCPM_PYTHON`; thiếu thì app báo rõ cách đặt.
- Tầng nghe lại của kiểm chất lượng tìm python có faster-whisper qua biến `DOCVOICE_PYTHON_KIEM` hoặc python trên PATH.
- Bấm Duyệt chọn thư mục DG Voice Studio rồi bấm Huỷ thì ô giữ nguyên, không bị đổi sang thư mục đang mở.
- Cập nhật từ 1.1.0 trở lên: app tự lên bản này, giữ nguyên giọng, cài đặt và lựa chọn của bạn.

**English**

- A small maintenance release; the app works the same way. Arthur, the Light engine, instant play and the quality check behave as in 1.6.0.
- The automatic code check (CI) on GitHub is green again: the lint error that kept CI red since 1.5.0 is fixed, and from now on every source file is checked on each push, not just one.
- The High quality (GPU) engine no longer hard-codes a folder from one PC: the app looks for the DG Voice Studio folder at the root of each drive, or uses the folder picked in Settings, or the `DOCVOICE_STUDIO` environment variable. The VoxCPM Python comes from DG Voice Studio's `config.json` (`voxcpm_python` key) or `DOCVOICE_VOXCPM_PYTHON`; when missing, the app says how to set it.
- The quality check's listening stage finds a Python with faster-whisper through `DOCVOICE_PYTHON_KIEM` or the Python on PATH.
- Pressing Browse for the DG Voice Studio folder and then Cancel keeps the box as it was instead of switching to the current folder.
- From 1.1.0 on the app updates itself to this version and keeps your voices, settings and choices.

Tải / Download: `DocVoice.exe` trên trang Releases, so sha256 với `DocVoice.exe.sha256`. / `DocVoice.exe` on the Releases page; compare with `DocVoice.exe.sha256`.
