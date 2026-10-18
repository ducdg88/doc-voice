**Tiếng Việt** | [English](#english)

# Đọc Voice

App Windows chuyển văn bản thành giọng nói cho người làm YouTube: giọng Microsoft Edge Neural (hơn 300 giọng, hơn 70 ngôn ngữ) và giọng clone **Arthur có sẵn trong app**, chạy ngay trên máy bạn bằng CPU (không cần card đồ họa). Xuất mp3 và phụ đề srt khớp thời gian, tự cập nhật.

## Điểm nổi bật

- **Giọng Edge Neural**: hơn 300 giọng, có giọng Đa ngôn ngữ như William, Andrew và giọng Việt Hoài My, Nam Minh.
- **Giọng Arthur có sẵn, chế độ Nhẹ (từ bản 1.5.0)**: mở app là được chọn sẵn, đọc bằng CPU, máy văn phòng nào cũng được, không cần GPU, không cần cài Python. Lần đầu dùng app tự tải thêm khoảng 254 MB (model giọng và công cụ âm thanh, có kiểm sha256), cần Internet.
- **Nghe ngay**: bấm Nghe hoặc **Đọc kịch bản YouTube**, khoảng 3 giây sau có tiếng, các câu sau sinh tiếp trong lúc phát. Bấm đúp một câu để nghe từ câu đó.
- **Gói giọng `.dgvoice`**: bấm **Xuất giọng** để đóng một giọng thành file, gửi cho người khác bấm **Nhập giọng (.dgvoice)**. Giọng vừa nhập hiện sẵn, được chọn sẵn và được nhớ cho lần mở sau.
- **Kiểm chất lượng (từ bản 1.6.0)**: bấm **Kiểm chất lượng** sau khi đọc, app nghe lại cả bài và trả **ĐẠT** hoặc **KHÔNG ĐẠT** kèm mốc thời gian từng lỗi (mất tiếng, im bất thường, đọc lặp, thiếu từ, click chỗ ghép, sai giọng), tự đọc lại phần lỗi. **Chạy nhiều kịch bản** chọn nhiều file `.txt`, đọc lần lượt và ra một bảng tổng. Báo cáo `.json` và `.html` nằm cạnh file mp3.
- **Kiểm kịch bản trước khi đọc (từ bản 1.6.0)**: nút **✔ Kiểm kịch bản** chỉ ra câu quá dài, ký hiệu lạ, số điện thoại, ngày giờ, viết tắt dễ đọc sai, bấm đúp để nhảy tới chỗ đó.
- **Kịch bản YouTube một nút**: lọc ghi chú `[B-roll]`, `(pause)`, lấy dòng `##` làm chapters, ra mp3 chuẩn -14 LUFS, srt và chapters.
- Giao diện 2 bảng: bên trái là văn bản đọc, bên phải là bản dịch tiếng Việt tự động (không cần API key), highlight vàng chạy theo câu đang đọc.
- Đọc kịch bản dài hàng chục nghìn ký tự: tự chia đoạn, ghép thành một file mp3. Xuất kèm `.srt` (có kiểu Shorts và TikTok, 6 đến 7 từ mỗi dòng).
- Mở `.txt` và Word `.docx`. Chỉnh cao độ, tốc độ. Từ điển cách đọc (`AI = ây ai`).
- Tự kiểm bản mới. Nếu đang đọc thì đợi đọc xong mới hỏi, không làm gián đoạn. Cập nhật không đụng vào giọng, cài đặt và lựa chọn của bạn.

## Lưu ý về giọng

Giọng **Arthur** (nam Mỹ, kể chuyện, audiobook) có sẵn trong app và được chọn sẵn khi mở lần đầu; muốn quay lại Arthur bấm **★ Giọng Arthur**. Arthur chỉ chạy ở chế độ **Nhẹ** (CPU). Từ bản 1.6.0 Arthur luôn hiện sẵn ngay khi mở app, kể cả khi chưa tải được danh sách giọng Edge (mất mạng, proxy lỗi). Muốn thêm giọng khác, bạn nhận file `.dgvoice` từ người có quyền dùng giọng đó rồi bấm **Nhập giọng (.dgvoice)**. Chỉ nhập và dùng giọng khi bạn có quyền hợp pháp với giọng đó.

## Tải và dùng

1. Vào [Releases](https://github.com/ducdg88/doc-voice/releases/latest), tải `DocVoice.exe`. Muốn kiểm file thì so với `DocVoice.exe.sha256` cùng trang (`Get-FileHash DocVoice.exe` trong PowerShell).
2. Đặt vào một thư mục riêng có quyền ghi (ví dụ `Documents\DocVoice`, không đặt trong Program Files), bấm đúp để chạy. Không cần cài Python.
3. File mp3 và srt lưu ở `Documents\Doc Voice` (hoặc thư mục tuỳ chọn trong Cài đặt). Cài đặt, từ điển, giọng đã nhập và văn bản đang gõ lưu ở `%APPDATA%\DocVoice`.
4. Đọc bằng Arthur: mở app, bấm **Nghe** hoặc **Đọc kịch bản YouTube**. Lần đầu app tự tải thêm khoảng 254 MB nên chờ vài phút. Muốn giọng khác: bấm **Nhập giọng (.dgvoice)**, chọn file giọng.

Windows SmartScreen có thể cảnh báo vì exe chưa ký số: bấm **More info**, rồi **Run anyway**.

App cần Internet (giọng Edge lấy từ Microsoft Edge TTS, không cần tài khoản hay khoá; lần đầu dùng Arthur tải khoảng 254 MB). Chế độ Nhẹ cần khoảng 400 MB ổ đĩa trống cho phần cài thêm, CPU 4 nhân trở lên là êm; CPU yếu hơn vẫn chạy nhưng chậm hơn. Dùng cho mục đích cá nhân, không bán lại dịch vụ giọng.

## Tự cập nhật

Từ bản 1.1.0 trở lên, app tự kiểm bản mới khi mở (hoặc bấm **Cập nhật**), tải file, kiểm kích thước và sha256, rồi thay exe và mở lại. Lỗi ở bước nào thì bản đang dùng vẫn nguyên. Bản 1.0.0 (tên cũ Đọc Giọng) không tự lên được vì trỏ vào kho cũ: hãy tải tay bản mới ở trang Releases.

## Tài liệu

- [ARCHITECTURE.md](ARCHITECTURE.md): kiến trúc, quy trình, số đo.
- [docs/PHAT-HANH.md](docs/PHAT-HANH.md): cách phát hành một lệnh.
- [docs/RELEASE-v1.6.0.md](docs/RELEASE-v1.6.0.md): ghi chú bản 1.6.0.

Làm bởi [ducpt.com](https://ducpt.com/?utm_source=github&utm_medium=readme&utm_campaign=doc-voice). Giấy phép MIT.

---

<a id="english"></a>
[Tiếng Việt](#đọc-voice) | **English**

# Doc Voice (Text to Speech)

A Windows app that turns text into speech for YouTube creators: Microsoft Edge Neural voices (300+ voices, 70+ languages) and the **Arthur** clone voice **built into the app**, running on your own PC on the CPU (no graphics card needed). Exports mp3 and time-aligned srt subtitles, and updates itself.

## Highlights

- **Edge Neural voices**: 300+ voices, including multilingual voices such as William and Andrew.
- **Arthur voice built in, Light mode (since 1.5.0)**: selected by default on first launch, runs on the CPU, any office PC works, no GPU and no Python install. On first use the app downloads about 254 MB more (voice model and audio tools, sha256 verified); internet required.
- **Instant listening**: press Listen or **Read YouTube script**; sound starts in about 3 seconds and the next sentences are generated while playing. Double-click a sentence to listen from there.
- **Voice packs `.dgvoice`**: press **Export voice** to pack a voice into a file, and the receiver presses **Import voice (.dgvoice)**. A freshly imported voice shows up, is pre-selected, and is remembered on the next launch.
- **Quality check (since 1.6.0)**: press **Kiểm chất lượng** (Quality check) after a read; the app listens to the whole take and returns PASS or FAIL with a timestamp for every defect (dropout, odd silence, repeat, missing word, click at a joint, wrong voice) and re-reads the bad parts. **Chạy nhiều kịch bản** (Batch scripts) reads several `.txt` files in a row and prints one summary table. Reports (`.json`, `.html`) sit next to the mp3.
- **Script pre-check (since 1.6.0)**: **✔ Kiểm kịch bản** flags very long sentences, odd symbols, phone numbers, dates and times, and abbreviations that are easy to misread; double-click to jump there.
- **One-button YouTube script**: strips `[B-roll]` and `(pause)` notes, turns `##` lines into chapters, outputs mp3 at -14 LUFS, srt and chapters.
- Two-pane layout: source text on the left, automatic Vietnamese translation on the right (no API key), with a yellow highlight following the sentence being read.
- Long scripts (tens of thousands of characters): auto-chunked and merged into a single mp3, with `.srt` (Shorts and TikTok style, 6 to 7 words per line).
- Opens `.txt` and Word `.docx`. Pitch and speed controls. Pronunciation dictionary (`AI = ây ai`).
- Auto-update. If a job is running it waits until the job finishes. Updating never touches your voices, settings or choices.

## About voices

The **Arthur** voice (American male, storytelling, audiobook) is built into the app and pre-selected on first launch; press **★ Arthur voice** to switch back to it. Arthur runs in **Light** mode (CPU) only. Since 1.6.0 Arthur is always there as soon as the app opens, even when the Edge voice list cannot be loaded (offline, broken proxy). To add other voices, get a `.dgvoice` file from someone who has the rights to that voice and press **Import voice (.dgvoice)**. Only import and use voices you are legally allowed to use.

## Download and use

1. Get `DocVoice.exe` from [Releases](https://github.com/ducdg88/doc-voice/releases/latest). To verify it, compare with `DocVoice.exe.sha256` on the same page (`Get-FileHash DocVoice.exe` in PowerShell).
2. Put it in its own writable folder (not Program Files) and double-click. No Python needed.
3. Output goes to `Documents\Doc Voice` (or the folder set in Settings). Settings, dictionary, imported voices and your draft live in `%APPDATA%\DocVoice`.
4. To read with Arthur: open the app and press **Listen** or **Read YouTube script**. On first use the app downloads about 254 MB more, so wait a few minutes. For another voice: press **Import voice (.dgvoice)** and choose the voice file.

SmartScreen may warn because the exe is unsigned: click **More info**, then **Run anyway**.

The app needs internet (Edge voices come from Microsoft Edge TTS, no account or key; the first Arthur use downloads about 254 MB). Light mode needs about 400 MB of free disk for the extra download; a 4-core CPU or better feels smooth, weaker CPUs still work but slower. Personal use only, do not resell the voice service.

## Auto-update

From v1.1.0 on, the app checks for a new version when it opens (or when you press **Update**), downloads it, verifies size and sha256, swaps the exe and relaunches. If any step fails the current version stays untouched. v1.0.0 (old name Doc Giong) cannot self-update because it points at the old repository: download the new version from Releases by hand.

## Docs

- [ARCHITECTURE.md](ARCHITECTURE.md): architecture, flow, measurements.
- [docs/PHAT-HANH.md](docs/PHAT-HANH.md): one-command release process (Vietnamese).
- [docs/RELEASE-v1.6.0.md](docs/RELEASE-v1.6.0.md): 1.6.0 release notes.

Made by [ducpt.com](https://ducpt.com/?utm_source=github&utm_medium=readme&utm_campaign=doc-voice). MIT license.
