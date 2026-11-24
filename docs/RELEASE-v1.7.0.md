## Đọc Voice v1.7.0

Tiếng Việt | [English](#english)

Bản này gồm cả các thay đổi của 1.6.2 (chưa phát hành riêng). Mở app là tự cập nhật, giữ nguyên giọng đã nhập, ngôn ngữ, cài đặt.

### Mới
- **Giọng Tây Ban Nha có sẵn**: "★ Tây Ban Nha (clone)" đi kèm app (chỉ dấu giọng 1 KB, chế độ Nhẹ, không kèm âm thanh mẫu).
- **Giọng clone đọc đa ngôn ngữ, đúng phát âm bản địa**: chọn ngôn ngữ ở ô Ngôn ngữ (ví dụ Español México), giọng clone đọc bằng giọng nguồn Edge bản địa của ngôn ngữ đó rồi mới đổi sang chất giọng clone. Bộ kiểm nghe lại chạy đúng ngôn ngữ.
- **22 vùng tiếng Tây Ban Nha** có tên rõ: España, México, EE.UU., Argentina, Colombia, Chile, Perú... Thêm Português (PT).
- **Thư mục Voice Training, nút Nạp Voice**: nạp giọng từ file âm thanh (.wav, .mp3, .m4a, .flac, .ogg) hoặc gói .dgvoice; nhận giọng từ DG Voice Studio nếu máy có.
- **Thư mục kết quả hiện ngay trên giao diện**, đọc xong hỏi mở thư mục. Logo và icon mới.

### Sửa lỗi
- Đóng app khi đang đọc không còn làm app văng: mọi luồng nền ngừng gọi giao diện khi cửa sổ đã đóng.
- Nút "Thư mục Voice Training" mở đúng kho giọng của app (trước đó trong exe mở nhầm thư mục tạm).

---

### English

This release also contains the unreleased 1.6.2 changes. The app updates itself on launch and keeps your imported voices, language and settings.

- **Spanish voice built in**: "★ Tây Ban Nha (clone)" ships with the app (1 KB voice fingerprint, Light mode, no audio sample).
- **Clone voices read any language with native pronunciation**: pick a language (for example Español México); the clone reads with a native Edge source voice for that language, then converts to the clone timbre. The listening check runs in that language.
- **22 Spanish regions** with clear names, plus Português (PT).
- **Voice Training folder and Load Voice button**: load a voice from an audio file or a .dgvoice pack; DG Voice Studio voices are detected when present.
- **Output folder shown in the main window**, with an offer to open it when done. New logo and icon.
- Fixed: closing the app while reading no longer crashes it. The Voice Training button now opens the real voice folder.
