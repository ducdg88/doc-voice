import gc as _gc
import json
import os
import re
import shutil
import subprocess
import tkinter
import unittest
import weakref
from io import BytesIO
from pathlib import Path
from shutil import which
from subprocess import run
from tempfile import TemporaryDirectory
from subprocess import CREATE_NO_WINDOW
from unittest.mock import patch
import numpy as np

import doc_voice
import giong_clone as gc
import kiem_chat_luong as kcl

_TCL_DA_MO = []     # bộ thông dịch Tcl của mọi App test đã mở, giữ tới khi tiến trình thoát


def mo_app_an(test):
    """Mở App ẩn cho một test, đóng khi test xong. Mọi App trong file này phải mở qua đây.

    App Tk là rác vòng (widget, biến Tk trỏ ngược về App) nên chỉ bộ gom rác vòng dọn được, và nó chạy ở luồng
    nào đang cấp phát. Chạy ở luồng phụ (DocDaiClone, luồng _lam_nong nạp máy đọc Nhẹ khi chọn Arthur trên máy đã
    cài) thì bộ thông dịch Tcl bị huỷ sai luồng và cả tiến trình chết: "Tcl_AsyncDelete: async handler deleted by
    the wrong thread". Nên giữ bộ thông dịch tới khi tiến trình thoát (huỷ ở luồng chính), không nạp máy đọc thật
    ở luồng nền trong test, đóng App rồi gom rác ngay ở luồng chính."""
    tat_nong = patch.object(doc_voice.App, "_lam_nong", lambda self: None)
    tat_nong.start()
    test.addCleanup(tat_nong.stop)
    _gc.collect()                       # App đã đóng trước đó (kể cả _thoat() rồi mở lại): dọn ở luồng chính
    app = doc_voice.App()
    _TCL_DA_MO.append(app.tk)
    app.withdraw()
    test.addCleanup(_dong_app, weakref.ref(app))
    app.update()
    return app


def _dong_app(ref):
    app = ref()
    if app is not None:
        try:
            app.destroy()
        except tkinter.TclError:        # đã đóng bằng _thoat()
            pass
    del app
    _gc.collect()


def gia_lap_edge(chu_doan, tu_moi_giay=3.2, im_lang_cuoi=0.06):
    """Giả lập một lượt Edge: chia câu như SentenceBoundary, mp3 dài hơn câu cuối một đoạn im lặng."""
    cau, t = [], 0.0
    for c in re.split(r"(?<=[.!?。！？…])\s+|\n+", chu_doan):
        if not c.strip():
            continue
        dai = len(c.split()) / tu_moi_giay
        cau.append((t, dai, c.strip()))
        t += dai
    return b"\0" * int(round(6000 * (t + im_lang_cuoi))), cau


def giay_srt(x):
    return int(x[:2]) * 3600 + int(x[3:5]) * 60 + int(x[6:8]) + int(x[9:]) / 1000


def doc_srt(srt):
    """({chữ cue: giây bắt đầu}, giây kết thúc cue cuối) đọc từ chuỗi SRT."""
    moc, cuoi = {}, 0.0
    for khoi in srt.strip().split("\n\n"):
        dong = khoi.split("\n")
        a, b = dong[1].split(" --> ")
        moc.setdefault(dong[2], giay_srt(a))
        cuoi = giay_srt(b)
    return moc, cuoi


def giay_yt(chuoi):
    p = [int(x) for x in chuoi.split(":")]
    return p[0] * 60 + p[1] if len(p) == 2 else p[0] * 3600 + p[1] * 60 + p[2]


class KiemTraDocVoice(unittest.TestCase):
    def test_so_phien_ban(self):
        self.assertEqual(doc_voice.so_phien_ban("1.1.0"), (1, 1, 0))
        self.assertEqual(doc_voice.so_phien_ban("v1.2.3"), (1, 2, 3))
        self.assertTrue(doc_voice.so_phien_ban("1.2.0") > doc_voice.so_phien_ban("1.1.0"))

    def test_chia_doan(self):
        vb = "Hello world! This is a test. Another sentence."
        doan = doc_voice.chia_doan(vb, toi_da=30)
        self.assertTrue(len(doan) >= 2)
        for d in doan:
            self.assertTrue(len(d) <= 30)

    def test_tu_dien_va_ban_do_cau(self):
        td = [("AI", "ây ai"), ("TP.HCM", "thành phố Hồ Chí Minh")]
        vb = "Tôi đang học AI tại TP.HCM. AI rất hay."
        doc = doc_voice.ap_tu_dien(vb, td)
        self.assertIn("ây ai", doc)
        self.assertIn("thành phố Hồ Chí Minh", doc)
        self.assertNotIn("AI", doc)

        bd = doc_voice.ban_do_cau(vb, td)
        self.assertIn(doc_voice.chuan_cau(doc_voice.ap_tu_dien("Tôi đang học AI tại TP.HCM.", td)), bd)
        self.assertEqual(bd[doc_voice.chuan_cau(doc_voice.ap_tu_dien("Tôi đang học AI tại TP.HCM.", td))],
                         doc_voice.chuan_cau("Tôi đang học AI tại TP.HCM."))

    def test_lay_danh_sach_cau(self):
        # ket_qua giả lập: 1 đoạn mp3 độ dài 6000 bytes (1s), 2 câu
        fake_mp3 = b"\x00" * 6000
        fake_cau = [(0.0, 0.4, "Hello world."), (0.5, 0.4, "Welcome back.")]
        ket_qua = [(fake_mp3, fake_cau)]
        cau_list = doc_voice.lay_danh_sach_cau(ket_qua)
        self.assertEqual(len(cau_list), 2)
        self.assertEqual(cau_list[0], (0.0, 0.4, "Hello world."))
        self.assertEqual(cau_list[1], (0.5, 0.9, "Welcome back."))

    def test_tinh_do_dai_doan(self):
        fake_cau = [(0.0, 1.2, "Câu 1."), (1.5, 2.0, "Câu 2.")]
        # Có mp3 thì độ dài theo byte mp3 thật (6000 byte một giây), kể cả im lặng cuối đoạn
        dai = doc_voice.tinh_do_dai_doan(b"\x00" * 21000, fake_cau)
        self.assertAlmostEqual(dai, 3.5)
        dai_im = doc_voice.tinh_do_dai_doan(b"\x00" * 12000, [(0.0, 1.5, "Câu 1.")])
        self.assertAlmostEqual(dai_im, 2.0)
        # mp3 rỗng thì dự phòng theo mốc câu
        self.assertAlmostEqual(doc_voice.tinh_do_dai_doan(b"", fake_cau), 3.5)
        self.assertAlmostEqual(doc_voice.tinh_do_dai_doan(b"", []), 0.0)

    def test_dich_tieng_viet(self):
        cau_goc = "Hello world"
        phan_hoi = BytesIO(b'[[["Xin chao the gioi", "Hello world", null, null, 1.0]] ]')
        with patch.object(doc_voice.urllib.request, "urlopen", return_value=phan_hoi):
            dich = doc_voice.dich_tieng_viet(cau_goc)
        self.assertEqual(dich, "Xin chao the gioi")

    def test_loc_kich_ban_youtube(self):
        vb_goc = (
            "## Giới thiệu\n"
            "Xin chào các bạn! [B-roll: bãi biển tuyệt đẹp]\n"
            "Hôm nay chúng ta sẽ tìm hiểu về lịch sử (năm 1995) của máy tính. (cười nhẹ)\n"
            "**Hãy bấm đăng ký kênh nhé!** [nhạc kết thúc: vui tươi]"
        )
        sach, dem = doc_voice.loc_kich_ban_youtube(vb_goc)
        self.assertTrue(dem >= 3)
        self.assertNotIn("[B-roll: bãi biển tuyệt đẹp]", sach)
        self.assertNotIn("[nhạc kết thúc: vui tươi]", sach)
        self.assertNotIn("(cười nhẹ)", sach)
        self.assertNotIn("**", sach)
        self.assertNotIn("##", sach)
        # Giữ lại ngoặc tròn bình thường không phải chỉ dẫn đạo diễn
        self.assertIn("(năm 1995)", sach)
        self.assertIn("Hãy bấm đăng ký kênh nhé!", sach)

        # Sau lọc, dòng tiêu đề giữ một dấu # cho Chapters, nhưng chữ đưa cho giọng đọc KHÔNG có # (xuống dòng thật).
        nguon = "# Intro\nHello world.\n## Second\nMore text."
        self.assertEqual(len(nguon.splitlines()), 4)
        sach_chapters, _ = doc_voice.loc_kich_ban_youtube(nguon)
        self.assertEqual(sach_chapters.splitlines(), ["# Intro", "Hello world.", "# Second", "More text."])
        cau = [(0.0, 1.0, "Intro."), (1.0, 14.0, "Hello world."), (15.0, 16.0, "Second."), (16.0, 30.0, "More text.")]
        chapters = doc_voice.trich_xuat_chapters(sach_chapters, cau, 0)
        self.assertEqual(chapters.splitlines(), ["00:00 Intro", "00:15 Second"])
        doc = doc_voice.chuan_bi_van_ban_doc(sach_chapters)
        self.assertNotIn("#", doc)
        self.assertEqual(doc.splitlines(), ["Intro.", "Hello world.", "Second.", "More text."])

    def test_chu_doc_va_phu_de_khong_co_dau_thang(self):
        vb = "## Mở đầu\nXin chào các bạn.\n# Kết\nHẹn gặp lại."
        sach, _ = doc_voice.loc_kich_ban_youtube(vb)
        for nguon in (vb, sach):
            doc = doc_voice.chuan_bi_van_ban_doc(nguon)
            self.assertNotIn("#", doc)
            ket_qua = [gia_lap_edge(d) for d in doc_voice.chia_doan(doc)]
            self.assertNotIn("#", doc_voice.tao_srt(ket_qua))
            cau_list = doc_voice.lay_danh_sach_cau(ket_qua)
            self.assertTrue(all("#" not in c[2] for c in cau_list))
            # Chapters vẫn lấy được tiêu đề từ bản có # và mốc từ câu tiêu đề thật
            chapters = doc_voice.trich_xuat_chapters(nguon, cau_list, 0)
            self.assertEqual([d.split(" ", 1)[1] for d in chapters.splitlines()], ["Mở đầu", "Kết"])
        # Dòng không phải tiêu đề được giữ nguyên
        self.assertEqual(doc_voice.chuan_bi_van_ban_doc("#hashtag vui\nXin chào."), "#hashtag vui\nXin chào.")

    def test_chia_cau_shorts(self):
        cau = "Một hai ba bốn năm sáu bảy tám chín mười"
        cum = doc_voice.chia_cau_shorts(cau, max_tu=7)
        self.assertEqual(len(cum), 2)
        self.assertEqual(cum[0], "Một hai ba bốn năm sáu bảy")
        self.assertEqual(cum[1], "tám chín mười")

        # Thử nghiệm câu ít từ nhưng ký tự dài
        cau_dai = "TríTuệNhânTạoĐangPhátTriểnCựcKỳNhanhChóngVượtBậc TrênToànThếGiới"
        cum_dai = doc_voice.chia_cau_shorts(cau_dai, max_tu=7, max_ky_tu=38)
        self.assertTrue(len(cum_dai) >= 2)
        self.assertEqual(cum_dai[0], "TríTuệNhânTạoĐangPhátTriểnCựcKỳNhanhChóngVượtBậc")
        self.assertEqual(cum_dai[1], "TrênToànThếGiới")

    def test_tao_srt_kieu_shorts(self):
        fake_mp3 = b"\x00" * 6000
        fake_cau = [(0.0, 3.0, "Đây là câu dài hơn bảy từ để thử nghiệm cắt ngắn phụ đề shorts.")]
        ket_qua = [(fake_mp3, fake_cau)]
        srt_shorts = doc_voice.tao_srt(ket_qua, kieu_shorts=True)
        # Phải được chia làm ít nhất 2 phụ đề
        self.assertIn("-->", srt_shorts)
        self.assertIn("1\n", srt_shorts)
        self.assertIn("2\n", srt_shorts)

    def test_tao_khoang_lang_mp3(self):
        self.assertEqual(doc_voice.tao_khoang_lang_mp3(0), b"")
        self.assertEqual(doc_voice.tao_khoang_lang_mp3(-1), b"")
        mp3_lang = doc_voice.tao_khoang_lang_mp3(1.0)
        # 1.0 giây khoảng 41 hoặc 42 frames, mỗi frame 144 bytes
        self.assertTrue(len(mp3_lang) > 0)
        self.assertEqual(len(mp3_lang) % 144, 0)
        # Kiểm tra header MPEG-2 Layer 3 24 kHz và khả năng giải mã thực tế.
        self.assertTrue(mp3_lang.startswith(b"\xff\xf3\x64\xc4"))
        # Frame silence phải giải mã được, kể cả khi đứng riêng hoặc đứng đầu file.
        ffprobe = which("ffprobe")
        if not ffprobe:
            self.skipTest("Không có ffprobe trong PATH")
        with TemporaryDirectory() as thu_muc:
            for ten, du_lieu, toi_thieu in (
                ("pause.mp3", mp3_lang, 0.9),
                ("pause-dau.mp3", mp3_lang + mp3_lang, 1.8),
            ):
                duong_dan = Path(thu_muc) / ten
                duong_dan.write_bytes(du_lieu)
                probe = run(
                    [ffprobe, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(duong_dan)],
                    capture_output=True, text=True, check=False, creationflags=CREATE_NO_WINDOW,
                )
                self.assertEqual(probe.returncode, 0, probe.stderr)
                self.assertGreater(float(probe.stdout.strip()), toi_thieu)

    def test_tach_doan_va_pause(self):
        vb = "Chào các bạn! [nghỉ 1.5s] Đây là phần tiếp theo. (pause 2s) Cảm ơn đã xem."
        cac_phan = doc_voice.tach_doan_va_pause(vb)
        self.assertEqual(len(cac_phan), 5)
        self.assertEqual(cac_phan[0], ("text", "Chào các bạn!"))
        self.assertEqual(cac_phan[1], ("pause", 1.5))
        self.assertEqual(cac_phan[2], ("text", "Đây là phần tiếp theo."))
        self.assertEqual(cac_phan[3], ("pause", 2.0))
        self.assertEqual(cac_phan[4], ("text", "Cảm ơn đã xem."))

    def test_trich_xuat_chapters(self):
        vb = (
            "# Khởi đầu hành trình\n"
            "Xin chào các bạn đã quay trở lại với kênh của chúng tôi hôm nay.\n"
            "## Nguyên nhân cốt lõi\n"
            "Trong phần này chúng ta sẽ phân tích lý do công nghệ phát triển mạnh.\n"
            "### Giải pháp tương lai\n"
            "Đây là các bước để áp dụng trí tuệ nhân tạo vào thực tế."
        )
        fake_cau = [
            (0.0, 15.0, "Xin chào các bạn đã quay trở lại với kênh của chúng tôi hôm nay."),
            (15.0, 35.0, "Trong phần này chúng ta sẽ phân tích lý do công nghệ phát triển mạnh."),
            (35.0, 60.0, "Đây là các bước để áp dụng trí tuệ nhân tạo vào thực tế.")
        ]
        chapters = doc_voice.trich_xuat_chapters(vb, fake_cau, toc_do=0)
        self.assertTrue(chapters.startswith("00:00"))
        self.assertIn("00:00 Khởi đầu hành trình", chapters)
        self.assertIn("00:15 Nguyên nhân cốt lõi", chapters)
        self.assertIn("00:35 Giải pháp tương lai", chapters)

        chapters_gan = doc_voice.trich_xuat_chapters(
            "# Một\n"
            "Câu một.\n"
            "## Hai\n"
            "Câu hai.\n"
            "### Ba\n"
            "Câu ba.",
            [(0.0, 4.0, "Câu một."), (4.0, 8.0, "Câu hai."), (8.0, 12.0, "Câu ba.")],
            toc_do=0,
        )
        self.assertIn("00:00 Một", chapters_gan)
        self.assertIn("00:10 Hai", chapters_gan)
        self.assertIn("00:20 Ba", chapters_gan)

        vb_tien_to = (
            "Phần 1: Mở đầu\n"
            "Nội dung giới thiệu.\n"
            "Chương 2 - Phân tích\n"
            "Nội dung phân tích."
        )
        chapters_tien_to = doc_voice.trich_xuat_chapters(
            vb_tien_to,
            [(0.0, 10.0, "Nội dung giới thiệu."), (10.0, 20.0, "Nội dung phân tích.")],
            toc_do=0
        )
        self.assertEqual(chapters_tien_to.splitlines(), ["00:00 Mở đầu", "00:10 Phân tích"])

    def test_chapters_chi_nhan_tieu_de_dung_mau(self):
        thuong = (
            "Phần lớn mọi người không biết điều này.\n"
            "Mục tiêu của video hôm nay rất đơn giản.\n"
            "1. Mua gỗ tốt\n"
            "2. Cắt gỗ\n"
            "Part of the problem is simple.\n"
            "Part 2 of the plan is easy.\n"
            "#hashtag không phải tiêu đề."
        )
        self.assertEqual(doc_voice.trich_xuat_chapters(thuong, None, 0), "")
        for dong, ten in (("Phần 1: Mở đầu", "Mở đầu"), ("Chương 2 - Phân tích", "Phân tích"),
                          ("Part 3", "Part 3"), ("Chapter 4: End", "End"), ("Mục: Kết luận", "Kết luận"),
                          ("### Tiêu đề **đậm**", "Tiêu đề đậm")):
            td = doc_voice.nhan_dien_tieu_de(dong)
            self.assertIsNotNone(td, dong)
            self.assertEqual(td[0], ten)
        # Chữ thường lẫn với tiêu đề thật: chỉ tiêu đề thật ra mốc
        hon_hop = ("Phần lớn là chữ thường và đứng trước tiêu đề đầu tiên, dài hơn nhiều so với mười hai từ.\n# Thật\n1. Mua gỗ\nChương 2: Hai")
        kq = doc_voice.trich_xuat_chapters(hon_hop, None, 0)
        self.assertEqual([d.split(" ", 1)[1] for d in kq.splitlines()], ["Giới thiệu", "Thật", "Hai"])

    def test_tao_srt_song_ngu(self):
        fake_cau = [
            (0.0, 4.0, "Hello world, welcome to our AI channel."),
            (4.0, 8.0, "Today we will explore new technology.")
        ]
        ban_dich = "Xin chào các bạn, chào mừng đến kênh AI. Hôm nay chúng ta cùng khám phá công nghệ mới."
        srt_vi = doc_voice.tao_srt_song_ngu(fake_cau, ban_dich, kieu_shorts=False)
        self.assertIn("00:00:00,000 --> 00:00:04,000", srt_vi)
        self.assertIn("Xin chào các bạn", srt_vi)
        self.assertIn("00:00:04,000 --> 00:00:08,000", srt_vi)
        self.assertIn("khám phá công nghệ mới", srt_vi)

    def test_tao_srt_song_ngu_giu_pause_khi_lech_so_cau(self):
        cau_list = [
            (0.0, 2.0, "Câu đầu."),
            (4.0, 6.0, "Câu sau."),
        ]
        srt_vi = doc_voice.tao_srt_song_ngu(
            cau_list,
            "Bản dịch gộp cả hai câu thành một đoạn.",
        )
        self.assertIn("00:00:00,000 --> 00:00:02,000", srt_vi)
        self.assertIn("00:00:04,000 --> 00:00:06,000", srt_vi)
        self.assertNotIn("00:00:02,000 --> 00:00:04,000", srt_vi)

    def test_chapters_khop_srt_kich_ban_dai(self):
        mau = "This sentence has exactly ten words in it for testing."
        dong = []
        for i in range(1, 9):
            dong.append(f"# Section {i}")
            dong += [mau] * 15
        vb = "\n".join(dong)
        doc = doc_voice.chuan_bi_van_ban_doc(vb)
        self.assertNotIn("#", doc)
        ket_qua = [gia_lap_edge(d, 3.2) for d in doc_voice.chia_doan(doc, 400)]
        self.assertGreater(len(ket_qua), 3)
        moc_srt, cuoi = doc_srt(doc_voice.tao_srt(ket_qua))
        chapters = doc_voice.trich_xuat_chapters(vb, doc_voice.lay_danh_sach_cau(ket_qua), 0)
        muc = [d.split(" ", 1) for d in chapters.splitlines()]
        self.assertEqual(len(muc), 8)
        for i, (tg, ten) in enumerate(muc, 1):
            self.assertEqual(ten, f"Section {i}")
            self.assertLessEqual(abs(giay_yt(tg) - moc_srt[f"Section {i}."]), 1.0, (i, tg))
        # câu cuối kết thúc khớp tổng mp3 trong 0,1 giây
        tong_mp3 = sum(len(m) for m, _ in ket_qua) / doc_voice.BYTE_MOI_GIAY
        self.assertLess(abs(tong_mp3 - cuoi), 0.1)

    def test_chapters_cau_list_khong_co_cau_tieu_de(self):
        # Timeline chỉ có câu nội dung, tốc độ đọc thật 3,2 từ mỗi giây (không phải 2,5): vẫn khớp trong 1 giây
        mau = "This sentence has exactly ten words in it for testing."
        dong, cau, t, moc_that = [], [], 0.0, []
        for i in range(1, 9):
            dong.append(f"# Section {i}")
            moc_that.append(t)
            for _ in range(15):
                dong.append(mau)
                cau.append((t, t + 10 / 3.2, mau))
                t += 10 / 3.2
        chapters = doc_voice.trich_xuat_chapters("\n".join(dong), cau, 0)
        muc = [d.split(" ", 1)[0] for d in chapters.splitlines()]
        self.assertEqual(len(muc), 8)
        for tg, that in zip(muc, moc_that):
            self.assertLessEqual(abs(giay_yt(tg) - that), 1.0, (tg, that))

    def test_dich_va_srt_tieng_viet_khong_co_dau_thang(self):
        vb = ("# Introduction\nHello and welcome to the workshop.\n## Why does wood crack?\n"
              "Wood cracks when moisture changes too fast. Dry it slowly.\n# Conclusion\nThanks for watching.")
        doc = doc_voice.chuan_bi_van_ban_doc(vb)
        cau_goc = [c for c in re.split(doc_voice.MAU_TACH_CAU, doc) if c.strip()]
        self.assertEqual(len(cau_goc), 7)

        def dich_gia(khoi):  # máy dịch giả: mỗi dòng một câu, giữ nguyên số dòng
            return "\n".join("VI " + d for d in khoi.split("\n"))

        with patch.object(doc_voice, "_dich_mot", side_effect=dich_gia):
            # Dù người dùng đưa bản còn # vào hàm dịch, bản dịch vẫn không có # và đúng 1 dòng mỗi câu
            dich = doc_voice.dich_tieng_viet(doc)
            self.assertNotIn("#", doc_voice.dich_tieng_viet(vb))
        self.assertNotIn("#", dich)
        self.assertEqual(dich.splitlines(), ["VI " + c for c in cau_goc])

        ket_qua = [gia_lap_edge(d) for d in doc_voice.chia_doan(doc)]
        cau_list = doc_voice.lay_danh_sach_cau(ket_qua)
        self.assertEqual(len(cau_list), len(cau_goc))
        srt_vi = doc_voice.tao_srt_song_ngu(cau_list, dich)
        self.assertNotIn("#", srt_vi)
        cue = [k.split("\n")[2] for k in srt_vi.strip().split("\n\n")]
        self.assertEqual(cue, ["VI " + c for c in cau_goc])  # số cue khớp bản gốc, không cắt giữa câu
        moc, _ = doc_srt(srt_vi)
        self.assertAlmostEqual(moc["VI Introduction."], cau_list[0][0], delta=0.002)

        # Bản dịch cũ còn # và nối thành một đoạn: SRT vẫn không có #
        cu = "# Giới thiệu Xin chào và chào mừng. ##Tại sao gỗ nứt? Gỗ nứt khi ẩm. # Kết luận Cảm ơn."
        self.assertNotIn("#", doc_voice.tao_srt_song_ngu(cau_list, cu))

    def test_srt_vi_mot_dong_mot_cau_gop_dich_nhieu_cau(self):
        # Một câu gốc dịch ra 2 câu ngắn: vẫn 1 cue cho 1 câu gốc, không cắt hay lệch các cue sau
        cau_list = [(0.0, 2.0, "A."), (3.0, 5.0, "B."), (6.0, 8.0, "C.")]
        dich = "Một. Hai. Ba bốn năm sáu bảy tám chín.\nMười.\n# Mười một"
        srt_vi = doc_voice.tao_srt_song_ngu(cau_list, dich)
        cue = [k.split("\n") for k in srt_vi.strip().split("\n\n")]
        self.assertEqual([(c[1], c[2]) for c in cue], [
            ("00:00:00,000 --> 00:00:02,000", "Một. Hai. Ba bốn năm sáu bảy tám chín."),
            ("00:00:03,000 --> 00:00:05,000", "Mười."),
            ("00:00:06,000 --> 00:00:08,000", "Mười một")])

    def test_dich_gop_dong_thi_dich_tung_cau(self):
        def dich_gop(khoi):  # máy dịch nối hết thành một dòng
            return "VI " + khoi.replace("\n", " ")
        with patch.object(doc_voice, "_dich_mot", side_effect=dich_gop):
            dich = doc_voice.dich_tieng_viet("# Một\nA b.\nC d.")
        self.assertEqual(dich.splitlines(), ["VI Một", "VI A b.", "VI C d."])

    def test_tieu_de_dung_mot_minh_va_dong_danh_sach(self):
        doc = doc_voice.chuan_bi_van_ban_doc(
            "Phần 1:\nGỗ sồi bền.\n1. Mua gỗ tốt\n- Cắt gỗ đúng kích thước\nPhần 2: Ráp\nXong rồi.")
        self.assertEqual(doc.splitlines(), ["Phần 1.", "Gỗ sồi bền.", "1. Mua gỗ tốt.",
                                            "- Cắt gỗ đúng kích thước.", "Phần 2: Ráp.", "Xong rồi."])
        self.assertEqual(doc_voice.nhan_dien_tieu_de("Phần 1:")[0], "Phần 1")
        # Tiêu đề đứng một mình thành một câu riêng, không dính câu sau, mốc khớp SRT
        ket_qua = [gia_lap_edge(d) for d in doc_voice.chia_doan(doc)]
        cau_list = doc_voice.lay_danh_sach_cau(ket_qua)
        chu = [c[2] for c in cau_list]
        self.assertIn("Phần 1.", chu)
        self.assertIn("- Cắt gỗ đúng kích thước.", chu)
        self.assertNotIn("Phần 2", " ".join(c for c in chu if c.startswith("- Cắt")))
        chapters = doc_voice.trich_xuat_chapters(
            "Phần 1:\nGỗ sồi bền.\n- Cắt gỗ đúng kích thước\nPhần 2: Ráp\nXong rồi.", cau_list, 0)
        self.assertEqual([d.split(" ", 1)[1] for d in chapters.splitlines()], ["Phần 1", "Ráp"])
        moc = {c[2]: c[0] for c in cau_list}
        self.assertLessEqual(abs(giay_yt(chapters.splitlines()[1].split()[0]) - moc["Phần 2: Ráp."]), 10.01)
        # Dòng thường giữa văn bản không bị thêm dấu chấm
        self.assertEqual(doc_voice.chuan_bi_van_ban_doc("dòng cắt giữa\ncâu tiếp"), "dòng cắt giữa\ncâu tiếp")

    def test_pause_co_tran_toi_da(self):
        canh_bao = []
        khoi = doc_voice.tach_doan_va_pause("Một. [nghỉ 99999s] Hai. (pause 3s) Ba.", canh_bao)
        self.assertEqual(khoi, [("text", "Một."), ("pause", doc_voice.TOI_DA_PAUSE),
                                ("text", "Hai."), ("pause", 3.0), ("text", "Ba.")])
        self.assertEqual(len(canh_bao), 1)
        self.assertIn("99999", canh_bao[0])
        self.assertLess(len(doc_voice.tao_khoang_lang_mp3(doc_voice.TOI_DA_PAUSE)), 100000)
        canh_bao2 = []  # đúng bằng trần thì không cảnh báo
        doc_voice.tach_doan_va_pause("A. [nghỉ 10s] B.", canh_bao2)
        self.assertEqual(canh_bao2, [])

    def test_srt_khop_mp3_that_khong_troi(self):
        # 11 đoạn, mỗi đoạn mp3 dài hơn câu cuối 0,06 giây (im lặng cuối), thêm 1 khoảng nghỉ ở giữa
        ket_qua = []
        for i in range(11):
            ket_qua.append(gia_lap_edge(f"Sentence {i} one two three. Sentence {i} four five six."))
            if i == 5:
                ket_qua.append((doc_voice.tao_khoang_lang_mp3(1.0), [(0.0, 1.0, "")]))
        moc, cuoi = doc_srt(doc_voice.tao_srt(ket_qua))
        tong = sum(len(m) for m, _ in ket_qua) / doc_voice.BYTE_MOI_GIAY
        self.assertLess(abs(tong - cuoi), 0.1)
        truoc = sum(len(m) for m, _ in ket_qua[:11]) / doc_voice.BYTE_MOI_GIAY  # đầu đoạn thứ 10
        self.assertAlmostEqual(moc["Sentence 10 one two three."], truoc, delta=0.002)
        lay = doc_voice.lay_danh_sach_cau(ket_qua)
        self.assertAlmostEqual(lay[-1][1], cuoi, delta=0.002)

    def test_trich_xuat_seo(self):
        vb = (
            "# Trí Tuệ Nhân Tạo Năm 2026\n"
            "Trí tuệ nhân tạo đang thay đổi toàn bộ quy trình sản xuất nội dung video. "
            "Các công cụ AI tự động giúp tăng tốc độ làm việc gấp mười lần và tiết kiệm chi phí. "
            "Khám phá các bí quyết ứng dụng công nghệ hiện đại ngay bây giờ."
        )
        kq = doc_voice.trich_xuat_seo(vb)
        self.assertTrue(len(kq["tieu_de"]) >= 1)
        self.assertIn("Trí Tuệ Nhân Tạo Năm 2026", kq["tieu_de"][0])
        self.assertTrue(len(kq["mo_ta_hook"]) > 0)
        self.assertLessEqual(len(kq["mo_ta_hook"]), 250)
        vb_dai = " ".join(["Nội dung công nghệ giúp người xem hiểu cách làm việc hiệu quả hơn."] * 20)
        self.assertLessEqual(len(doc_voice.trich_xuat_seo(vb_dai)["mo_ta_hook"]), 250)
        self.assertTrue(len(kq["tags"]) > 0)
        self.assertTrue(len(kq["tags"]) <= 500)
        self.assertIn("#", kq["hashtags"])


# ---------- phụ đề tiếng Việt ghép theo nội dung ----------
VIET_TAT_EDGE = ("Mr.", "Mrs.", "Dr.", "e.g.", "i.e.", "U.S.")


def _tach_kieu_edge(chu_doan, tach_nho=False, tach_phay=60):
    """Bộ tách câu giả lập Edge, cố ý KHÁC bộ tách của app:
    - mặc định: giữ "1. Mua gỗ" và "Mr. Smith" một câu, không tách sau "...", gộp câu ngắn 1 đến 2 từ
      vào câu sau, câu dài hơn tach_phay ký tự tách tại dấu phẩy.
    - tach_nho=True: tách sau MỌI dấu chấm có khoảng trắng (cả "1." và "Mr." thành câu riêng)."""
    tu = chu_doan.split()
    cau, hien = [], []
    for i, t in enumerate(tu):
        hien.append(t)
        het = t[-1] in ".!?" and i < len(tu) - 1
        if het and not tach_nho:
            if re.fullmatch(r"\d+\.", t) or t in VIET_TAT_EDGE or t.endswith("..."):
                het = False
        if het:
            cau.append(" ".join(hien))
            hien = []
    if hien:
        cau.append(" ".join(hien))
    if tach_nho:
        return cau
    gop, cho = [], ""
    for c in cau:
        c = f"{cho} {c}".strip()
        cho = ""
        if len(c.split()) <= 2:
            cho = c
            continue
        gop.append(c)
    if cho:
        gop.append(cho)
    ra = []
    for c in gop:
        while len(c) > tach_phay and "," in c[25:]:
            k = c.index(",", 25) + 1
            ra.append(c[:k].strip())
            c = c[k:].strip()
        ra.append(c)
    return ra


def gia_lap_edge_khac(chu_doan, tach_nho=False, tu_moi_giay=3.2):
    cau, t = [], 0.0
    for c in _tach_kieu_edge(chu_doan, tach_nho):
        dai = len(c.split()) / tu_moi_giay
        cau.append((t, dai, c))
        t += dai
    return b"\0" * int(round(6000 * (t + 0.06))), cau


def dich_gia_tung_dong(khoi):
    """Máy dịch giả giữ đúng số dòng: mỗi dòng nguồn thành «dòng»."""
    return "\n".join(f"«{d}»" for d in khoi.split("\n"))


def chay_nhu_app(vb, tach_nho=False):
    """Đi đúng đường app: nút Dịch (dich_tieng_viet trên chữ đọc) rồi Chuyển (_chay, _lam) với Edge giả."""
    doc = doc_voice.chuan_bi_van_ban_doc(vb)
    ket_qua = []
    for loai, val in doc_voice.tach_doan_va_pause(doc):
        if loai == "pause":
            ket_qua.append((doc_voice.tao_khoang_lang_mp3(val), [(0.0, val, "")]))
        else:
            ket_qua += [gia_lap_edge_khac(d, tach_nho) for d in doc_voice.chia_doan(val)]
    cau_list = doc_voice.lay_danh_sach_cau(ket_qua)
    with patch.object(doc_voice, "_dich_mot", side_effect=dich_gia_tung_dong):
        dich = doc_voice.dich_tieng_viet(doc_voice.chuan_bi_van_ban_doc(vb))
    canh_bao = []
    srt = doc_voice.tao_srt_song_ngu(cau_list, dich, van_ban_nguon=doc, canh_bao=canh_bao)
    return cau_list, dich, srt, canh_bao


def doc_cue(srt):
    ra = []
    for khoi in srt.strip().split("\n\n"):
        d = khoi.split("\n")
        a, b = d[1].split(" --> ")
        ra.append((giay_srt(a), giay_srt(b), "\n".join(d[2:])))
    return ra


class KiemTraPhuDeTheoNoiDung(unittest.TestCase):
    def kiem_ca(self, vb, tach_nho=False):
        cau_list, dich, srt, canh_bao = chay_nhu_app(vb, tach_nho)
        dong = dich.split("\n")
        cue = doc_cue(srt)
        self.assertTrue(cue)
        self.assertEqual(canh_bao, [])
        self.assertNotIn("#", srt)
        self.assertIsNone(re.search(r"\[|\]|nghỉ|pause|tạm dừng", srt, re.IGNORECASE), srt)
        # Không cue nào cắt giữa câu dịch: mỗi cue là một dãy dòng dịch trọn vẹn, đúng thứ tự, không sót dòng.
        i = 0
        for _, _, chu in cue:
            gom = []
            while i < len(dong) and " ".join(gom) != chu:
                gom.append(dong[i])
                i += 1
            self.assertEqual(" ".join(gom), chu, (chu, gom))
        self.assertEqual(i, len(dong))
        # Mốc lấy từ câu Edge thật, lệch không quá 0,1 giây
        dau = [c[0] for c in cau_list]
        cuoi = [c[1] for c in cau_list]
        for bd, kt, chu in cue:
            self.assertLessEqual(min(abs(bd - x) for x in dau), 0.1, chu)
            self.assertLessEqual(min(abs(kt - x) for x in cuoi), 0.1, chu)
        # Đúng nội dung: chữ nguồn của cue dịch trùng chữ các câu Edge nằm trong khoảng thời gian của nó
        for bd, kt, chu in cue:
            nguon = doc_voice._chuan_ghep(re.sub(r"[«»]", " ", chu))
            edge = doc_voice._chuan_ghep(" ".join(c[2] for c in cau_list
                                                  if c[0] >= bd - 0.001 and c[1] <= kt + 0.001))
            self.assertEqual(nguon, edge, chu)
        return cau_list, dong, cue

    def test_kich_ban_5_meo_danh_sach_danh_so(self):
        vb = ("## 5 mẹo làm gỗ\n"
              "Hôm nay tôi chia sẻ năm mẹo nhỏ, giúp bạn làm việc trong xưởng nhanh hơn, an toàn hơn và đẹp hơn.\n"
              "1. Đo hai lần trước mỗi lần cắt.\n2. Giữ đục luôn sắc\n3. Chà nhám theo thớ gỗ, đừng chà ngang thớ.\n"
              "4. Để keo khô qua đêm.\n5. Sơn trong phòng không bụi.\n## Lời kết\nHãy thử các mẹo này nhé.")
        nguon = [doc_voice._chuan_ghep(d) for d in doc_voice.tach_cau_nguon(doc_voice.chuan_bi_van_ban_doc(vb))]
        for tach_nho in (False, True):
            cau_list, dong, cue = self.kiem_ca(vb, tach_nho)
            # Bộ giả lập Edge thật sự tách câu khác app
            self.assertNotEqual([doc_voice._chuan_ghep(c[2]) for c in cau_list], nguon)
            self.assertTrue(any("«1. Đo hai lần trước mỗi lần cắt.»" in c[2] for c in cue))

    def test_viet_tat_mr_dr_eg(self):
        vb = ("Mr. Smith built this bench in 2019.\nIt cost about $3.50 per board, e.g. pine or fir.\n"
              "Dr. Lee said it was great... really great!\nWe used U.S. made screws.")
        for tach_nho in (False, True):
            _, dong, _ = self.kiem_ca(vb, tach_nho)
        self.assertEqual(dong[0], "«Mr. Smith built this bench in 2019.»")

    def test_the_pause_khong_vao_may_dich_va_phu_de(self):
        vb = ("# Intro\nWelcome back to the channel.\n[tạm dừng 2s]\nToday we build a table.\n"
              "First we pick the wood. [nghỉ 1s] Then we cut it.\n(pause 1.5s)\n# End\nSee you next time.")
        nguon = doc_voice.tach_cau_nguon(doc_voice.chuan_bi_van_ban_doc(vb))
        self.assertEqual(nguon, ["Intro.", "Welcome back to the channel.", "Today we build a table.",
                                 "First we pick the wood.", "Then we cut it.", "End.", "See you next time."])
        khoi = doc_voice.tach_doan_va_pause(doc_voice.chuan_bi_van_ban_doc(vb))
        self.assertEqual([v for k, v in khoi if k == "pause"], [2.0, 1.0, 1.5])
        for tach_nho in (False, True):
            cau_list, _, cue = self.kiem_ca(vb, tach_nho)
            # Không cue dịch nào phủ khoảng nghỉ
            for bd, kt, chu in cue:
                for (_, b1, _), (a2, _, _) in zip(cau_list, cau_list[1:]):
                    if a2 - b1 > 0.5:
                        self.assertFalse(bd < a2 - 0.01 and kt > b1 + 0.01, chu)  # mốc SRT làm tròn mili giây
        # Bản dịch cũ còn chỉ dẫn khoảng nghỉ: phụ đề vẫn sạch
        self.assertEqual(doc_voice.bo_the_pause("[tạm dừng 2 giây] Hôm nay. [nghỉ 1s] Rồi cắt."),
                         "Hôm nay. Rồi cắt.")

    def test_dau_thang_chi_bo_dau_dong(self):
        self.assertEqual(doc_voice.bo_dau_thang("Số # 5 là số đẹp."), "Số # 5 là số đẹp.")
        self.assertEqual(doc_voice.bo_dau_thang("Mẹo #5 hay nhất."), "Mẹo #5 hay nhất.")
        self.assertEqual(doc_voice.bo_dau_thang("## Tiêu đề\n#Giới thiệu\nKết. # Hết"),
                         "Tiêu đề\nGiới thiệu\nKết. Hết")
        self.assertEqual(doc_voice.tach_cau_nguon("## Tiêu đề\nSố # 5 là số đẹp."), ["Tiêu đề", "Số # 5 là số đẹp."])

    def test_dich_28000_ky_tu_toi_da_40_luot(self):
        cau = [f"Part {i}, we sand the oak board slowly and check every edge with a square." for i in range(380)]
        vb = "\n".join(cau)
        self.assertGreaterEqual(len(vb), 28000)
        goi = []

        def gop_tat_ca(khoi):  # máy dịch tệ nhất: luôn nối mọi dòng thành một
            goi.append(khoi)
            return "VI " + khoi.replace("\n", " ")

        thong_ke, canh_bao = {}, []
        dich = doc_voice.dich_tieng_viet(vb, ham_dich=gop_tat_ca, canh_bao=canh_bao, thong_ke=thong_ke)
        self.assertLessEqual(len(goi), 40)
        self.assertEqual(thong_ke["so_luot"], len(goi))
        self.assertEqual(len(dich.split("\n")), len(cau))
        self.assertTrue(canh_bao)
        goi.clear()

        def gop_vai_dong(khoi):  # máy dịch gộp dòng "Part 17," và "Part 250," với dòng sau
            goi.append(khoi)
            ds = khoi.split("\n")
            ra, i = [], 0
            while i < len(ds):
                if ds[i].startswith(("Part 17,", "Part 250,")) and i + 1 < len(ds):
                    ra.append("VI " + ds[i] + " " + ds[i + 1])
                    i += 2
                else:
                    ra.append("VI " + ds[i])
                    i += 1
            return "\n".join(ra)

        canh_bao = []
        dich = doc_voice.dich_tieng_viet(vb, ham_dich=gop_vai_dong, canh_bao=canh_bao)
        self.assertLessEqual(len(goi), 40)
        self.assertEqual(dich.split("\n"), ["VI " + c for c in cau])
        self.assertEqual(canh_bao, [])

    def test_dich_gap_429_thi_cho_roi_thu_lai(self):
        import urllib.error
        dem, ngu = [0], []

        def loi_hai_lan(khoi):
            dem[0] += 1
            if dem[0] <= 2:
                raise urllib.error.HTTPError("u", 429, "Too Many Requests", None, None)
            return dich_gia_tung_dong(khoi)

        self.assertEqual(doc_voice.dich_tieng_viet("A b.\nC d.", ham_dich=loi_hai_lan, ngu=ngu.append),
                         "«A b.»\n«C d.»")
        self.assertEqual(ngu, [2, 4])

        def luon_429(khoi):
            raise urllib.error.HTTPError("u", 429, "Too Many Requests", None, None)

        with self.assertRaises(RuntimeError):
            doc_voice.dich_tieng_viet("A b.", ham_dich=luon_429, ngu=lambda s: None)

    def test_khong_ghep_duoc_thi_canh_bao(self):
        cau_list = [(0.0, 2.0, "Hello there."), (2.0, 4.0, "Good bye.")]
        canh_bao = []
        doc_voice.tao_srt_song_ngu(cau_list, "Xin chào.\nTạm biệt.\nHẹn gặp.", van_ban_nguon="Hoàn toàn khác.",
                                   canh_bao=canh_bao)
        self.assertTrue(canh_bao)
        # Bản dịch cũ (cai_dat.json): một đoạn liền còn #, không biết câu nguồn, ít câu hơn số câu đọc.
        # Phụ đề dự phòng vẫn chỉ gồm câu dịch trọn vẹn, đủ và đúng thứ tự, không #.
        cl = [(0.0, 1.0, "Introduction."), (1.0, 3.0, "Hello and welcome."), (3.0, 4.0, "Why?"),
              (4.0, 7.0, "Wood cracks fast."), (7.0, 8.0, "End."), (8.0, 9.0, "Thanks.")]
        cau_dich = ["Giới thiệu Xin chào và chào mừng.", "Tại sao?", "Gỗ nứt nhanh.", "Kết luận Cảm ơn."]
        canh_bao = []
        cue = doc_cue(doc_voice.tao_srt_song_ngu(
            cl, "# Giới thiệu Xin chào và chào mừng. ##Tại sao? Gỗ nứt nhanh. # Kết luận Cảm ơn.", canh_bao=canh_bao))
        self.assertTrue(canh_bao)
        self.assertEqual(" ".join(c for _, _, c in cue), " ".join(cau_dich))
        for _, _, chu in cue:
            self.assertTrue(all(x in cau_dich for x in re.split(doc_voice.MAU_TACH_CAU, chu)), chu)



class KiemTraGiongClone(unittest.TestCase):
    """giọng clone Arthur, chia câu, chuẩn hoá, ghép, srt, chạy tiếp khi dừng."""

    def test_chuan_hoa_so_va_ky_hieu(self):
        gc = doc_voice.gc
        s = gc.chuan_hoa_tieng_anh("In 1969, 3,000 people paid $12.5 million (about 45%) on the 21st day.")
        self.assertIn("nineteen sixty-nine", s)
        self.assertIn("three thousand", s)
        self.assertIn("twelve point five million dollars", s)
        self.assertIn("forty-five percent", s)
        self.assertIn("twenty-first", s)
        self.assertNotIn("(", s)
        self.assertEqual(gc.chuan_hoa_tieng_anh("It cost $300 & more \u2014 truly."),
                         "It cost three hundred dollars and more, truly.")
        self.assertEqual(gc.so_thanh_chu(1215), "one thousand two hundred fifteen")
        self.assertEqual(gc.nam_thanh_chu(2004), "two thousand four")
        self.assertEqual(gc.nam_thanh_chu(1815), "eighteen fifteen")

    def test_wer(self):
        gc = doc_voice.gc
        self.assertEqual(gc.wer(gc.chu_cho_wer("Sealed for fifteen million years."),
                                gc.chu_cho_wer("sealed for 15 million years")), 0.0)
        self.assertAlmostEqual(gc.wer(["a", "b", "c", "d"], ["a", "x", "c"]), 0.5)

    def test_chia_phan_clone_vua_khung(self):
        gc = doc_voice.gc
        vb = ("Short one. " + "This sentence is deliberately long, with several commas, and it keeps going "
              "well past the limit of the machine reader so that it must be split.\n\nNew paragraph here! End.")
        phan = gc.chia_phan_clone(vb, 80)
        for p in phan:
            self.assertLessEqual(len(gc.chuan_hoa_tieng_anh(p["chu"])), 80)
        self.assertEqual([p["ngat"] for p in phan][0], "cau")
        self.assertIn("phay", [p["ngat"] for p in phan])
        self.assertIn("doan", [p["ngat"] for p in phan])
        self.assertEqual(phan[-1]["ngat"], "het")
        # không mất chữ nào khi chia
        self.assertEqual(" ".join(p["chu"] for p in phan).split(), vb.split())

    def test_cat_lang_va_ghep_khoang_lang_deu(self):
        gc = doc_voice.gc
        import numpy as np
        sr = 48000
        t = np.arange(int(sr * 0.5)) / sr
        tieng = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
        lang = np.zeros(int(sr * 0.3), dtype=np.float32)
        x = np.concatenate([lang, tieng, lang])
        cat = gc.cat_lang(x, sr)
        self.assertLess(abs(len(cat) / sr - 0.58), 0.03)   # 0.5 s tiếng + 2 x 40 ms đệm
        am, moc = gc.ghep_phan([(cat, gc.LANG_CAU), (cat, gc.LANG_DOAN), (cat, 0.0)], sr)
        self.assertAlmostEqual(moc[1][0] - moc[0][1], gc.LANG_CAU, places=3)
        self.assertAlmostEqual(moc[2][0] - moc[1][1], gc.LANG_DOAN, places=3)
        self.assertTrue(0.25 <= gc.LANG_CAU <= 0.5 and 0.6 <= gc.LANG_DOAN <= 0.9)
        # mép ghép đã fade về 0: không click
        self.assertLess(abs(am[0]), 1e-3)
        self.assertLess(abs(am[int(moc[0][1] * sr) - 1]), 0.02)

    def test_srt_dong_toi_da_42_ky_tu_2_dong(self):
        gc = doc_voice.gc
        cau = ("Deep beneath the ice of Antarctica, scientists have found a lake that has been sealed for "
               "fifteen million years, untouched by sunlight.")
        srt = gc.tao_srt_clone([(1.0, 9.0, cau), (9.4, 11.0, "Short line.")])
        khoi = [k for k in srt.strip().split("\n\n") if k.strip()]
        self.assertGreaterEqual(len(khoi), 3)
        chu_lai = []
        for k in khoi:
            dong = k.split("\n")
            loi = dong[2:]
            self.assertLessEqual(len(loi), 2)
            for d in loi:
                self.assertLessEqual(len(d), 42)
            chu_lai += " ".join(loi).split()
        self.assertEqual(chu_lai, (cau + " Short line.").split())
        self.assertTrue(srt.startswith("1\n00:00:01,000 --> "))
        self.assertIn("--> 00:00:11,000", srt)
        # không để sót một từ lẻ thành cụm riêng (lỗi thấy ở bản thử exe đầu tiên: "it.", "floor.")
        for c in ("Its trunk is so wide that five adults, arms outstretched, can barely reach around it.",
                  "Every autumn, a mature oak can drop tens of thousands of acorns onto the forest floor."):
            for k in gc.chia_cum_srt(c):
                self.assertGreaterEqual(len(k.split()), 3)
        shorts = gc.tao_srt_clone([(0.0, 4.0, cau)], kieu_shorts=True,
                                  chia_shorts=lambda c: doc_voice.chia_cau_shorts(c, max_tu=7))
        self.assertGreater(shorts.count("-->"), 3)

    def test_doc_dai_chay_tiep_khi_dung(self):
        gc = doc_voice.gc
        import numpy as np
        import tempfile
        import threading

        class MayGia:
            def __init__(self):
                self.so_lan = 0

            def bat(self, **kw):
                return {"loaded": True}

            def sinh(self, chu, ref, ra, cfg=2.0, buoc=16, **kw):
                self.so_lan += 1
                sr = 48000
                n = int(sr * 0.32 * len(chu.split()))
                t = np.arange(n) / sr
                x = np.concatenate([np.zeros(2400), 0.2 * np.sin(2 * np.pi * 180 * t), np.zeros(2400)])
                gc.ghi_wav(ra, x, sr)
                return {"ok": True}

        with tempfile.TemporaryDirectory() as tam:
            ref = os.path.join(tam, "ref.wav")
            gc.ghi_wav(ref, np.zeros(4800), 48000)
            goc = gc.duong_mau
            gc.duong_mau = lambda slug: ref
            try:
                may = MayGia()
                dung = threading.Event()
                dem = {"n": 0}

                def bao(chu, pt):
                    if chu.startswith("Giọng clone: xong"):
                        dem["n"] += 1
                        if dem["n"] == 2:
                            dung.set()
                vb = "One two three. Four five six seven.\n\nEight nine ten eleven. Twelve thirteen."
                doc = gc.DocDaiClone(tam, may, None, ffmpeg="ffmpeg-gia", bao=bao, dung=dung)
                kq = doc.chay(vb, "arthur", "Kể chuyện", os.path.join(tam, "ra.mp3"))
                self.assertTrue(kq["dung"])
                self.assertEqual(kq["xong"], 2)
                self.assertEqual(may.so_lan, 2)
                # chạy lại: chỉ sinh 2 câu còn thiếu, rồi xuất (thay hậu kỳ ffmpeg bằng hàm giả)
                dung.clear()
                dem["n"] = 99
                goc_hk = gc.hau_ky
                gc.hau_ky = lambda ff, vao, mp3, wav="", **kw: open(mp3, "wb").close()
                try:
                    kq2 = doc.chay(vb, "arthur", "Kể chuyện", os.path.join(tam, "ra.mp3"),
                                   srt_ra=os.path.join(tam, "ra.srt"))
                finally:
                    gc.hau_ky = goc_hk
                self.assertFalse(kq2["dung"])
                self.assertEqual(may.so_lan, 4)
                self.assertEqual(kq2["tong"], 4)
                with open(os.path.join(tam, "ra.srt"), encoding="utf-8") as f:
                    srt = f.read()
                self.assertIn("Twelve thirteen.", srt)
                self.assertEqual(srt.count("-->"), 4)
            finally:
                gc.duong_mau = goc

    @unittest.skipUnless(shutil.which("ffmpeg"), "cần ffmpeg")
    def test_hau_ky_chuan_am_luong(self):
        gc = doc_voice.gc
        import numpy as np
        import tempfile
        with tempfile.TemporaryDirectory() as tam:
            sr = 48000
            t = np.arange(sr * 6) / sr
            x = 0.05 * np.sin(2 * np.pi * 300 * t) * (1 + np.sin(2 * np.pi * 2 * t))
            vao = os.path.join(tam, "vao.wav")
            gc.ghi_wav(vao, x, sr)
            mp3 = os.path.join(tam, "ra.mp3")
            gc.hau_ky(gc.tim_ffmpeg(), vao, mp3, os.path.join(tam, "ra.wav"))
            do = gc.do_am_luong(gc.tim_ffmpeg(), mp3)
            self.assertLess(abs(do["lufs"] + 14.0), 1.0)
            self.assertLessEqual(do["true_peak"], -1.0)
            self.assertTrue(os.path.isfile(os.path.join(tam, "ra.wav")))

    def test_cli_khong_chet_cp1252(self):
        # stdout là ống dẫn cp1252 (mặc định Windows khi không đặt PYTHONIOENCODING)
        import subprocess
        import sys
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
        env["PYTHONIOENCODING"] = "cp1252"
        r = subprocess.run([sys.executable, "giong_clone.py", "--help"], capture_output=True, env=env,
                           cwd=os.path.dirname(os.path.abspath(__file__)), timeout=120,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))
        self.assertIn("giọng clone", r.stdout.decode("utf-8"))

    def test_danh_sach_giong_co_arthur(self):
        gc = doc_voice.gc
        self.assertIn("arthur", gc.GIONG_CLONE)
        self.assertEqual(set(gc.MUC_CAM_XUC), {"Tự động theo kịch bản", "Trung tính", "Kể chuyện", "Hào hứng"})
        self.assertEqual(gc.MUC_MAC_DINH, "Tự động theo kịch bản")
        for muc in gc.MUC_CAM_XUC.values():
            # tiền tố ngắn đủ để mỗi phần còn ít nhất 60 ký tự chữ trong khung 120 ký tự của máy đọc nóng
            self.assertGreaterEqual(gc.KHUNG_MAY_DOC - len(muc["tien_to"]), 60)
        self.assertEqual(doc_voice.TEN_NGON_NGU[doc_voice.MA_CLONE], "★ Giọng clone (máy này)")



class KiemTraMayNheVaGoiGiong(unittest.TestCase):
    """gói giọng .dgvoice, máy đọc nhẹ, kịch bản YouTube một nút."""

    def _giong_tam(self, tam):
        import numpy as np
        gc = doc_voice.gc
        goc = gc.THU_MUC_GIONG
        gc.THU_MUC_GIONG = os.path.join(tam, "giong")
        d = os.path.join(gc.THU_MUC_GIONG, "thu")
        os.makedirs(d)
        t = np.arange(48000 * 5) / 48000
        gc.ghi_wav(os.path.join(d, "ref.wav"), 0.2 * np.sin(2 * np.pi * 150 * t), 48000)
        with open(os.path.join(d, "voice.json"), "w", encoding="utf-8") as f:
            json.dump({"ten": "Thu (clone)", "gioi": "Nam", "locale": "en-US", "quyen_dung": "thu nghiem"}, f)
        return goc

    def test_xuat_nhap_goi_giong(self):
        import tempfile
        import zipfile
        gc = doc_voice.gc
        with tempfile.TemporaryDirectory() as tam:
            goc = self._giong_tam(tam)
            try:
                self.assertIn("thu", gc.ds_giong())
                goi = gc.xuat_goi_giong("thu", os.path.join(tam, "Thu.dgvoice"))
                with zipfile.ZipFile(goi) as z:
                    self.assertEqual(set(z.namelist()), {"voice.json", "ref.wav"})
                    meta = json.loads(z.read("voice.json"))
                self.assertEqual(meta["quyen_dung"], "thu nghiem")
                self.assertIn("Kể chuyện", meta["muc_cam_xuc"])
                # máy kia: kho giọng trống, nhập gói
                gc.THU_MUC_GIONG = os.path.join(tam, "may_kia")
                self.assertNotIn("thu", gc.ds_giong())
                tt = gc.nhap_goi_giong(goi)
                self.assertEqual(tt["slug"], "thu")
                self.assertIn("thu", gc.ds_giong())
                self.assertTrue(gc.duong_mau("thu").startswith(gc.THU_MUC_GIONG))
            finally:
                gc.THU_MUC_GIONG = goc

    def test_nhap_goi_chan_file_la_va_duong_thoat(self):
        import tempfile
        import zipfile
        gc = doc_voice.gc
        with tempfile.TemporaryDirectory() as tam:
            goc = gc.THU_MUC_GIONG
            gc.THU_MUC_GIONG = os.path.join(tam, "kho")
            try:
                xau = os.path.join(tam, "xau.dgvoice")
                with zipfile.ZipFile(xau, "w") as z:
                    z.writestr("voice.json", "{}")
                    z.writestr("ref.wav", b"x")
                    z.writestr("../../thoat.txt", "x")
                with self.assertRaises(gc.LoiClone):
                    gc.nhap_goi_giong(xau)
                self.assertFalse(os.path.exists(os.path.join(tam, "thoat.txt")))
                hong = os.path.join(tam, "hong.dgvoice")
                with zipfile.ZipFile(hong, "w") as z:
                    z.writestr("voice.json", json.dumps({"slug": "../../x", "ten": "x"}))
                    z.writestr("ref.wav", b"khong phai wav")
                with self.assertRaises(gc.LoiClone):
                    gc.nhap_goi_giong(hong)
                self.assertEqual(gc.safe_slug("../../Arthur Clone!"), "arthur-clone")
            finally:
                gc.THU_MUC_GIONG = goc

    def test_tach_chuong_va_chapters(self):
        kb = ("[INTRO MUSIC]\n## Hook\nWhat if wood could talk? (pause) Stay with me.\n\n"
              "## Chapter 1: The Stump\n[B-roll: stump]\nIn 2019 a sawmill got a stump. It was ugly.\n\n"
              "## Outro\nSubscribe for more.")
        van_ban, chuong = doc_voice.tach_chuong(kb)
        self.assertNotIn("Hook", van_ban)
        self.assertNotIn("B-roll", van_ban)
        self.assertNotIn("pause", van_ban)
        self.assertEqual([c[0] for c in chuong], ["Hook", "Chapter 1: The Stump", "Outro"])
        muc_tg = [(0.0, 2.0, "What if wood could talk?"), (2.4, 3.5, "Stay with me."),
                  (4.3, 7.0, "In 2019 a sawmill got a stump."), (7.4, 8.4, "It was ugly."),
                  (65.2, 67.0, "Subscribe for more.")]
        ch = doc_voice.chapters_youtube(chuong, muc_tg).splitlines()
        self.assertEqual(ch, ["00:00 Hook", "00:04 Chapter 1: The Stump", "01:05 Outro"])

    def test_tai_file_tai_tiep_khi_dut(self):
        import hashlib
        import tempfile
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        mn = doc_voice.mn
        du_lieu = os.urandom(300000)
        dem = {"lan": 0}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                dem["lan"] += 1
                rg = self.headers.get("Range")
                bd = int(rg.split("=")[1].split("-")[0]) if rg else 0
                phan = du_lieu[bd:]
                self.send_response(206 if rg else 200)
                self.send_header("Content-Length", str(len(phan)))
                self.end_headers()
                if dem["lan"] == 1:              # lần đầu: đứt giữa chừng
                    self.wfile.write(phan[:100000])
                    self.wfile.flush()
                    self.connection.shutdown(2)
                    return
                self.wfile.write(phan)

        sv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=sv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tam:
                dich = os.path.join(tam, "m.bin")
                mn.tai_file(f"http://127.0.0.1:{sv.server_port}/m.bin", dich, co=len(du_lieu),
                            sha256=hashlib.sha256(du_lieu).hexdigest())
                with open(dich, "rb") as f:
                    self.assertEqual(f.read(), du_lieu)
                self.assertGreaterEqual(dem["lan"], 2)
        finally:
            sv.shutdown()

    def test_pho_giong_kich_thuoc(self):
        import numpy as np
        mn = doc_voice.mn
        x = np.sin(np.arange(22050) / 10).astype(np.float32)
        p = mn.pho(x)
        self.assertEqual(p.shape[:2], (1, 513))
        self.assertEqual(p.shape[2], 1 + (22050 + 768 - 1024) // 256)

    @unittest.skipUnless(os.environ.get("DOCVOICE_OV_CKPT"), "cần checkpoint OpenVoice V2 (DOCVOICE_OV_CKPT)")
    def test_doc_pth_khong_torch(self):
        mn = doc_voice.mn
        sd = mn.doc_pth(os.environ["DOCVOICE_OV_CKPT"])["model"]
        self.assertEqual(len(sd), 486)
        with open(os.path.join(mn.thu_muc_khung(), "ov_cong_thuc.json"), encoding="utf-8") as f:
            ct = json.load(f)
        for ten in ("ov_ref.onnx", "ov_doi.onnx"):
            for k, c in ct[ten].items():
                self.assertEqual(mn.trong_so(sd, k, c).dtype.name, "float32")

    def test_may_nhe_khong_doc_tien_to(self):
        gc = doc_voice.gc
        mn = doc_voice.mn
        self.assertFalse(mn.MayDocNhe.nhan_tien_to)
        self.assertTrue(gc.MayDocVoxCPM.nhan_tien_to)
        # máy nhẹ có nhịp, cao độ cho 3 mức cố định và cho từng sắc thái của mức Tự động theo kịch bản
        self.assertEqual(set(mn.NHAC_DIEU), (set(gc.MUC_CAM_XUC) - {gc.MUC_TU_DONG}) | set(gc.SAC_THAI))
        self.assertNotEqual(gc.ma_viec("a", "Kể chuyện", "x"), gc.ma_viec("a", "Kể chuyện", "x", "maydocnhe"))



class KiemTraNgheLuong(unittest.TestCase):
    """nghe ngay, sinh trước phía sau, bấm câu nghe từ câu đó."""

    def test_phan_dau_ngan_de_nghe_ngay(self):
        gc = doc_voice.gc
        vb = ("When the old carpenter opened the workshop that morning, he found the oak beam had split "
              "along its whole length, and nobody could explain why. Second sentence here.")
        phan = gc.chia_phan_clone(vb, 260, 80)
        self.assertLessEqual(len(gc.chuan_hoa_tieng_anh(phan[0]["chu"])), 80)
        self.assertEqual(" ".join(p["chu"] for p in phan).split(), vb.split())
        self.assertEqual(len(gc.chia_phan_clone(vb, 260, 0)), 2)
        mn = doc_voice.mn
        self.assertEqual(gc.chia_theo_may(vb, mn.MayDocNhe, "Kể chuyện"), phan)        # mức cố định: không có khoá sac
        tu_dong = gc.chia_theo_may(vb, mn.MayDocNhe)                                      # mức mặc định Tự động: thêm khoá sac
        self.assertEqual([p["chu"] for p in tu_dong], [p["chu"] for p in phan])
        self.assertEqual(tu_dong, gc.chia_phan_clone(vb, 260, 80, tu_dong=True))

    def test_sinh_tu_cau_bam_va_nhay_cau(self):
        gc = doc_voice.gc
        import numpy as np
        import tempfile

        thu_tu = []

        class MayGia:
            khung = 260
            nhan_tien_to = False      # máy giả đọc chữ trần, không có tiền tố cảm xúc

            def bat(self, **kw):
                return {"loaded": True}

            def sinh(self, chu, ref, ra, cfg=2.0, buoc=16, **kw):
                thu_tu.append(chu.split()[0])
                sr = 22050
                n = int(sr * 0.32 * len(chu.split()))
                x = np.concatenate([np.zeros(1000), 0.2 * np.sin(2 * np.pi * 180 * np.arange(n) / sr), np.zeros(1000)])
                gc.ghi_wav(ra, x, sr)

        with tempfile.TemporaryDirectory() as tam:
            ref = os.path.join(tam, "ref.wav")
            gc.ghi_wav(ref, np.zeros(4800), 22050)
            goc, goc_hk = gc.duong_mau, gc.hau_ky
            gc.duong_mau = lambda slug: ref
            gc.hau_ky = lambda ff, vao, mp3, wav="", **kw: open(mp3, "wb").close()
            try:
                vb = "Alpha one two. Bravo three four. Charlie five six. Delta seven eight. Echo nine ten."
                doc = gc.DocDaiClone(tam, MayGia(), None, ffmpeg="ffmpeg-gia")
                su_kien = []

                def phat(sk, **kw):
                    su_kien.append((sk, kw.get("i")))
                    if sk == "xong" and kw["i"] == 2:
                        doc.uu_tien = 0           # người dùng bấm đúp câu đầu trong lúc đang sinh
                kq = doc.chay(vb, "arthur", "Kể chuyện", os.path.join(tam, "ra.mp3"), bat_dau=2, phat=phat)
                self.assertFalse(kq["dung"])
                self.assertEqual(thu_tu, ["Charlie", "Alpha", "Bravo", "Delta", "Echo"])
                self.assertEqual(su_kien[0], ("phan", None))
                self.assertEqual(sorted(i for sk, i in su_kien if sk == "xong"), [0, 1, 2, 3, 4])
                # chạy lại cùng chữ: không sinh lại câu nào (Xuất file không phải đọc lại)
                thu_tu.clear()
                doc.chay(vb, "arthur", "Kể chuyện", os.path.join(tam, "ra2.mp3"))
                self.assertEqual(thu_tu, [])
                self.assertEqual(doc.san, {0, 1, 2, 3, 4})
            finally:
                gc.duong_mau, gc.hau_ky = goc, goc_hk

    def test_phat_luong_dem_vap_va_tam_dung(self):
        import time
        import numpy as np
        pl = doc_voice.pl
        sr = 22050
        song = (0.1 * np.sin(np.arange(int(sr * 0.5)) * 2 * np.pi * 440 / sr)).astype(np.float32)
        san = {0, 1}
        doi = []
        p = pl.PhatLuong(4, lambda i: (song, sr) if i in san else None, khi_doi_phan=doi.append, im_lang=True)
        p.phat_tu(0)
        time.sleep(1.4)                    # câu 2 chưa có: thiết bị cạn, tính một lần vấp
        san.update({2, 3})
        time.sleep(0.3)
        p.tam_dung()
        time.sleep(0.4)
        self.assertTrue(p.dang_tam_dung)
        p.tiep()
        han = time.time() + 5
        while p.dang_chay and time.time() < han:
            time.sleep(0.05)
        if p.loi:
            self.skipTest(p.loi)
        self.assertFalse(p.dang_chay)
        self.assertEqual(p.so_lan_vap, 1)
        self.assertEqual(doi, [0, 1, 2, 3])
        self.assertAlmostEqual(p.giay_da_phat, 2.0, delta=0.05)

    def test_phat_luong_dem_truoc_khi_may_ban(self):
        """máy đọc chậm hơn tiếng phát (RTF > 0,85) thì gom sẵn vài câu rồi mới phát, vấp thì đệm gấp đôi."""
        import threading
        import time
        import numpy as np
        pl = doc_voice.pl
        sr = 22050
        song = (0.1 * np.sin(np.arange(int(sr * 1.0)) * 2 * np.pi * 440 / sr)).astype(np.float32)   # mỗi câu 1 giây
        san = set()
        p = pl.PhatLuong(5, lambda i: (song, sr) if i in san else None, im_lang=True, do_rtf=lambda: 1.6)
        p.DEM_DAU = 3.0              # 3 giây: gom đủ 3 câu rồi mới phát
        dem = []
        p.khi_dem = dem.append
        p.phat_tu(0)
        san.add(0)
        time.sleep(0.6)
        self.assertIsNone(p.t_ghi_dau, "mới có 1 câu (1 giây) < đệm 3 giây: chưa được phát")
        self.assertEqual(dem, [True])
        san.update({1, 2})
        han = time.time() + 3
        while p.t_ghi_dau is None and time.time() < han:
            time.sleep(0.05)
        if p.loi:
            self.skipTest(p.loi)
        self.assertIsNotNone(p.t_ghi_dau, "đủ 3 giây thì phát")
        self.assertEqual(dem, [True, False])
        self.assertEqual(p.so_lan_dem, 1)
        san.update({3, 4})
        han = time.time() + 8
        while p.dang_chay and time.time() < han:
            time.sleep(0.05)
        self.assertFalse(p.dang_chay)
        self.assertAlmostEqual(p.giay_da_phat, 5.0, delta=0.05)
        # máy rảnh (RTF thấp): không đệm thêm, phát ngay câu đầu
        san2 = {0}
        q = pl.PhatLuong(3, lambda i: (song, sr) if i in san2 else None, im_lang=True, do_rtf=lambda: 0.5)
        q.phat_tu(0)
        han = time.time() + 2
        while q.t_ghi_dau is None and time.time() < han:
            time.sleep(0.02)
        self.assertIsNotNone(q.t_ghi_dau)
        self.assertEqual(q.so_lan_dem, 0)
        q.dung()

    def test_phat_luong_dem_theo_trung_binh_cac_cau(self):
        """quyết định đệm dựa trên tốc độ trung bình các câu đã đọc, không chỉ câu đầu."""
        import time
        import numpy as np
        pl = doc_voice.pl
        sr = 22050
        song = (0.1 * np.sin(np.arange(int(sr * 1.0)) * 2 * np.pi * 440 / sr)).astype(np.float32)   # mỗi câu 1 giây

        def chay(san, rtf, cho, dem_dau=10.0, so_phan=6):
            p = pl.PhatLuong(so_phan, lambda i: (song, sr) if i in san else None, im_lang=True, do_rtf=rtf)
            p.DEM_DAU = dem_dau
            p.phat_tu(0)
            time.sleep(cho)
            return p

        # 1) mới có 1 câu, RTF 1,0 (trên 0,85 nhưng dưới 1,1): câu đầu hay chậm do khởi động, không đệm, phát ngay
        p = chay({0}, lambda: 1.0, 0.8)
        if p.loi:
            self.skipTest(p.loi)
        self.assertIsNotNone(p.t_ghi_dau)
        self.assertEqual(p.so_lan_dem, 0)
        p.dung()
        # 2) câu đầu rất chậm (1,5 > 1,1): giữ lại; 3 câu đã đọc có trung bình 0,6 thì thôi đệm, phát dù mới có 3 < 10 giây
        san = {0}
        p = chay(san, lambda: 1.5 if len(san) < 3 else 0.6, 0.6)
        self.assertIsNone(p.t_ghi_dau, "câu đầu chậm: giữ lại chờ thêm câu")
        san.update({1, 2})
        han = time.time() + 3
        while p.t_ghi_dau is None and time.time() < han:
            time.sleep(0.05)
        self.assertIsNotNone(p.t_ghi_dau, "trung bình 3 câu là 0,6: máy rảnh, không đệm tiếp")
        self.assertEqual(p.so_lan_dem, 1)
        p.dung()
        # 3) đã đọc 3 câu mà trung bình 1,0 > 0,85: máy bận, đệm đủ 10 giây (chưa đủ thì chưa phát)
        p = chay({0, 1, 2}, lambda: 1.0, 0.8)
        self.assertIsNone(p.t_ghi_dau)
        self.assertEqual(p.so_lan_dem, 1)
        p.dung()

    def test_phat_luong_do_rtf_loi_khong_chet_luong(self):
        """do_rtf lỗi (tranh chấp luồng) thì coi như chưa biết, luồng phát vẫn chạy tới hết."""
        import time
        import numpy as np
        pl = doc_voice.pl
        sr = 22050
        song = (0.1 * np.sin(np.arange(int(sr * 0.5)) * 2 * np.pi * 440 / sr)).astype(np.float32)

        def hong():
            raise RuntimeError("dictionary changed size during iteration")

        p = pl.PhatLuong(3, lambda i: (song, sr), im_lang=True, do_rtf=hong)
        p.phat_tu(0)
        han = time.time() + 6
        while p.dang_chay and time.time() < han:
            time.sleep(0.05)
        if p.loi:
            self.skipTest(p.loi)
        self.assertFalse(p.dang_chay)
        self.assertAlmostEqual(p.giay_da_phat, 1.5, delta=0.05)
        self.assertEqual(p.so_lan_dem, 0)

    def test_phat_luong_vap_thi_dem_gap_doi(self):
        import time
        import numpy as np
        pl = doc_voice.pl
        sr = 22050
        song = (0.1 * np.sin(np.arange(int(sr * 0.5)) * 2 * np.pi * 440 / sr)).astype(np.float32)
        san = {0}
        p = pl.PhatLuong(8, lambda i: (song, sr) if i in san else None, im_lang=True)
        p.DEM_DAU = 1.0
        p.phat_tu(0)
        time.sleep(1.2)                  # câu 1 chưa có: vấp lần 1, đệm đòi 1 giây
        if p.loi:
            self.skipTest(p.loi)
        self.assertEqual(p.so_lan_vap, 1)
        self.assertEqual(p.muc_dem, 1.0)
        san.update({1})                  # chỉ 0,5 giây sẵn < 1 giây: vẫn giữ, chưa phát tiếp
        time.sleep(0.4)
        self.assertEqual(p.so_lan_dem, 1)
        san.update({2, 3, 4, 5, 6, 7})
        han = time.time() + 6
        while p.dang_chay and time.time() < han:
            time.sleep(0.05)
        self.assertFalse(p.dang_chay)
        self.assertAlmostEqual(p.giay_da_phat, 4.0, delta=0.05)

    def test_doi_noi_khuc_co_fade(self):
        """P3: câu dài chia khúc 20 giây, chỗ nối có fade 10 ms hai phía (không click)."""
        import numpy as np
        mn = doc_voice.mn
        goc = mn.pho

        class SsGia:
            trong_so = {}

            def run(self, _, d):
                return [d["spec"]]
        try:
            mn.pho = lambda a: np.asarray(a, dtype=np.float32)
            may = mn.MayDocNhe.__new__(mn.MayDocNhe)
            may.ss_doi, may.se_nguon = SsGia(), None
            am = np.ones(mn.SR * 45, dtype=np.float32)
            o = may.doi(am, None)
        finally:
            mn.pho = goc
        khuc = mn.SR * 20
        n = int(mn.SR * 0.010)
        self.assertEqual(len(o), len(am))
        self.assertEqual(o[khuc - 1], 0.0)          # cuối khúc 1 fade về 0
        self.assertEqual(o[khuc], 0.0)              # đầu khúc 2 fade từ 0
        self.assertAlmostEqual(float(o[khuc - n - 1]), 1.0, places=3)
        self.assertAlmostEqual(float(o[khuc + n]), 1.0, places=3)
        self.assertEqual(float(o[0]), 1.0)          # đầu bài, cuối bài không fade
        self.assertEqual(float(o[-1]), 1.0)

    def test_ffmpeg_ghim_ban_va_ma_kiem(self):
        mn = doc_voice.mn
        self.assertNotIn("/latest/", mn.FFMPEG_ZIP)
        self.assertIn("lgpl", mn.FFMPEG_ZIP)
        self.assertRegex(mn.FFMPEG_ZIP_SHA256, r"^[0-9a-f]{64}$")
        self.assertGreater(mn.FFMPEG_ZIP_CO, 50_000_000)


class KiemTraChonSanArthur(unittest.TestCase):
    """cai_dat.json cũ là en-AU William thì bản có Arthur vẫn mở lên Arthur."""

    def _mo_app(self, cai_dat):
        import tempfile
        tam = tempfile.mkdtemp()
        file_cd = os.path.join(tam, "cai_dat.json")
        file_giong = os.path.join(tam, "giong.json")
        doc_voice.ghi_json(file_cd, cai_dat)
        doc_voice.ghi_json(file_giong, [
            {"ShortName": "en-AU-WilliamMultilingualNeural", "Locale": "en-AU", "Gender": "Male"},
            {"ShortName": "en-US-AndrewNeural", "Locale": "en-US", "Gender": "Male"},
            {"ShortName": "vi-VN-NamMinhNeural", "Locale": "vi-VN", "Gender": "Male"}])
        cu = (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.gc.ds_giong, doc_voice.App._tai_giong)
        doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG = file_cd, file_giong
        doc_voice.gc.ds_giong = lambda: {"arthur": {"ten": "Arthur (clone)", "gioi": "Nam"}}
        doc_voice.App._tai_giong = lambda self: None

        def tra():
            (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.gc.ds_giong, doc_voice.App._tai_giong) = cu
            shutil.rmtree(tam, ignore_errors=True)
        self.addCleanup(tra)
        return mo_app_an(self)

    def test_lan_dau_mo_ban_co_arthur_thi_chon_arthur(self):
        app = self._mo_app({"ngon_ngu": "en-AU", "giong": "en-AU-WilliamMultilingualNeural"})
        self.assertEqual(app._locales[app.ngon_ngu.current()], doc_voice.MA_CLONE)
        self.assertEqual(app._tham_so()[0], "clone:arthur")
        self.assertIn("Arthur", app.nhan_giong.cget("text"))
        self.assertTrue(app.cd.get("da_gioi_thieu_clone"))

    def test_da_gioi_thieu_thi_ton_trong_giong_edge_nhung_arthur_dung_dau(self):
        app = self._mo_app({"ngon_ngu": "en-AU", "giong": "en-AU-WilliamMultilingualNeural",
                            "da_gioi_thieu_clone": True})
        self.assertEqual(app._tham_so()[0], "en-AU-WilliamMultilingualNeural")
        self.assertEqual(app._ds_giong[0], "clone:arthur")
        self.assertTrue(app.chon_giong["values"][0].startswith("★ Arthur"))
        self.assertIn("William", app.nhan_giong.cget("text"))
        app._chon_arthur()
        self.assertEqual(app._tham_so()[0], "clone:arthur")
        self.assertIn("Arthur", app.nhan_giong.cget("text"))

    def test_cai_dat_trong_ban_arthur_nhung_mo_len_la_arthur_dung_dau(self):
        app = self._mo_app({})
        self.assertEqual(app._locales[app.ngon_ngu.current()], doc_voice.MA_CLONE)
        self.assertEqual(app._tham_so()[0], "clone:arthur")
        self.assertEqual(app.may_doc.get(), doc_voice.mn.MayDocNhe.ten)
        for loc in ("en-US", "en-AU"):
            app.ngon_ngu.current(app._locales.index(loc))
            app._nap_giong()
            self.assertEqual(app._ds_giong[0], "clone:arthur")
            self.assertTrue(app.chon_giong["values"][0].startswith("★ Arthur"))

    def test_ngon_ngu_khong_phai_tieng_anh_khong_chen_arthur(self):
        app = self._mo_app({"ngon_ngu": "vi-VN", "giong": "vi-VN-NamMinhNeural", "da_gioi_thieu_clone": True})
        self.assertEqual(app._ds_giong, ["vi-VN-NamMinhNeural"])


class KiemTraArthurNhung(unittest.TestCase):
    """Bản đóng sẵn Arthur chỉ kèm dấu giọng (giong_nhung/arthur/se.npy, 1 KB), không kèm âm thanh mẫu."""

    def _goc_gia(self, tam, voi_wav=False):
        import numpy as np
        gc = doc_voice.gc
        d = os.path.join(tam, "giong_nhung", "arthur")
        os.makedirs(d)
        np.save(os.path.join(d, "se.npy"), np.ones((1, 256, 1), dtype=np.float32))
        if voi_wav:
            os.makedirs(os.path.join(tam, "giong_mau", "arthur"))
            gc.ghi_wav(os.path.join(tam, "giong_mau", "arthur", "ref.wav"), np.zeros(48000), 48000)
        cu = (gc.thu_muc_goc_app, gc.THU_MUC_GIONG)
        gc.thu_muc_goc_app = lambda: tam
        gc.THU_MUC_GIONG = os.path.join(tam, "kho_nguoi_dung")
        self.addCleanup(lambda: (setattr(gc, "thu_muc_goc_app", cu[0]), setattr(gc, "THU_MUC_GIONG", cu[1])))
        return d

    def test_chi_co_dau_giong_van_hien_arthur_va_khong_xuat_duoc_goi(self):
        import tempfile
        gc = doc_voice.gc
        with tempfile.TemporaryDirectory() as tam:
            self._goc_gia(tam)
            tt = gc.ds_giong()["arthur"]
            self.assertEqual(tt["nguon"], "đóng sẵn")
            ref = gc.duong_mau("arthur")
            self.assertTrue(ref.endswith("se.npy") and gc.la_dau_giong(ref))
            with self.assertRaises(gc.LoiClone):
                gc.xuat_goi_giong("arthur", os.path.join(tam, "A.dgvoice"))
            self.assertFalse(os.path.exists(os.path.join(tam, "A.dgvoice")))

    def test_mau_wav_cua_nguoi_dung_hoac_ban_co_mau_duoc_uu_tien(self):
        import tempfile
        gc = doc_voice.gc
        with tempfile.TemporaryDirectory() as tam:
            self._goc_gia(tam, voi_wav=True)
            self.assertTrue(gc.duong_mau("arthur").endswith("ref.wav"))
            nd = os.path.join(gc.THU_MUC_GIONG, "arthur")
            os.makedirs(nd)
            gc.ghi_wav(os.path.join(nd, "ref.wav"), __import__("numpy").zeros(48000), 48000)
            self.assertTrue(gc.duong_mau("arthur").startswith(gc.THU_MUC_GIONG))

    def test_may_nhe_doc_thang_dau_giong_va_chan_file_hong(self):
        import tempfile
        import numpy as np
        mn = doc_voice.mn
        with tempfile.TemporaryDirectory() as tam:
            ok = os.path.join(tam, "se.npy")
            np.save(ok, np.full((1, 256, 1), 0.5, dtype=np.float64))
            m = mn.MayDocNhe()
            g = m.se_dich(ok)
            self.assertEqual((g.shape, g.dtype.name), ((1, 256, 1), "float32"))
            xau = os.path.join(tam, "xau.npy")
            np.save(xau, np.zeros((1, 10, 1), dtype=np.float32))
            with self.assertRaises(RuntimeError):
                m.se_dich(xau)
            nan = os.path.join(tam, "nan.npy")
            np.save(nan, np.full((1, 256, 1), np.nan, dtype=np.float32))
            with self.assertRaises(RuntimeError):
                m.se_dich(nan)

    def test_chuan_cao_gpu_tu_choi_giong_chi_co_dau_giong(self):
        import tempfile
        gc = doc_voice.gc
        with tempfile.TemporaryDirectory() as tam:
            self._goc_gia(tam)
            may = gc.MayDocVoxCPM.__new__(gc.MayDocVoxCPM)
            doc = gc.DocDaiClone.__new__(gc.DocDaiClone)
            doc.may, doc.ffmpeg, doc.thu_muc_tam = may, "ffmpeg", tam
            with self.assertRaises(gc.LoiClone) as c:
                doc.chay("Hello world.", slug="arthur")
            self.assertIn("chế độ Nhẹ", str(c.exception))

    def test_se_dong_goi_that_nho_va_dung_dinh_dang(self):
        import numpy as np
        p = os.path.join(os.path.dirname(os.path.abspath(doc_voice.__file__)), "giong_nhung", "arthur", "se.npy")
        if not os.path.isfile(p):
            self.skipTest("chưa có giong_nhung/arthur/se.npy (chỉ có ở máy dựng bản đóng sẵn)")
        self.assertLess(os.path.getsize(p), 2048)
        g = np.load(p, allow_pickle=False)
        self.assertEqual((g.shape, g.dtype.name), ((1, 256, 1), "float32"))
        self.assertTrue(np.isfinite(g).all())

    @unittest.skipUnless(os.environ.get("DOCVOICE_OV_CKPT"), "cần checkpoint OpenVoice V2 (DOCVOICE_OV_CKPT)")
    def test_dau_giong_dong_goi_bang_dau_giong_tinh_tu_mau_goc(self):
        import numpy as np
        mn = doc_voice.mn
        goc = os.path.dirname(os.path.abspath(doc_voice.__file__))
        wav = os.path.join(goc, "giong_mau", "arthur", "ref.wav")
        npy = os.path.join(goc, "giong_nhung", "arthur", "se.npy")
        if not (os.path.isfile(wav) and os.path.isfile(npy)):
            self.skipTest("cần cả ref.wav gốc và se.npy")
        os.environ.setdefault("LOCALAPPDATA", os.path.dirname(os.path.dirname(os.environ["DOCVOICE_OV_CKPT"])))
        m = mn.MayDocNhe()
        m.bat()
        self.assertTrue(np.allclose(m.se_dich(wav), m.se_dich(npy), atol=1e-6))


class KiemTraInUtf8(unittest.TestCase):
    def test_console_cp1252_khong_chet_khi_in_chu_viet(self):
        import io
        gc = doc_voice.gc
        buf = io.BytesIO()
        st = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
        with self.assertRaises(UnicodeEncodeError):
            st.write("Giọng clone: xong 1/2 phần")
            st.flush()
        st = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
        gc.sua_console_utf8(st)
        print("Giọng clone: xong 1/2 phần", file=st)
        st.flush()
        self.assertIn("Giọng".encode("utf-8"), st.buffer.getvalue())


class KiemTraCamXucTuDong(unittest.TestCase):
    """giọng tự đọc đúng cảm xúc từng câu theo kịch bản, luật chạy trên máy."""

    def test_phan_loai_cau_theo_tu_khoa(self):
        gc = doc_voice.gc
        self.assertEqual(gc.phan_loai_cau("Do not forget to subscribe and hit the like button.", 5, 8), "thân thiện")
        self.assertEqual(gc.phan_loai_cau("Speechless.", 3, 8), "nhấn")
        self.assertEqual(gc.phan_loai_cau("But there's one terrifying possibility…", 3, 8), "căng thẳng")
        self.assertEqual(gc.phan_loai_cau("What if a single wrong cut destroys it?", 4, 8), "căng thẳng")
        self.assertEqual(gc.phan_loai_cau("Inside, they found something extraordinary.", 4, 8), "hào hứng")
        self.assertEqual(gc.phan_loai_cau("Have you ever wondered why?", 4, 8), "hồi hộp")
        self.assertEqual(gc.phan_loai_cau("The trunk was loaded onto a trailer.", 0, 8), "hồi hộp")   # hook mở đầu
        self.assertEqual(gc.phan_loai_cau("The trunk was loaded onto a trailer.", 4, 8), "kể chuyện")

    def test_the_ghi_de_khong_bi_doc_va_ba_cham_ngat_dai(self):
        gc = doc_voice.gc
        phan = gc.chia_phan_clone("[trầm] The blade is moving. Look at this! [hào hứng] It is a burl…  "
                                  "might leave you speechless.\n\nPlease subscribe.", tu_dong=True)
        self.assertEqual([p["chu"] for p in phan], ["The blade is moving.", "Look at this!", "It is a burl…",
                                                    "might leave you speechless.", "Please subscribe."])
        self.assertEqual([p["sac"] for p in phan], ["trầm", "trầm", "hào hứng", "hào hứng", "thân thiện"])
        self.assertEqual(phan[2]["ngat"], "lung")
        self.assertGreater(gc.lang_sau("lung"), gc.lang_sau("cau"))
        self.assertTrue(all("[" not in p["chu"] for p in phan))
        # mức một sắc thái: không có khoá sac, thẻ vẫn không bị đọc
        self.assertNotIn("sac", gc.chia_phan_clone("[trầm] Hello there.")[0])
        self.assertEqual(gc.chia_phan_clone("[trầm] Hello there.")[0]["chu"], "Hello there.")

    def test_loc_kich_ban_giu_the_sac_thai(self):
        vb, _ = doc_voice.loc_kich_ban_youtube("[B-roll: forest] [hồi hộp] What is inside?")
        self.assertEqual(vb, "[hồi hộp] What is inside?")

    def test_sinh_lai_nhe_hon_khi_lech_giong(self):
        gc = doc_voice.gc
        import numpy as np
        import tempfile
        goi = []

        class MayGia:
            nguong_giong = 0.8

            def bat(self, **kw):
                return {}

            def sinh(self, chu, ref, ra, cfg=2.0, buoc=16, muc=None, **kw):
                goi.append((chu, muc))
                sr = 22050
                gc.ghi_wav(ra, 0.2 * np.sin(2 * np.pi * 180 * np.arange(int(sr * 1.2)) / sr), sr)

            def do_giong(self, wav, ref):
                return 0.5 if goi[-1][1] == "hào hứng" else 0.9

        with tempfile.TemporaryDirectory() as tam:
            doc = gc.DocDaiClone(tam, MayGia(), None, ffmpeg="ffmpeg-gia")
            kq = doc._sinh_mot("This is incredible news", "ref.wav", os.path.join(tam, "a.wav"), gc.MUC_TU_DONG, "hào hứng")
            self.assertEqual([m for _, m in goi], ["hào hứng", "kể chuyện"])
            self.assertEqual(kq["sac"], "kể chuyện")
            self.assertEqual(kq["giong"], 0.9)


class KiemTraTuDichVaToCau(unittest.TestCase):
    def test_tach_cau_hien_thi_giu_vi_tri(self):
        txt = "## Hook\nWhat if this tree hides a secret? One log.\n\nAnd what they discover… might leave you speechless."
        cau = doc_voice.tach_cau_hien_thi(txt)
        self.assertEqual([txt[a:b] for a, b, _d in cau],
                         ["## Hook", "What if this tree hides a secret?", "One log.", "And what they discover…",
                          "might leave you speechless."])
        self.assertEqual(len({d for _a, _b, d in cau}), 3)

    def test_dich_cau_gom_luot_va_du_phong_tung_cau(self):
        goi = []

        def gia(q):
            goi.append(q)
            dong = q.split("\n")
            if len(dong) > 1 and "Lech" in q:
                return "chỉ một dòng"
            return "\n".join("VI:" + d for d in dong)
        kq = doc_voice.dich_cau(["One log.", "Two logs."], goi=gia)
        self.assertEqual(kq, {"One log.": "VI:One log.", "Two logs.": "VI:Two logs."})
        self.assertEqual(len(goi), 1)
        kq = doc_voice.dich_cau(["Lech one.", "Lech two."], goi=gia)
        self.assertEqual(kq["Lech two."], "VI:Lech two.")

    def test_ban_dich_cu_khong_khop_thi_bo_va_ghep_cap_cau(self):
        import tempfile
        tam = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tam, True)
        cu = (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.FILE_BO_DEM_DICH, doc_voice.App._tai_giong,
              doc_voice.dich_cau)
        doc_voice.FILE_CAI_DAT = os.path.join(tam, "cd.json")
        doc_voice.FILE_GIONG = os.path.join(tam, "g.json")
        doc_voice.FILE_BO_DEM_DICH = os.path.join(tam, "bd.json")
        doc_voice.ghi_json(doc_voice.FILE_GIONG, [{"ShortName": "en-US-AndrewNeural", "Locale": "en-US", "Gender": "Male"}])
        doc_voice.ghi_json(doc_voice.FILE_CAI_DAT, {"van_ban": "One log. Two logs.", "ban_dich": "fmXin chào các bạn! luô",
                                                    "ngon_ngu": "en-US", "da_gioi_thieu_clone": True})
        doc_voice.App._tai_giong = lambda self: None
        doc_voice.dich_cau = lambda ds, goi=None: {c: "VI " + c for c in ds}

        def tra():
            (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.FILE_BO_DEM_DICH, doc_voice.App._tai_giong,
             doc_voice.dich_cau) = cu
        self.addCleanup(tra)
        app = mo_app_an(self)
        self.assertNotIn("fmXin", app.o_dich.get("1.0", "end"))
        app._tu_dich(bat_ky=True)
        import time
        han = time.time() + 5
        while getattr(app, "_dang_dich", False) and time.time() < han:
            app.update()
            time.sleep(0.05)
        app.update()
        self.assertEqual(app.o_dich.get("1.0", "end-1c"), "VI One log. VI Two logs.")
        k = app._to_dich_theo(len("One log. "))
        self.assertEqual(k, 1)
        self.assertEqual(app.o_dich.get("dang_doc.first", "dang_doc.last"), "VI Two logs.")


class KiemTraMayNheVong4(unittest.TestCase):
    """luồng onnx theo số nhân, ước tính phút theo số nhân, bản team không lộ giọng Arthur."""

    def test_so_luong_onnx_theo_so_nhan(self):
        f = doc_voice.mn.so_luong_onnx
        self.assertEqual(f(1, {}), 1)
        self.assertEqual(f(2, {}), 2)
        self.assertEqual(f(4, {}), 4)
        self.assertEqual(f(6, {}), 4)
        self.assertEqual(f(8, {}), 4)
        self.assertEqual(f(12, {}), 4)
        self.assertEqual(f(0, {}), 1)

    def test_so_luong_onnx_bien_moi_truong_de(self):
        f = doc_voice.mn.so_luong_onnx
        self.assertEqual(f(2, {"DOCVOICE_THREADS": "6"}), 6)
        self.assertEqual(f(8, {"DOCVOICE_THREADS": "1"}), 1)
        self.assertEqual(f(8, {"DOCVOICE_THREADS": "abc"}), 4)     # giá trị hỏng thì bỏ qua
        self.assertEqual(f(8, {"DOCVOICE_THREADS": "0"}), 4)
        self.assertEqual(f(8, {"DOCVOICE_THREADS": ""}), 4)

    def test_so_luong_onnx_co_tran(self):
        """P3 chấm vòng 4: DOCVOICE_THREADS khổng lồ không được tạo ngần ấy luồng."""
        f = doc_voice.mn.so_luong_onnx
        tran = doc_voice.mn.TRAN_LUONG_ONNX
        self.assertEqual(tran, 32)
        self.assertEqual(f(4, {"DOCVOICE_THREADS": "32"}), 32)
        self.assertEqual(f(4, {"DOCVOICE_THREADS": "33"}), 32)
        self.assertEqual(f(4, {"DOCVOICE_THREADS": "100000"}), 32)
        self.assertEqual(f(4, {"DOCVOICE_THREADS": "9" * 5000}), 4)      # số quá dài: Python từ chối, bỏ qua
        self.assertEqual(f(4, {"DOCVOICE_THREADS": "-5"}), 4)

    def test_may_doc_nhe_dung_so_luong_mac_dinh(self):
        mn = doc_voice.mn
        cu = os.environ.pop("DOCVOICE_THREADS", None)
        try:
            self.assertEqual(mn.MayDocNhe().so_luong, mn.so_luong_onnx())
            self.assertEqual(mn.MayDocNhe(so_luong=3).so_luong, 3)
            os.environ["DOCVOICE_THREADS"] = "2"
            self.assertEqual(mn.MayDocNhe().so_luong, 2)
        finally:
            os.environ.pop("DOCVOICE_THREADS", None)
            if cu is not None:
                os.environ["DOCVOICE_THREADS"] = cu

    def test_uoc_tinh_phut_theo_so_nhan(self):
        ten = doc_voice.mn.MayDocNhe.ten
        f = doc_voice.phut_moi_1000_tu
        self.assertEqual(f(ten, 1), 20.0)
        self.assertEqual(f(ten, 2), 5.2)
        self.assertEqual(f(ten, 4), 4.1)
        self.assertEqual(f(ten, 8), 4.5)
        self.assertEqual(f(ten, 16), 4.5)
        self.assertTrue(4.1 < f(ten, 6) < 4.5)
        self.assertTrue(5.2 > f(ten, 3) > 4.1)
        self.assertEqual(f(doc_voice.gc.MayDocVoxCPM.ten, 4), 50.0)    # máy GPU không đổi theo nhân
        self.assertEqual(f("máy lạ", 4), 5)

    def test_canh_bao_may_yeu_chi_khi_1_nhan(self):
        ten = doc_voice.mn.MayDocNhe.ten
        f = doc_voice.canh_bao_may_yeu
        self.assertIn("chậm hơn nghe", f(ten, 1))
        self.assertIn("Máy yếu", f(ten, 1))
        self.assertEqual(f(ten, 2), "")
        self.assertEqual(f(doc_voice.gc.MayDocVoxCPM.ten, 1), "")

    @staticmethod
    def _tim_cam(byte):
        return [k for k in (b"ref.wav", b"ref.txt", b"giong_mau", b"Arthur.dgvoice", b".dgvoice") if k in byte]

    def test_dong_goi_co_kiem_chan_lo_giong(self):
        goc = os.path.dirname(os.path.abspath(doc_voice.__file__))
        with open(os.path.join(goc, "quet-ban-team.ps1"), encoding="utf-8-sig") as f:
            ps = f.read()
        for k in ("ref.wav", "Arthur.dgvoice", "giong_mau", "exit 1", "Remove-Item"):
            self.assertIn(k, ps)

    @staticmethod
    def _chay_quet(thu_muc):
        goc = os.path.dirname(os.path.abspath(doc_voice.__file__))
        kq = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             os.path.join(goc, "quet-ban-team.ps1"), "-ThuMuc", thu_muc],
            capture_output=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
        return kq.returncode, kq.stdout.decode("utf-8", "replace") + kq.stderr.decode("utf-8", "replace")

    def test_quet_ban_team_chay_that(self):
        """Chạy thật quet-ban-team.ps1 (đoạn quét của dong-goi.ps1) trên exe giả: sạch thì qua, lộ thì xoá exe."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            sach = os.path.join(d, "sach")
            os.makedirs(sach)
            with open(os.path.join(sach, "DocVoice.exe"), "wb") as f:
                f.write(b"MZ" + b"\x00" * 500 + b"may_nhe_onnx/ov_ref.onnx" + b"\x00" * 100)
            ma, ra = self._chay_quet(sach)
            self.assertEqual(ma, 0, ra)
            self.assertTrue(os.path.isfile(os.path.join(sach, "DocVoice.exe")))
            for i, nho in enumerate((b"giong_mau/arthur/ref.wav", b"x ref.txt x", b"Arthur.dgvoice", b"giong_mau")):
                bd = os.path.join(d, f"loi{i}")
                os.makedirs(bd)
                with open(os.path.join(bd, "DocVoice.exe"), "wb") as f:
                    f.write(b"MZ" + b"\x00" * 200 + nho + b"\x00" * 50)
                ma, ra = self._chay_quet(bd)
                self.assertEqual(ma, 1, (nho, ra))
                self.assertFalse(os.path.exists(os.path.join(bd, "DocVoice.exe")), nho)
            # file lạ cạnh exe cũng bị chặn
            for ten in ("ref.wav", "Arthur.dgvoice", "x.dgvoice"):
                bd = os.path.join(d, "la_" + ten)
                os.makedirs(bd)
                with open(os.path.join(bd, "DocVoice.exe"), "wb") as f:
                    f.write(b"MZ" + b"\x00" * 50)
                with open(os.path.join(bd, ten), "wb") as f:
                    f.write(b"x")
                ma, ra = self._chay_quet(bd)
                self.assertEqual(ma, 1, (ten, ra))
                self.assertFalse(os.path.exists(os.path.join(bd, "DocVoice.exe")), ten)

    def test_dong_goi_goi_quet_ban_team(self):
        goc = os.path.dirname(os.path.abspath(doc_voice.__file__))
        with open(os.path.join(goc, "dong-goi.ps1"), encoding="utf-8-sig") as f:
            ps = f.read()
        self.assertIn("quet-ban-team.ps1", ps)
        self.assertLess(ps.index("-KhongGiongMau"), ps.index("quet-ban-team.ps1"))

    def test_ban_team_khong_chua_giong_arthur(self):
        goc = os.path.dirname(os.path.abspath(doc_voice.__file__))
        team = os.path.join(goc, "dist", "team")
        if not os.path.isdir(team):
            self.skipTest("chưa dựng dist/team")
        for ten in os.listdir(team):
            low = ten.lower()
            self.assertNotIn(low, ("ref.wav", "ref.txt"), ten)
            self.assertFalse(low.endswith(".dgvoice"), ten)
        exe = os.path.join(team, "DocVoice.exe")
        if not os.path.isfile(exe):
            self.skipTest("chưa có dist/team/DocVoice.exe")
        with open(exe, "rb") as f:
            self.assertEqual(self._tim_cam(f.read()), [], "bản team lộ giọng Arthur")

    def test_cach_quet_bat_duoc_ban_co_giong(self):
        """Đối chứng: cùng cách quét phải thấy giọng trong bản không phải team, không thì kiểm trên là vô nghĩa."""
        goc = os.path.dirname(os.path.abspath(doc_voice.__file__))
        rieng = os.path.join(goc, "dist", "DocVoice.exe")
        if not os.path.isfile(rieng) or not os.path.isfile(os.path.join(goc, "giong_mau", "arthur", "ref.wav")):
            self.skipTest("chưa có bản có giọng để đối chứng")
        with open(rieng, "rb") as f:
            self.assertIn(b"ref.wav", self._tim_cam(f.read()))

class KiemTraTuCapNhat(unittest.TestCase):
    """mọi bản đã phát hành có tự lên bản mới được không. Giả lập hoàn toàn: urlopen bị thay, không chạm mạng,
    exe giả nằm trong thư mục tạm. Bộ cập nhật của v1.0.0 và v1.1.0 là bản trích nguyên văn từ git (docs/cap-nhat-cu/)."""

    KHO_MOI = "ducdg88/doc-voice"
    KHO_CU = "ducdg88/doc-giong"
    EXE_MOI = b"MZ" + b"exe-moi-v1.5.0." * 4000
    EXE_CU = b"MZ" + b"exe-cu." * 100

    @staticmethod
    def _nap(ten):
        import importlib.util
        goc = os.path.dirname(os.path.abspath(doc_voice.__file__))
        spec = importlib.util.spec_from_file_location(
            "cap_nhat_" + ten, os.path.join(goc, "docs", "cap-nhat-cu", ten + ".py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def _phat_hanh(self, tag, kho_url, ten_asset=("DocVoice.exe", "DocGiong.exe"), noi_dung=None, digest=True, size=True):
        import hashlib
        nd = self.EXE_MOI if noi_dung is None else noi_dung
        assets = []
        for t in ten_asset:
            a = {"name": t, "browser_download_url": f"https://github.com/{kho_url}/releases/download/{tag}/{t}"}
            if size:
                a["size"] = len(nd)
            if digest:
                a["digest"] = "sha256:" + hashlib.sha256(nd).hexdigest()
            assets.append(a)
        return {"tag_name": tag, "assets": assets}

    def _chay(self, mo_dun, release, tai_ve=None, ten_exe="DocVoice.exe", chay=None):
        """Chạy mo_dun.kiem_ban_moi (và chay(mo_dun, ket_qua, thu_muc, exe) nếu có) trong hộp cát:
        sys.frozen, sys.executable giả, urlopen giả."""
        import io
        import sys
        import tempfile
        from unittest import mock
        loi_goi = []

        class Tra(io.BytesIO):
            def __init__(self, b, dai=None):
                super().__init__(b)
                self.headers = {} if dai is None else {"Content-Length": str(dai)}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def urlopen(req, timeout=None):
            url = req.full_url if hasattr(req, "full_url") else req
            loi_goi.append(url)
            if url.startswith("https://api.github.com/repos/"):
                # GitHub 301 từ tên kho cũ sang kho mới, urllib tự theo; ở đây trả thẳng release
                return Tra(json.dumps(release).encode("utf-8"))
            if url.startswith("https://github.com/") and "/releases/download/" in url:
                nd = tai_ve if tai_ve is not None else self.EXE_MOI
                return Tra(nd, len(nd))
            raise AssertionError("gọi ra ngoài ngoài dự kiến: " + url)

        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, ten_exe)
            with open(exe, "wb") as f:
                f.write(self.EXE_CU)
            with open(os.path.join(d, "cai_dat.json"), "w", encoding="utf-8") as f:
                f.write('{"giu": "du lieu nguoi dung"}')
            with mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.object(sys, "executable", exe), \
                    mock.patch("urllib.request.urlopen", urlopen):
                ket = mo_dun.kiem_ban_moi()
                ra = chay(mo_dun, ket, d, exe) if chay else None
            return ket, ra, loi_goi

    @staticmethod
    def _thay(mo_dun, ket, d, exe):
        mo_dun.thay_exe(ket[1], mo_lai=False)
        with open(exe, "rb") as f:
            moi = f.read()
        cu_ten = [n for n in os.listdir(d) if n.endswith(".old.exe")]
        mo_dun.don_exe_cu()
        con_lai = [n for n in os.listdir(d) if n.endswith(".old.exe")]
        with open(os.path.join(d, "cai_dat.json"), encoding="utf-8") as f:
            du_lieu = f.read()
        return moi, cu_ten, con_lai, du_lieu, sorted(os.listdir(d))

    def test_so_sanh_phien_ban_dung_so(self):
        f = doc_voice.so_phien_ban
        self.assertGreater(f("v1.10.0"), f("1.9.0"))
        self.assertGreater(f("v1.5.0"), f("1.1.0"))
        self.assertGreater(f("v2.0.0"), f("1.99.99"))
        self.assertEqual(f("v1.5.0"), f("1.5.0"))
        self.assertGreater(f("v1.5.1"), f("1.5.0"))
        self.assertGreater(f("v1.5.0"), f("v1.4.9-rc1"))

    def test_v110_tu_len_v150_va_giu_du_lieu(self):
        m = self._nap("ban_1_1_0")
        rel = self._phat_hanh("v1.5.0", self.KHO_MOI)
        ket, ra, goi = self._chay(m, rel, chay=self._thay)
        self.assertEqual(ket[0], "1.5.0")
        self.assertEqual(ket[1]["name"], "DocVoice.exe")
        moi, cu_ten, con_lai, du_lieu, ds = ra
        self.assertEqual(moi, self.EXE_MOI)
        self.assertEqual(cu_ten, ["DocVoice.old.exe"])
        self.assertEqual(con_lai, [])                                   # bản mới dọn exe cũ
        self.assertIn("du lieu nguoi dung", du_lieu)
        self.assertEqual(ds, ["DocVoice.exe", "cai_dat.json"])
        self.assertTrue(all(u.startswith("https://") for u in goi))

    def test_v110_exe_ten_docgiong_van_len_duoc(self):
        """Người dùng v1.1.0 đổi tên exe thành DocGiong.exe vẫn lên được: bộ cập nhật thay đúng file đang chạy."""
        m = self._nap("ban_1_1_0")
        rel = self._phat_hanh("v1.5.0", self.KHO_MOI)
        ket, ra, goi = self._chay(m, rel, ten_exe="DocGiong.exe", chay=self._thay)
        self.assertEqual(ra[0], self.EXE_MOI)
        self.assertEqual(ra[4], ["DocGiong.exe", "cai_dat.json"])

    def test_v100_bi_chan_khi_link_tai_o_kho_moi(self):
        """GHI NHẬN LỖI BẢN CŨ: v1.0.0 hỏi kho ducdg88/doc-giong (GitHub chuyển sang doc-voice), nhận link tải dưới
        doc-voice nhưng chỉ chấp nhận link dưới doc-giong, nên luôn coi như không có bản mới. Không sửa được từ phía bản mới."""
        m = self._nap("ban_1_0_0")
        self.assertEqual(m.KHO_GITHUB, self.KHO_CU)
        rel = self._phat_hanh("v1.5.0", self.KHO_MOI)
        ket, _, _ = self._chay(m, rel, ten_exe="DocGiong.exe")
        self.assertIsNone(ket)

    def test_v100_len_duoc_neu_kho_doc_giong_co_ban_phat_hanh(self):
        """Phương án cho v1.0.0: kho ducdg88/doc-giong (kho mới cùng tên cũ) có release v1.5.0 kèm DocGiong.exe."""
        m = self._nap("ban_1_0_0")
        rel = self._phat_hanh("v1.5.0", self.KHO_CU, ten_asset=("DocGiong.exe",))
        ket, ra, _ = self._chay(m, rel, ten_exe="DocGiong.exe", chay=self._thay)
        self.assertEqual(ket[0], "1.5.0")
        self.assertEqual(ra[0], self.EXE_MOI)
        self.assertEqual(ra[1], ["DocGiong.old.exe"])
        self.assertEqual(ra[2], [])
        self.assertIn("du lieu nguoi dung", ra[3])

    def test_v100_khong_thay_asset_ten_cu_thi_khong_len(self):
        m = self._nap("ban_1_0_0")
        rel = self._phat_hanh("v1.5.0", self.KHO_CU, ten_asset=("DocVoice.exe",))
        ket, _, _ = self._chay(m, rel, ten_exe="DocGiong.exe")
        self.assertIsNone(ket)

    def test_v150_khong_tu_cap_nhat_len_chinh_no(self):
        for tag in ("v1.5.0", "v1.4.9", "v1.1.0"):
            ket, _, _ = self._chay(doc_voice, self._phat_hanh(tag, self.KHO_MOI))
            self.assertIsNone(ket, tag)

    def test_v150_thay_v1_10_0_moi_hon_v1_9_0(self):
        cu = doc_voice.PHIEN_BAN
        try:
            doc_voice.PHIEN_BAN = "1.9.0"
            ket, ra, _ = self._chay(doc_voice, self._phat_hanh("v1.10.0", self.KHO_MOI), chay=self._thay)
            self.assertEqual(ket[0], "1.10.0")
            self.assertEqual(ra[0], self.EXE_MOI)
            self.assertEqual(ra[2], [])
        finally:
            doc_voice.PHIEN_BAN = cu

    def test_v150_tu_choi_link_ngoai_kho_va_asset_thieu_ten(self):
        rel = self._phat_hanh("v9.0.0", "ke-xau/doc-voice")
        self.assertIsNone(self._chay(doc_voice, rel)[0])
        self.assertIsNone(self._chay(doc_voice, self._phat_hanh("v9.0.0", self.KHO_MOI, ten_asset=("Khac.exe",)))[0])

    def test_thay_exe_loi_thi_exe_cu_con_nguyen(self):
        for m in (self._nap("ban_1_1_0"), doc_voice):
            kich_ban = {
                "sha": (self._phat_hanh("v9.0.0", self.KHO_MOI), b"MZ" + b"gia-mao" * 100),       # file tải về không khớp digest
                "cut": (self._phat_hanh("v9.0.0", self.KHO_MOI), self.EXE_MOI[:-10]),              # mạng ngắt giữa chừng
                "khong_mz": (self._phat_hanh("v9.0.0", self.KHO_MOI, noi_dung=b"PK" + b"x" * 500), b"PK" + b"x" * 500),
                "thieu_thong_tin": (self._phat_hanh("v9.0.0", self.KHO_MOI, digest=False, size=False), self.EXE_MOI),
            }
            for ten, (rel, tai) in kich_ban.items():
                def chay(mo_dun, ket, d, exe, ten=ten):
                    with self.assertRaises(RuntimeError, msg=ten):
                        mo_dun.thay_exe(ket[1], mo_lai=False)
                    with open(exe, "rb") as f:
                        giu = f.read()
                    return giu, sorted(os.listdir(d))
                ket, ra, _ = self._chay(m, rel, tai_ve=tai, chay=chay)
                self.assertEqual(ra[0], self.EXE_CU, ten)                          # exe đang dùng vẫn nguyên
                self.assertEqual(ra[1], ["DocVoice.exe", "cai_dat.json"], ten)     # không để lại file .new


class KiemTraGiongNhapVaGiuLuaChon(unittest.TestCase):
    """giọng nhập vào hiện sẵn, được chọn sẵn, nhớ ngay, mở lại đúng giọng; bản team chưa có giọng
    thì có hướng dẫn rõ; cập nhật bản mới không đè lựa chọn người dùng."""

    @staticmethod
    def _tao_goi(thu_muc, slug="thu-test", ten="Giọng thử (không phải Arthur)"):
        import math
        import struct
        import wave
        import zipfile
        ref = os.path.join(thu_muc, "ref.wav")
        with wave.open(ref, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 20))) for i in range(24000 * 5)))
        goi = os.path.join(thu_muc, slug + ".dgvoice")
        with zipfile.ZipFile(goi, "w") as z:
            z.writestr("voice.json", json.dumps({"slug": slug, "ten": ten, "gioi": "Nam", "locale": "en-US",
                                                 "quyen_dung": "giong thu", "dinh_dang": 1}, ensure_ascii=False))
            z.write(ref, "ref.wav")
        return goi

    def _hop_cat(self):
        import tempfile
        tam = tempfile.mkdtemp()
        file_cd, file_giong = os.path.join(tam, "cd.json"), os.path.join(tam, "g.json")
        gc = doc_voice.gc
        cu = (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, gc.THU_MUC_GIONG, doc_voice.App._tai_giong,
              gc.thu_muc_goc_app)
        doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG = file_cd, file_giong
        gc.THU_MUC_GIONG = os.path.join(tam, "giong_clone")
        gc.thu_muc_goc_app = lambda: os.path.join(tam, "khong-co-giong-dong-goi")      # bản team: không kèm giọng nào
        doc_voice.App._tai_giong = lambda self: None
        doc_voice.ghi_json(file_giong, [
            {"ShortName": "en-US-AndrewNeural", "Locale": "en-US", "Gender": "Male"},
            {"ShortName": "vi-VN-NamMinhNeural", "Locale": "vi-VN", "Gender": "Male"}])

        def tra():
            (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, gc.THU_MUC_GIONG, doc_voice.App._tai_giong,
             gc.thu_muc_goc_app) = cu
            shutil.rmtree(tam, ignore_errors=True)
        self.addCleanup(tra)
        return tam, file_cd

    def _mo(self):
        return mo_app_an(self)

    def test_ban_team_chua_co_giong_thi_co_huong_dan_nhap_giong(self):
        tam, file_cd = self._hop_cat()
        doc_voice.ghi_json(file_cd, {"ngon_ngu": "en-US", "giong": "en-US-AndrewNeural"})
        app = self._mo()
        self.assertEqual(app._locales[0], doc_voice.MA_CLONE)               # mục Giọng clone luôn đứng đầu
        self.assertEqual(app._tham_so()[0], "en-US-AndrewNeural")           # không ép giọng nào khi chưa có giọng clone
        app.ngon_ngu.current(0)
        app._nap_giong()
        self.assertIn("chưa có", app.chon_giong.get())
        self.assertIn("Nhập giọng", app.trang_thai.get())
        self.assertIn("Nhập giọng", doc_voice.HUONG_DAN_NHAP_GIONG)
        self.assertNotIn(chr(0x2013), doc_voice.HUONG_DAN_NHAP_GIONG)
        self.assertNotIn(chr(0x2014), doc_voice.HUONG_DAN_NHAP_GIONG)

    def test_nhap_giong_hien_ngay_chon_san_luu_ngay_va_mo_lai_dung(self):
        tam, file_cd = self._hop_cat()
        doc_voice.ghi_json(file_cd, {"ngon_ngu": "vi-VN", "giong": "vi-VN-NamMinhNeural", "cam_xuc": "Trung tính",
                                     "da_gioi_thieu_tu_dong": True})
        goi = self._tao_goi(tam)
        app = self._mo()
        self.assertEqual(app._tham_so()[0], "vi-VN-NamMinhNeural")
        tt = app._nhap_giong(goi)
        self.assertEqual(tt["slug"], "thu-test")
        # 1. hiện ngay trong ô giọng, được chọn sẵn, đọc ngay không cần chọn lại
        self.assertEqual(app._locales[app.ngon_ngu.current()], doc_voice.MA_CLONE)
        self.assertEqual(app._tham_so()[0], "clone:thu-test")
        self.assertIn("Giọng thử", app.chon_giong.get())
        self.assertEqual(str(app.cam_xuc.cget("state")), "readonly")
        self.assertIn("Giọng thử", app.nhan_giong.cget("text"))
        # 2. lưu ngay, chưa cần thoát app (tắt đột ngột vẫn còn)
        dia = doc_voice.doc_json(file_cd, {})
        self.assertEqual(dia["giong"], "clone:thu-test")
        self.assertEqual(dia["ngon_ngu"], doc_voice.MA_CLONE)
        self.assertTrue(dia["da_gioi_thieu_clone"])
        self.assertEqual(dia["cam_xuc"], "Trung tính")                      # lựa chọn cũ khác không bị đụng
        # mở lại (không qua _thoat): đúng giọng vừa nhập
        app2 = self._mo()
        self.assertEqual(app2._tham_so()[0], "clone:thu-test")
        self.assertEqual(app2.cam_xuc.get(), "Trung tính")
        # người dùng chủ động đổi sang giọng Edge: lưu ngay, mở lại vẫn giữ, không bị ép về giọng clone
        app2.ngon_ngu.current(app2._locales.index("vi-VN"))
        app2.ngon_ngu.event_generate("<<ComboboxSelected>>")
        app2.update()
        self.assertEqual(doc_voice.doc_json(file_cd, {})["giong"], "vi-VN-NamMinhNeural")
        app3 = self._mo()
        self.assertEqual(app3._tham_so()[0], "vi-VN-NamMinhNeural")
        # giọng nhập vẫn có trong mục Giọng clone và trong danh sách tiếng Anh
        app3.ngon_ngu.current(app3._locales.index("en-US"))
        app3._nap_giong()
        self.assertIn("clone:thu-test", app3._ds_giong)
        app3.ngon_ngu.current(app3._locales.index(doc_voice.MA_CLONE))
        app3._nap_giong()
        self.assertIn("clone:thu-test", app3._ds_giong)

    def test_nhap_giong_truoc_khi_nap_danh_sach_van_luu(self):
        """Danh sách giọng chưa tải xong (app vừa mở, mất mạng): nhập giọng vẫn ghi vào cai_dat.json."""
        tam, file_cd = self._hop_cat()
        doc_voice.ghi_json(doc_voice.FILE_GIONG, [])
        doc_voice.ghi_json(file_cd, {"da_gioi_thieu_tu_dong": True, "thu_muc_ra": os.path.join(tam, "ra")})
        goi = self._tao_goi(tam)
        app = self._mo()
        self.assertTrue(app._nhap_giong(goi))
        dia = doc_voice.doc_json(file_cd, {})
        self.assertEqual(dia["giong"], "clone:thu-test")
        self.assertEqual(dia["thu_muc_ra"], os.path.join(tam, "ra"))

    def test_cap_nhat_khong_de_len_lua_chon_nguoi_dung(self):
        """Cấu hình cũ + giọng đã nhập; cập nhật giả lập thay exe; cai_dat.json, giong_clone, tu_dien còn nguyên từng byte;
        bản mới mở lên vẫn đúng giọng, mức cảm xúc, thư mục xuất."""
        import hashlib
        import io
        import sys
        import tempfile
        from unittest import mock
        tam, file_cd = self._hop_cat()
        ra = os.path.join(tam, "xuat-rieng")
        doc_voice.ghi_json(file_cd, {"ngon_ngu": "vi-VN", "giong": "vi-VN-NamMinhNeural", "cam_xuc": "Hào hứng",
                                     "thu_muc_ra": ra, "song_song": 5, "tu_kiem_cap_nhat": True, "kieu_shorts": True,
                                     "da_gioi_thieu_clone": True, "da_gioi_thieu_tu_dong": True})
        doc_voice.gc.nhap_goi_giong(self._tao_goi(tam))
        with open(os.path.join(tam, "tu_dien.txt"), "w", encoding="utf-8") as f:
            f.write("AI = ây ai\n")

        def dau_van_tay():
            kq = {}
            for goc, _d, fs in os.walk(tam):
                for f in fs:
                    if f.endswith((".json", ".txt", ".wav")) and f != "g.json":
                        p = os.path.join(goc, f)
                        with open(p, "rb") as h:
                            kq[os.path.relpath(p, tam)] = hashlib.sha256(h.read()).hexdigest()
            return kq
        truoc = dau_van_tay()
        self.assertIn(os.path.join("giong_clone", "thu-test", "ref.wav"), truoc)
        exe_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, exe_dir, True)
        exe = os.path.join(exe_dir, "DocVoice.exe")
        moi = b"MZ" + b"x" * 5000

        class Tra(io.BytesIO):
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with open(exe, "wb") as f:
            f.write(b"MZ" + b"cu" * 100)
        asset = {"name": "DocVoice.exe", "browser_download_url": doc_voice.LINK_TAI_HOP_LE + "v9.9.9/DocVoice.exe",
                 "size": len(moi), "digest": "sha256:" + hashlib.sha256(moi).hexdigest()}
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(sys, "executable", exe), \
                mock.patch("urllib.request.urlopen", lambda req, timeout=None: Tra(moi)):
            doc_voice.thay_exe(asset, mo_lai=False)
        with open(exe, "rb") as f:
            self.assertEqual(f.read(), moi)
        self.assertEqual(dau_van_tay(), truoc)                              # không một file dữ liệu nào bị đụng
        app = self._mo()
        self.assertEqual(app._tham_so()[0], "vi-VN-NamMinhNeural")
        self.assertEqual(app.cam_xuc.get(), "Hào hứng")
        self.assertEqual(app.thu_muc_ra, ra)
        self.assertEqual(app.cd.get("song_song"), 5)
        self.assertTrue(app.cd.get("kieu_shorts"))
        self.assertIn("thu-test", doc_voice.gc.ds_giong())
    def test_may_doc_rieng_tu_bat_lai_khi_chet(self):
        gc = doc_voice.gc
        import sys
        import tempfile
        ma = (
            "import sys, json, os, numpy as np, wave\n"
            "print(json.dumps({'san_sang': True, 'cuda': False}), flush=True)\n"
            "for d in sys.stdin:\n"
            "    y = json.loads(d)\n"
            "    if y['lenh'] == 'thoat': break\n"
            "    if 'chet' in y['text'] and not os.path.exists(y['out'] + '.lan1'):\n"
            "        open(y['out'] + '.lan1', 'w').close(); os._exit(3)\n"
            "    w = wave.open(y['out'], 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)\n"
            "    w.writeframes(np.zeros(1600, dtype=np.int16).tobytes()); w.close()\n"
            "    print(json.dumps({'ok': True, 'out': y['out'], 'che_do': y.get('che_do')}), flush=True)\n")
        with tempfile.TemporaryDirectory() as tam:
            gia = os.path.join(tam, "may_gia.py")
            with open(gia, "w", encoding="utf-8") as f:
                f.write(ma)
            may = gc.MayDocRieng(tam, os.path.join(tam, "log"), cho_nap=60)
            may.python = lambda: sys.executable
            may.goc_model = lambda: tam
            may.ma_may = lambda: gia
            try:
                r = may.sinh("hello", "ref.wav", os.path.join(tam, "a.wav"))
                self.assertTrue(r["ok"])
                self.assertEqual(r["che_do"], gc.CHE_DO_SINH)
                self.assertEqual(may.lan_bat, 1)
                bao = []
                r = may.sinh("chet giua chung", "ref.wav", os.path.join(tam, "b.wav"), bao=lambda c, *a: bao.append(c))
                self.assertTrue(r["ok"])
                self.assertEqual(may.lan_bat, 2)              # tự bật lại, gửi lại đúng câu đó
                self.assertTrue(any("bật lại" in c for c in bao))
                self.assertTrue(may.trang_thai()["loaded"])
            finally:
                may.dong()
            self.assertFalse(may.song)

    def test_chon_may_doc_mot_voxcpm_tren_gpu(self):
        gc = doc_voice.gc
        import tempfile

        class Chung:
            def __init__(self, st):
                self.st = st

            def trang_thai(self):
                return self.st
        with tempfile.TemporaryDirectory() as tam:
            gc._MAY_CHUNG.clear()
            rieng = gc.may_doc_chung(tam, os.path.join(tam, "log"))
            rieng.co_san = lambda: True
            dang_nap = Chung({"app": "dg-voice-daemon", "loaded": False})
            self.assertIs(gc.chon_may_doc(tam, chung=dang_nap), dang_nap)        # daemon đang nạp: đi qua nó
            khong_chay = Chung({"running": False})
            self.assertIs(gc.chon_may_doc(tam, chung=khong_chay), rieng)          # không ai chạy: máy riêng
            rieng.co_san = lambda: False
            self.assertIs(gc.chon_may_doc(tam, chung=khong_chay), khong_chay)     # thiếu venv: daemon
            gc._MAY_CHUNG.clear()


class KiemTraMoApp(unittest.TestCase):
    """mở app là Arthur, Arthur đứng đầu giọng tiếng Anh, thấy rõ giọng đang đọc."""
    EDGE = [{"ShortName": "en-AU-WilliamNeural", "Locale": "en-AU", "Gender": "Male"},
            {"ShortName": "en-US-AndrewMultilingualNeural", "Locale": "en-US", "Gender": "Male"},
            {"ShortName": "vi-VN-NamMinhNeural", "Locale": "vi-VN", "Gender": "Male"}]

    def test_arthur_dau_danh_sach_tieng_anh(self):
        for loc in ("en-US", "en-AU"):
            ds = doc_voice.danh_sach_giong(loc, self.EDGE)
            self.assertEqual(ds[0], ("clone:arthur", "★ Arthur (clone) · Nam"))
            self.assertIn(loc, ds[1][0])
        self.assertNotIn("clone:arthur", [m for m, _ in doc_voice.danh_sach_giong("vi-VN", self.EDGE)])
        self.assertEqual(doc_voice.danh_sach_giong("clone", self.EDGE), [("clone:arthur", "★ Arthur (clone) · Nam")])
        nho_william = {"ngon_ngu": "en-AU", "giong": "en-AU-WilliamNeural"}
        self.assertEqual(doc_voice.lua_chon_khi_mo(nho_william), ("clone", "clone:arthur"))
        self.assertEqual(doc_voice.lua_chon_khi_mo({**nho_william, doc_voice.KHOA_DA_CHUYEN_ARTHUR: True}),
                         ("en-AU", "en-AU-WilliamNeural"))

    def test_mo_app_nho_william_van_la_arthur(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tam:
            cai_dat, giong = os.path.join(tam, "cai_dat.json"), os.path.join(tam, "giong.json")
            doc_voice.ghi_json(cai_dat, {"ngon_ngu": "en-AU", "giong": "en-AU-WilliamNeural",
                                         "cam_xuc": "Kể chuyện", "thu_muc_ra": tam})
            doc_voice.ghi_json(giong, self.EDGE)
            goc = (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.App._tai_giong)
            doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG = cai_dat, giong
            doc_voice.App._tai_giong = lambda self: None      # không gọi mạng
            app = mo_app_an(self)
            try:
                self.assertEqual(app._locales[app.ngon_ngu.current()], "clone")
                self.assertEqual(app._tham_so()[0], "clone:arthur")
                self.assertTrue(app.nhan_giong.cget("text").startswith("Giọng đang chọn: Arthur (clone) · "))
                self.assertEqual(str(app.cam_xuc.cget("state")), "readonly")
                # đổi sang en-AU: Arthur đứng đầu và vẫn là giọng đang nhớ (đã chuyển Arthur một lần)
                app.ngon_ngu.current(app._locales.index("en-AU"))
                app._nguoi_dung_doi(app._nap_giong)
                self.assertEqual(app._ds_giong[0], "clone:arthur")
                self.assertEqual(app._tham_so()[0], "clone:arthur")
                # tự chọn William thì nhãn hiện William
                app.chon_giong.current(app._ds_giong.index("en-AU-WilliamNeural"))
                app._nguoi_dung_doi(app._cap_nhat_cam_xuc)
                self.assertIn("William", app.nhan_giong.cget("text"))
                # en-US chưa nhớ giọng nào: mặc định Arthur
                app.ngon_ngu.current(app._locales.index("en-US"))
                app._nguoi_dung_doi(app._nap_giong)
                self.assertEqual(app._tham_so()[0], "clone:arthur")
            finally:
                app.destroy()
                doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.App._tai_giong = goc

    def test_nho_giong_lan_cuoi_khi_mo_lai(self):
        """Yêu cầu: mở lại app, đọc lần sau phải đúng giọng và mức cảm xúc đã chọn lần cuối."""
        import tempfile
        with tempfile.TemporaryDirectory() as tam:
            cai_dat, giong = os.path.join(tam, "cai_dat.json"), os.path.join(tam, "giong.json")
            doc_voice.ghi_json(cai_dat, {"ngon_ngu": "en-AU", "giong": "en-AU-WilliamNeural", "thu_muc_ra": tam})
            doc_voice.ghi_json(giong, self.EDGE)
            goc = (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.App._tai_giong)
            doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG = cai_dat, giong
            doc_voice.App._tai_giong = lambda self: None

            def mo():
                return mo_app_an(self)
            try:
                # 1. cài đặt cũ nhớ William, lần đầu mở bản mới: thành Arthur, ghi khoá đánh dấu
                app = mo()
                self.assertEqual(app._tham_so()[0], "clone:arthur")
                self.assertTrue(doc_voice.doc_json(cai_dat, {}).get(doc_voice.KHOA_DA_CHUYEN_ARTHUR))
                # 2. chọn Arthur + Kể chuyện (đúng sự kiện người dùng), đóng, mở lại: vẫn Arthur Kể chuyện
                app.cam_xuc.set("Kể chuyện")
                app.cam_xuc.event_generate("<<ComboboxSelected>>")
                app.update()
                self.assertEqual(doc_voice.doc_json(cai_dat, {}).get("cam_xuc"), "Kể chuyện")   # lưu ngay khi chọn
                app._thoat()
                app = mo()
                self.assertEqual(app._tham_so()[0], "clone:arthur")
                self.assertEqual(app.cam_xuc.get(), "Kể chuyện")
                self.assertEqual(app.nhan_giong.cget("text"), "Giọng đang chọn: Arthur (clone) · Kể chuyện")
                # 3. chọn Edge Andrew (en-US), đóng, mở lại: vẫn Andrew, không bị ép về Arthur
                app.ngon_ngu.current(app._locales.index("en-US"))
                app.ngon_ngu.event_generate("<<ComboboxSelected>>")
                app.update()
                app.chon_giong.current(app._ds_giong.index("en-US-AndrewMultilingualNeural"))
                app.chon_giong.event_generate("<<ComboboxSelected>>")
                app.update()
                self.assertEqual(doc_voice.doc_json(cai_dat, {}).get("giong"), "en-US-AndrewMultilingualNeural")
                app._thoat()
                app = mo()
                self.assertEqual(app._tham_so()[0], "en-US-AndrewMultilingualNeural")
                self.assertIn("Andrew", app.nhan_giong.cget("text"))
                app._thoat()
            finally:
                try:
                    app.destroy()
                except Exception:
                    pass
                (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.App._tai_giong,
                 ) = goc


class KiemTraVong2(unittest.TestCase):
    """thẻ không đọc ra ở giọng Edge, tự dịch theo câu, highlight theo vị trí phát."""

    def test_bo_the_giu_doan_cho_giong_edge(self):
        gc = doc_voice.gc
        vb = "[hồi hộp] Have you ever wondered?  Nobody knows.\n\n[trầm]  The wind stopped. [Hào hứng] It worked!"
        sach = gc.bo_the(vb)
        self.assertNotIn("[", sach)
        self.assertNotIn("hồi hộp", sach)
        self.assertEqual(sach, "Have you ever wondered? Nobody knows.\n\nThe wind stopped. It worked!")
        self.assertEqual(len([d for d in doc_voice.chia_doan(sach) if d.strip()]),
                         len([d for d in doc_voice.chia_doan("Have you ever wondered? Nobody knows.\n\n"
                                                             "The wind stopped. It worked!") if d.strip()]))

class KiemTraAppVong2(unittest.TestCase):
    """Tự dịch theo câu và highlight trên app thật (cửa sổ ẩn, máy dịch giả, không gọi mạng)."""

    def setUp(self):
        import tempfile
        self.tam = tempfile.TemporaryDirectory()
        t = self.tam.name
        self.goc = (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.App._tai_giong)
        doc_voice.FILE_CAI_DAT = os.path.join(t, "cai_dat.json")
        doc_voice.FILE_GIONG = os.path.join(t, "giong.json")
        doc_voice.App._tai_giong = lambda self: None
        doc_voice.ghi_json(doc_voice.FILE_GIONG, KiemTraMoApp.EDGE)

    def tearDown(self):
        (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.App._tai_giong) = self.goc
        self.tam.cleanup()

    def _cho(self, app, dk, giay=5):
        import time
        t = time.time()
        while time.time() - t < giay:
            app.update()
            if dk():
                return True
            time.sleep(0.02)
        return False

    def test_app_hien_tien_do_va_nhay_moi_giay(self):
        import time as _t
        doc_voice.ghi_json(doc_voice.FILE_CAI_DAT, {"thu_muc_ra": self.tam.name})
        app = mo_app_an(self)
        try:
            with app._khoa_trang_thai:
                app.dang_chay = True
            app._t_bat_dau_luot = _t.time() - 30
            app._tien = {"chu": "Đang chuẩn bị", "pt": 0.0, "t_doi": _t.time() - 30, "lich_su": []}
            app._nhat_ky_tien_do = []
            app._bao("Đã chuyển 4/10 đoạn...", 40.0)
            tt = app.trang_thai.get()
            self.assertIn("40%", tt)
            self.assertIn("đoạn 4/10", tt)
            self.assertIn("đã chạy 0:30", tt)
            self.assertEqual(app.tien_do["value"], 40.0)
            app._tien["t_doi"] = _t.time() - 6            # một câu đọc lâu, tiến độ đứng 6 giây
            app._nhip_tien_do()
            self.assertIn("bước này đã chạy 6 giây", app.trang_thai.get())
            self.assertGreaterEqual(len(app._nhat_ky_tien_do), 2)
            app.dang_chay = False
        finally:
            app.destroy()

    def test_thu_muc_ra_rong_khong_lam_app_chet(self):
        """cai_dat.json có thu_muc_ra rỗng làm exe chết lúc mở."""
        doc_voice.ghi_json(doc_voice.FILE_CAI_DAT, {"thu_muc_ra": ""})
        app = mo_app_an(self)
        try:
            self.assertEqual(app.thu_muc_ra, doc_voice.THU_MUC_RA)
        finally:
            app.destroy()

class KiemTraTienDo(unittest.TestCase):
    """Yêu cầu: khi đang đọc phải hiện phần trăm, x/y, thời gian chạy, còn lại, tên bước."""

    def test_dinh_dang_gio(self):
        self.assertEqual(doc_voice.dinh_dang_gio(0), "0:00")
        self.assertEqual(doc_voice.dinh_dang_gio(65), "1:05")
        self.assertEqual(doc_voice.dinh_dang_gio(3725), "1:02:05")
        self.assertEqual(doc_voice.dinh_dang_gio(-3), "0:00")

    def test_uoc_con_lai_theo_toc_do_that(self):
        # 40 giây chạy được 25%: còn 75% nữa, tức 120 giây
        self.assertAlmostEqual(doc_voice.uoc_con_lai(40, 25), 120.0)
        # bỏ phần chờ nạp máy 10 giây trước lúc có tiến độ: 30 giây chạy thật được 25% thì còn 90 giây
        self.assertAlmostEqual(doc_voice.uoc_con_lai(40, 25, 10), 90.0)
        self.assertIsNone(doc_voice.uoc_con_lai(40, 1))        # chưa đủ dữ liệu
        self.assertIsNone(doc_voice.uoc_con_lai(2, 50))
        self.assertIsNone(doc_voice.uoc_con_lai(40, 100))

    def test_uoc_theo_nhip_gan(self):
        # bốn điểm gần nhất: 10% mỗi 5 giây, đang ở 40% lúc 20 giây, bây giờ 22 giây: còn 60% = 30 giây, trừ 2 giây đã trôi
        ls = [(5, 10.0), (10, 20.0), (15, 30.0), (20, 40.0)]
        self.assertAlmostEqual(doc_voice.uoc_theo_nhip_gan(ls, 22), 28.0)
        self.assertIsNone(doc_voice.uoc_theo_nhip_gan(ls[:2], 22))          # chưa đủ điểm
        self.assertIsNone(doc_voice.uoc_theo_nhip_gan([(1, 5.0), (2, 5.0), (3, 5.0)], 4))   # không tiến
        # đoạn đầu nhanh (kho câu), đoạn sau chậm: chỉ nhìn bốn điểm cuối
        ls2 = [(1, 30.0), (2, 60.0), (3, 70.0), (13, 71.0), (23, 72.0), (33, 73.0)]
        self.assertGreater(doc_voice.uoc_theo_nhip_gan(ls2, 33), 250)
        # con_giay truyền vào được dùng thay cho ước tính theo cả quãng
        self.assertIn("còn khoảng 0:45", doc_voice.dong_tien_do("Đã chuyển 4/10 đoạn...", 40.0, 60.0, 0.0, 0.0, 45.0))

    def test_uoc_ket_hop_khong_tut_ve_1_giay_khi_dung_lau(self):
        # ba phần xong gần như cùng lúc rồi đứng 50 giây: ước tính theo nhịp gần đây sẽ tụt về 1 giây, trung bình với
        # ước tính theo cả quãng thì vẫn còn lâu
        ls = [(2.0, 11.0), (2.1, 22.0), (2.4, 34.0)]
        gan = doc_voice.uoc_theo_nhip_gan(ls, 55.0)
        self.assertLessEqual(gan, 1.0)
        self.assertGreater(doc_voice.uoc_ket_hop(ls, 55.0, 34.0), 30)
        self.assertIsNone(doc_voice.uoc_ket_hop([], 1.0, 0.0))

    def test_edge_dung_o_79_phan_tram_khong_bao_sap_xong(self):
        """đứng ở 79% (đoạn 7/8) 45 giây mà vẫn hiện còn 0:02 đến 0:08 là sai."""
        ls = [(2.0, 11.0), (2.1, 22.0), (2.4, 34.0), (58.5, 45.0), (59.6, 56.0), (64.7, 68.0), (67.4, 79.0)]
        con = doc_voice.uoc_ket_hop(ls, 67.4, 79.0)
        self.assertGreater(con, 67.4 * 21 / 79 - 0.01)               # không dưới phần còn lại x tốc độ trung bình
        # đếm ngược quá ước tính thì chữ phải đổi thành "lâu hơn dự kiến", không hiện số nhỏ
        self.assertIn("lâu hơn dự kiến", doc_voice.dong_tien_do("Đã chuyển 7/8 đoạn...", 79.0, 100.0, 30.0, 0.0, 0.0))
        self.assertNotIn("còn khoảng", doc_voice.dong_tien_do("Đã chuyển 7/8 đoạn...", 79.0, 100.0, 30.0, 0.0, -3.0))

    def test_ten_buoc(self):
        tb = doc_voice.ten_buoc
        self.assertEqual(tb("Đã chuyển 3/10 đoạn..."), "Đọc (giọng Edge)")
        self.assertEqual(tb("Giọng clone: xong 3/12 phần, còn khoảng 4 phút (sinh 9 giây cho 1 giây tiếng)"), "Đọc (giọng clone)")
        self.assertEqual(tb("Đang ghép và chuẩn âm lượng..."), "Ghép và chuẩn âm lượng")
        self.assertEqual(tb("Đang xuất mp3 và srt..."), "Xuất mp3 và srt")
        self.assertEqual(tb("Xong: C:" + chr(92) + "a.mp3"), "Xong")
        self.assertEqual(tb("Lỗi: hết mạng"), "Lỗi")
        self.assertEqual(tb("Giọng clone: đọc lại câu 5 (whisper nghe lệch 30%)"), "Đọc lại câu lệch")
        self.assertEqual(tb("Đang bật bộ kiểm đọc (whisper)..."), "Bật bộ kiểm đọc")

    def test_dong_tien_do_day_du(self):
        d = doc_voice.dong_tien_do("Giọng clone: xong 12/30 phần, còn khoảng 3 phút", 36.0, 83.0, 1.0, 5.0)
        self.assertIn("Đọc (giọng clone)", d)
        self.assertIn("36%", d)
        self.assertIn("phần 12/30", d)
        self.assertIn("đã chạy 1:23", d)
        self.assertIn("còn khoảng", d)
        self.assertNotIn("bước này đã chạy", d)

    def test_khong_dung_im_qua_3_giay(self):
        d = doc_voice.dong_tien_do("Đã chuyển 2/10 đoạn...", 20.0, 50.0, 7.4, 0.0)
        self.assertIn("bước này đã chạy 7 giây", d)
        d0 = doc_voice.dong_tien_do("Đã chuyển 0/10 đoạn...", 0.0, 1.0, 1.0, 0.0)
        self.assertIn("đang ước tính", d0)

    def test_xong_va_loi_giu_nguyen_chu(self):
        self.assertEqual(doc_voice.dong_tien_do("Lỗi: x", 0, 5), "Lỗi: x")
        self.assertIn("mất 2:05", doc_voice.dong_tien_do("Xong: a.mp3", 100, 125))


class KiemTraKiemChatLuong(unittest.TestCase):
    """các bộ dò của kiem_chat_luong.py trên âm thanh tổng hợp có cài lỗi, và không báo nhầm ở bản sạch."""
    SR = 24000

    @classmethod
    def _loi_noi(cls, giay, hat):
        """Giả tiếng nói: chuỗi âm tiết 0,2 giây, mỗi âm tiết một cặp tần số riêng, có bao biên độ."""
        rng = np.random.RandomState(hat)
        t = np.arange(int(0.2 * cls.SR)) / cls.SR
        bao = np.minimum(1.0, 4.0 * np.sin(np.pi * t / 0.2))      # bao phẳng, mép mềm
        ra = []
        for _ in range(int(giay / 0.2)):
            f1, f2 = rng.uniform(200, 900), rng.uniform(1200, 3500)
            a = (np.sin(2 * np.pi * f1 * t) + 0.6 * np.sin(2 * np.pi * f2 * t) + 0.05 * rng.randn(len(t))) * bao * 0.3
            ra.append(a.astype(np.float32))
        return np.concatenate(ra)

    @classmethod
    def _ghep(cls, phan, lang=0.30):
        am, moc = gc.ghep_phan([(x, lang) for x in phan], cls.SR)
        return am.astype(np.float32), [(bd, kt, "câu %d" % i) for i, (bd, kt) in enumerate(moc)]

    def _sach(self):
        return self._ghep([self._loi_noi(3.0, 10 + i) for i in range(6)])

    def test_ban_sach_khong_bao_loi_nang(self):
        am, moc = self._sach()
        loi, _ = kcl.do_cho_im(am, self.SR, moc)
        loi += kcl.do_click_cho_ghep(am, self.SR, moc) + kcl.do_lap_doan(am, self.SR, moc)
        nang = [e for e in loi if kcl.MUC.get(e["loai"]) == "cao" or e["loai"] == "click_cho_ghep"]
        self.assertEqual(nang, [], nang)

    def test_mat_tieng_giua_phan(self):
        phan = [self._loi_noi(3.0, 10 + i) for i in range(6)]
        phan[2][int(1.0 * self.SR):int(2.2 * self.SR)] = 0.0       # cắt 1,2 giây thành câm tuyệt đối
        am, moc = self._ghep(phan)
        loi, _ = kcl.do_cho_im(am, self.SR, moc)
        bd = moc[2][0] + 1.0
        co = [e for e in loi if e["loai"] in ("mat_tieng", "im_bat_thuong") and abs(e["bd"] - bd) <= 0.5]
        self.assertTrue(co, loi)

    def test_im_bat_thuong_hai_giay(self):
        phan = [self._loi_noi(3.0, 10 + i) for i in range(6)]
        phan[3] = np.concatenate([phan[3][:int(1.5 * self.SR)], np.zeros(int(2.0 * self.SR), np.float32),
                                  phan[3][int(1.5 * self.SR):]])
        am, moc = self._ghep(phan)
        loi, _ = kcl.do_cho_im(am, self.SR, moc)
        bd = moc[3][0] + 1.5
        self.assertTrue([e for e in loi if e["loai"] == "im_bat_thuong" and abs(e["bd"] - bd) <= 0.5], loi)

    def test_lap_cau(self):
        phan = [self._loi_noi(3.0, 10 + i) for i in range(6)]
        phan.insert(4, phan[3].copy())                             # đọc lặp y hệt cả câu
        am, moc = self._ghep(phan)
        loi = kcl.do_lap_doan(am, self.SR, moc)
        self.assertTrue([e for e in loi if e["loai"] == "lap_cau" and abs(e["bd"] - moc[4][0]) <= 0.5], loi)

    def test_click_cho_ghep(self):
        phan = [self._loi_noi(3.0, 10 + i) for i in range(4)]
        phan[1] = phan[1] + 1.5                                    # lệch DC: nhảy biên độ ở chỗ nối cứng giữa hai đoạn to
        moc, cac, t = [], [], 0.0
        for x in phan:                                             # nối cứng, không fade
            moc.append((t, t + len(x) / self.SR, "câu"))
            t += len(x) / self.SR
            cac.append(x)
        am = np.concatenate(cac).astype(np.float32)
        loi = kcl.do_click_cho_ghep(am, self.SR, moc)
        self.assertTrue([e for e in loi if abs(e["bd"] - moc[1][0]) <= 0.5], loi)

    def test_mat_tieng_con_nhieu_nen(self):
        """mất tiếng 0,9 s mà nền còn nhiễu -55 dBFS (không câm tuyệt đối) vẫn phải là mất tiếng."""
        phan = [self._loi_noi(3.0, 10 + i) for i in range(6)]
        rng = np.random.RandomState(3)
        a = int(1.0 * self.SR)
        phan[2][a:a + int(0.9 * self.SR)] = (rng.randn(int(0.9 * self.SR)) * 10 ** (-55 / 20)).astype(np.float32)
        am, moc = self._ghep(phan)
        loi, _ = kcl.do_cho_im(am, self.SR, moc)
        bd = moc[2][0] + 1.0
        self.assertTrue([e for e in loi if e["loai"] == "mat_tieng" and abs(e["bd"] - bd) <= 0.5], loi)

    def test_lap_cau_khong_giong_bit_bat_bang_loi(self):
        """máy đọc đọc lại một câu, giọng không giống hệt bit: tầng tín hiệu không thấy, lời thì lặp."""
        goc = "the old oak stood at the edge of the village for many centuries"
        moc = [(0.0, 5.0, goc)]
        loi = kcl.loi_tu_nghe(moc, {"0": goc + " the old oak stood at the edge of the village for many centuries"})
        self.assertTrue([e for e in loi if e["loai"] == "lap_tu" and e["phan"] == 0], loi)
        self.assertEqual(kcl.loi_tu_nghe(moc, {"0": goc}), [])
        # chữ gốc tự lặp một cụm thì nghe lặp đúng như vậy không phải lỗi
        goc2 = "one two three four five and one two three four five again"
        self.assertEqual(kcl.loi_tu_nghe([(0.0, 5.0, goc2)], {"0": goc2}), [])

    def test_click_tren_mp3_that_qua_hau_ky(self):
        """mp3 sau hau_ky lệch mốc, xung 3 ms ở đầu phần phải bị bắt; bản sạch không báo nhầm."""
        import tempfile
        ff = kcl.tim_ffmpeg()
        if not ff:
            self.skipTest("máy không có ffmpeg")
        phan = [self._loi_noi(3.0, 30 + i) for i in range(6)]
        am, moc = self._ghep(phan)
        with tempfile.TemporaryDirectory() as t:
            def xuat(x, ten):
                wav, mp3 = os.path.join(t, ten + ".wav"), os.path.join(t, ten + ".mp3")
                gc.ghi_wav(wav, x, self.SR)
                gc.hau_ky(ff, wav, mp3)
                return kcl.doc_am(mp3, self.SR)
            sach = xuat(am, "sach")
            self.assertEqual(kcl.do_click_cho_ghep(sach, self.SR, moc), [])
            xl = am.copy()
            a = int(moc[2][0] * self.SR)
            xl[a:a + int(0.003 * self.SR)] += 0.35
            co = kcl.do_click_cho_ghep(xuat(xl, "click"), self.SR, moc)
            self.assertTrue([e for e in co if abs(e["bd"] - moc[2][0]) <= 0.1], co)

    def test_sai_giong_tung_cau_khong_dat_va_dua_vao_sinh_lai(self):
        """một câu sai giọng phải KHÔNG ĐẠT, đúng câu đó, và nằm trong danh sách sinh lại."""
        import tempfile
        am, moc = self._sach()
        goc = kcl.kiem_nang
        try:
            kcl.kiem_nang = lambda *a, **k: {"nghe": {}, "loi": [], "chua_kiem": [],
                                             "giong": {"ca_bai": 0.93, "tung_phan": [[0, 0.88, 0.93], [1, 0.75, 0.8], [2, 0.86, 0.9]]},
                                             "giong_cau": {"may": "wespeaker-resnet34",
                                                           "tung_cau": [[0, 0.8, None], [1, 0.3, None], [2, 0.75, None]]}}
            with tempfile.TemporaryDirectory() as t:
                wav = os.path.join(t, "a.wav")
                gc.ghi_wav(wav, am, self.SR)
                bc = kcl.kiem_day_du(wav, moc, ref="x", ten="sai-giong", nang=True)
        finally:
            kcl.kiem_nang = goc
        self.assertFalse(bc["dat"], bc["ly_do"])
        sai = [e for e in bc["loi"] if e["loai"] == "sai_giong"]
        self.assertEqual([e["phan"] for e in sai], [1])
        self.assertAlmostEqual(sai[0]["bd"], moc[1][0], places=1)
        self.assertIn(1, kcl.phan_can_sinh_lai(bc))
        # giọng đều tốt thì vẫn đạt phần giọng
        self.assertEqual(kcl.ket_luan({"loi": [], "giong_giong": {"ca_bai": 0.93}})["dat"], True)

    def _cham_voi_giong_cau(self, giong_cau, giong=None):
        import tempfile
        am, moc = self._sach()
        goc = kcl.kiem_nang
        try:
            kcl.kiem_nang = lambda *a, **k: {"nghe": {}, "loi": [], "chua_kiem": [] if giong_cau else ["Chưa đo sai giọng từng câu"],
                                             "giong": giong, "giong_cau": giong_cau}
            with tempfile.TemporaryDirectory() as t:
                wav = os.path.join(t, "a.wav")
                gc.ghi_wav(wav, am, self.SR)
                return kcl.kiem_day_du(wav, moc, ref="x", ten="giong-cau", nang=True), moc
        finally:
            kcl.kiem_nang = goc

    def test_giong_cau_ban_sach_thap_nhat_nhu_arthur_that_van_dat(self):
        """P1-A vòng 3: câu Arthur thật trầm nhất (resemblyzer 0,67 đến 0,71 với mẫu, câu 52 bài 3000 từ) không bị đánh trượt:
        quyết định theo độ giống với các câu khác của chính bài, không theo ngưỡng resemblyzer với mẫu."""
        bc, _ = self._cham_voi_giong_cau({"may": "wespeaker-resnet34", "tung_cau": [[0, 0.86, None], [1, 0.46, None], [2, 0.59, None],
                                                                                  [3, 0.9, None], [4, 0.47, None], [5, 0.8, None]]},
                                         giong={"ca_bai": 0.95, "tung_phan": [[0, 0.8, 0.9], [1, 0.67, 0.71], [2, 0.69, 0.7]]})
        self.assertEqual([e for e in bc["loi"] if e["loai"] == "sai_giong"], [])
        self.assertTrue(bc["dat"], bc["ly_do"])
        self.assertEqual(bc["giong_giong"]["thap_nhat_so_voi_bai"], 0.46)
        self.assertEqual(bc["giong_giong"]["thap_nhat_so_voi_mau"], 0.67)

    def test_giong_cau_mot_cau_giong_nam_khac_khong_dat_dung_cau(self):
        """P1-B vòng 3: một câu giọng nam khác (Edge Guy, Andrew, Ryan... đo 0,14 đến 0,42 so với bài) là sai giọng đúng câu đó."""
        bc, moc = self._cham_voi_giong_cau({"may": "wespeaker-resnet34", "tung_cau": [[0, 0.85, None], [1, 0.82, None], [2, 0.88, None],
                                                                                    [3, 0.42, None], [4, 0.84, None], [5, 0.8, None]]})
        sai = [e for e in bc["loi"] if e["loai"] == "sai_giong"]
        self.assertFalse(bc["dat"])
        self.assertEqual([e["phan"] for e in sai], [3])
        self.assertAlmostEqual(sai[0]["bd"], moc[3][0], places=1)
        self.assertIn("0.42", sai[0]["mo_ta"])

    def test_nguong_giong_cau_theo_do_dai(self):
        self.assertEqual(kcl.nguong_giong_cau(1.5), 0.30)
        self.assertEqual(kcl.nguong_giong_cau(2.0), 0.375)
        self.assertEqual(kcl.nguong_giong_cau(2.5), 0.45)
        self.assertEqual(kcl.nguong_giong_cau(6.0), 0.45)

    def test_giong_cau_khong_do_duoc_thi_ghi_chua_kiem(self):
        bc, _ = self._cham_voi_giong_cau(None, giong={"ca_bai": 0.95, "tung_phan": [[0, 0.5, 0.5]]})
        self.assertEqual([e for e in bc["loi"] if e["loai"] == "sai_giong"], [])     # không lùi về ngưỡng resemblyzer cũ
        self.assertTrue(any("từng câu" in c for c in bc["chua_kiem"]), bc["chua_kiem"])

    def test_giong_so_voi_bai_bo_chinh_no_ra(self):
        rng = np.random.RandomState(3)
        goc = rng.randn(64)
        E = [goc + 0.3 * rng.randn(64) for _ in range(5)] + [rng.randn(64)]
        E = [v / np.linalg.norm(v) for v in E]
        d = kcl.giong_so_voi_bai(E)
        self.assertTrue(all(x > 0.8 for x in d[:5]), d)
        self.assertLess(d[5], 0.4)
        # 3 trong 9 câu cùng một giọng lạ: trung vị các câu khác vẫn là giọng chính
        E2 = [goc + 0.3 * rng.randn(64) for _ in range(6)] + [rng.randn(64)] * 3
        E2 = [v / np.linalg.norm(v) for v in E2]
        d2 = kcl.giong_so_voi_bai(E2)
        self.assertTrue(all(x < 0.45 for x in d2[6:]), d2)
        self.assertTrue(all(x > 0.8 for x in d2[:6]), d2)
        # bài một câu: so với mẫu giọng
        self.assertAlmostEqual(kcl.giong_so_voi_bai([E[0]], them=E[1])[0], float(E[0] @ E[1]), places=5)
        self.assertEqual(kcl.giong_so_voi_bai([E[0]]), [None])

    def test_fbank_kaldi(self):
        self.assertIsNone(kcl.fbank_kaldi(np.zeros(1000)))
        f = kcl.fbank_kaldi(0.1 * np.sin(2 * np.pi * 220 * np.arange(16000) / 16000))
        self.assertEqual(f.shape, (98, 80))
        self.assertLess(abs(float(f.mean())), 1e-3)

    def test_mo_hinh_that_tach_giong_khac(self):
        """Mô hình WeSpeaker thật (chạy bằng Python tầng nặng): 4 đoạn mẫu Arthur và 1 đoạn giọng Andrew, đoạn Andrew dưới ngưỡng,
        các đoạn Arthur trên ngưỡng. Bỏ qua khi máy chưa có mô hình, Python tầng nặng hoặc file mẫu."""
        import subprocess, tempfile, json as _json
        py, co_w, _ = kcl.tim_python_nang()
        mh = kcl.duong_mo_hinh_giong_cau()
        goc = os.path.dirname(os.path.abspath(__file__))
        a = os.path.join(goc, "giong_mau", "arthur", "ref.wav")
        b = os.path.join(goc, "docs", "NV-arthur", "thu-kcl", "ref-en-US-AndrewNeural.wav")
        if not (py and co_w and os.path.isfile(mh) and os.path.isfile(a) and os.path.isfile(b)):
            self.skipTest("chưa có mô hình giọng, Python tầng nặng hoặc file mẫu")
        ma = kcl._MA_GIONG_CAU + r"""
import sys, json, onnxruntime as ort
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
ses = ort.InferenceSession(cfg["mh"], providers=["CPUExecutionProvider"])
def doc(p):
    r = subprocess.run([cfg["ff"], "-v", "error", "-nostdin", "-i", p, "-ac", "1", "-ar", "16000", "-f", "f32le", "-"], capture_output=True, creationflags=0x08000000)
    return np.frombuffer(r.stdout, dtype=np.float32)
import subprocess
A, B = doc(cfg["a"]), doc(cfg["b"])
doan = [A[int(s * 16000):int((s + 3.5) * 16000)] for s in (0.0, 4.0, 8.0, 12.0)] + [B[16000:int(4.5 * 16000)]]
E = []
for k in doan:
    e = ses.run(None, {"feats": fbank_kaldi(k)[None]})[0][0]
    E.append(e / np.linalg.norm(e))
print("KQ>" + json.dumps(giong_so_voi_bai(E)))
"""
        with tempfile.TemporaryDirectory() as t:
            f = os.path.join(t, "w.py")
            with open(f, "w", encoding="utf-8") as g:
                g.write(ma)
            c = os.path.join(t, "c.json")
            with open(c, "w", encoding="utf-8") as g:
                _json.dump({"mh": mh, "ff": kcl.tim_ffmpeg(), "a": a, "b": b}, g)
            r = subprocess.run([py, "-I", f, c], capture_output=True, text=True, timeout=300, creationflags=kcl.NO_WINDOW)
        dong = [x for x in r.stdout.splitlines() if x.startswith("KQ>")]
        self.assertTrue(dong, r.stderr[-500:])
        d = _json.loads(dong[-1][3:])
        self.assertTrue(all(x >= kcl.NGUONG_GIONG_CAU for x in d[:4]), d)
        self.assertLess(d[4], kcl.NGUONG_GIONG_CAU, d)

    def test_ket_luan_dat_va_khong_dat(self):
        sach = kcl.ket_luan({"loi": [], "am_luong": {"lufs": -14.2, "true_peak": -1.5}})
        self.assertTrue(sach["dat"])
        xau = kcl.ket_luan({"loi": [{"loai": "lap_cau", "bd": 1, "kt": 2}], "am_luong": {"lufs": -14.0, "true_peak": -1.5}})
        self.assertFalse(xau["dat"])
        self.assertTrue(xau["ly_do"])
        thap = kcl.ket_luan({"loi": [], "giong_giong": {"ca_bai": 0.8}})
        self.assertFalse(thap["dat"])

    def test_sinh_lai_toi_da_va_danh_dau_do(self):
        so_lan = []

        def kiem_mot():
            return {"loi": [{"loai": "mat_tieng", "bd": 1, "kt": 2, "phan": 3}], "dat": False}
        bc, nhat_ky = kcl.kiem_va_sua(lambda ds: so_lan.append(ds), kiem_mot, toi_da=2)
        self.assertEqual(len(so_lan), 2)
        self.assertEqual(so_lan[0], [3])
        self.assertTrue(bc["loi"][0].get("do"))

    def test_bao_cao_html_va_luu_canh_mp3(self):
        import tempfile
        bc = kcl.ket_luan({"kich_ban": "k", "loi": [{"loai": "lap_cau", "bd": 1.0, "kt": 3.0, "mo_ta": "x"}],
                           "thong_so": {"giay": 5.0}, "am_luong": {"lufs": -14.1, "true_peak": -1.5},
                           "chua_kiem": ["Chưa kiểm giọng"], "sinh_lai": [{"vong": 1, "phan": [0], "so_loi_truoc": 1, "so_loi_sau": 1}]})
        h = kcl.html_bao_cao(bc)
        self.assertIn("KHÔNG ĐẠT", h)
        self.assertIn("Chưa kiểm giọng", h)
        with tempfile.TemporaryDirectory() as t:
            j, hh = kcl.luu_bao_cao(bc, os.path.join(t, "bai.mp3"))
            self.assertTrue(os.path.isfile(j) and os.path.isfile(hh))

    def test_bang_tong_nhieu_kich_ban(self):
        cac = [{"kich_ban": "a", "dat": True, "loi": []},
               {"kich_ban": "b", "dat": False, "loi": [{"loai": "lap_cau", "bd": 0, "kt": 1}]}]
        dong = kcl.bang_tong(cac)
        self.assertEqual([d["dat"] for d in dong], [True, False])
        self.assertIn("KHÔNG ĐẠT", kcl.bang_tong_chu(dong))
        self.assertIn("<table>", kcl.html_bang_tong(dong))


class KiemTraKiemKichBan(unittest.TestCase):
    """kiểm kịch bản trước khi đọc, chỉ báo chỗ dễ lỗi và gợi ý cách sửa, không báo oan bộ kịch bản sạch."""

    def test_bat_cac_loi_hay_gap(self):
        import kich_ban_kiem as k
        t = ("Call 5551234567 now! Visit www.example.com or mail me@x.com on 03/04/2024 at 8:30 pm. "
             "Xin chào các bạn nhé tôi là Đức. The the end!!! The ABC and X2 go.")
        loai = {e["loai"] for e in k.kiem(t)}
        for can in ("day_so_dai", "duong_dan", "ngay_dang_so", "gio", "khong_phai_tieng_anh", "tu_lap", "dau_lap",
                    "viet_tat_la", "ma_chu_so"):
            self.assertIn(can, loai, can)
        for e in k.kiem(t):
            self.assertIn(e["muc"], ("sua", "luu_y"))
            self.assertTrue(e["goi_y"])
            self.assertEqual(t[e["bd"]:e["kt"]].replace("\n", " ")[:20], e["trich"][:20])

    def test_cau_dai_va_vi_tri_dong(self):
        import kich_ban_kiem as k
        dai = " ".join(["word"] * 55) + "."
        ds = k.kiem("Short one.\n\n" + dai)
        cau = [e for e in ds if e["loai"] == "cau_dai"]
        self.assertEqual(len(cau), 1)
        self.assertEqual(cau[0]["dong"], 3)
        self.assertEqual(k.tom_tat(ds)["sua"], 1)

    def test_kich_ban_sach_khong_bi_bao_oan(self):
        import glob
        import kich_ban_kiem as k
        thu_muc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "ban-giao", "kich-ban")
        for f in sorted(glob.glob(os.path.join(thu_muc, "*.txt"))):
            t = open(f, encoding="utf-8").read()
            sua = [e for e in k.kiem(t) if e["muc"] == "sua"]
            if os.path.basename(f).startswith("06-"):
                continue                  # kịch bản câu dài: cố ý bị báo
            self.assertEqual(sua, [], os.path.basename(f))
        self.assertEqual(k.kiem(""), [])
        self.assertEqual(k.kiem("   \n  "), [])

    def test_ten_viet_tat_va_so_bt_khong_bao(self):
        import kich_ban_kiem as k
        ds = k.kiem("Dr. Smith of the U.S. paid 12.5 percent in 2023, about 3 km from the 1st gate. NASA and the FBI agreed.")
        self.assertEqual([e for e in ds if e["muc"] == "sua"], [])
        self.assertEqual([e for e in ds if e["loai"] == "viet_tat_la"], [])

    def test_tu_sua_an_toan_khong_doi_nghia(self):
        import kich_ban_kiem as k
        moi, doi = k.tu_sua_an_toan("The the end!!!  Wait \u2014 what? \U0001f600 Fine .")
        self.assertTrue(doi)
        self.assertEqual(moi, "The end! Wait, what? Fine.")
        moi2, doi2 = k.tu_sua_an_toan("Nothing to fix here.")
        self.assertFalse(doi2)
        self.assertEqual(moi2, "Nothing to fix here.")


class KiemTraChuanHoaMoi(unittest.TestCase):
    """chuẩn hoá số, giờ, ngày, điện thoại, đơn vị, viết tắt: những chỗ trước đây đọc sai làm bản đọc KHÔNG ĐẠT."""

    def ch(self, t):
        return doc_voice.gc.chuan_hoa_tieng_anh(t)

    def test_tien_viet_tat_trieu_ty(self):
        self.assertEqual(self.ch("Pay \x245M now"), "Pay five million dollars now")
        self.assertEqual(self.ch("It cost \x242.3 billion"), "It cost two point three billion dollars")
        self.assertEqual(self.ch("About \x24300K a year"), "About three hundred thousand dollars a year")

    def test_gio_phut(self):
        self.assertEqual(self.ch("at 9:30 p.m. sharp"), "at nine thirty P M sharp")
        self.assertEqual(self.ch("at 8:30"), "at eight thirty")
        self.assertEqual(self.ch("at 12:05 am"), "at twelve oh five A M")
        self.assertEqual(self.ch("up at 8 a.m."), "up at eight A M")

    def test_ngay_thang_viet_tat_khong_tach_cau(self):
        gc = doc_voice.gc
        self.assertIn("December twelve", self.ch("on Dec. 12 in town"))
        phan = gc.chia_phan_clone("The demo was ready by Dec. 12 and it ran. Then Gen. Smith agreed. Done.")
        self.assertEqual([p["chu"] for p in phan],
                         ["The demo was ready by Dec. 12 and it ran.", "Then Gen. Smith agreed.", "Done."])

    def test_dien_thoai_doc_tung_so(self):
        self.assertEqual(self.ch("Call 555-1234 now"), "Call five five five, one two three, four now")
        self.assertEqual(self.ch("ID 5551234567"), "ID five five five, one two three, four five six, seven")

    def test_don_vi_va_ky_hieu(self):
        self.assertEqual(self.ch("a 5.5 kW system"), "a five point five kilowatts system")
        self.assertEqual(self.ch("8,000 kWh"), "eight thousand kilowatt hours")
        self.assertEqual(self.ch("60 ml and 20 g"), "sixty milliliters and twenty grams")
        self.assertEqual(self.ch("2.5x faster"), "two point five times faster")
        self.assertEqual(self.ch("open 24/7, 50/50, 3/4 cup"), "open twenty-four seven, fifty fifty, three quarters cup")
        self.assertEqual(self.ch("4K, 5G, gate B12"), "four K, five G, gate B twelve")
        self.assertEqual(self.ch("3-4 weeks, 2019-2020"), "three to four weeks, twenty nineteen to twenty twenty")

    def test_khong_doi_so_thuong(self):
        self.assertEqual(self.ch("In 1969 it cost 1,284,560 units, 78%"),
                         "In nineteen sixty-nine it cost one million two hundred eighty-four thousand five hundred sixty units, "
                         "seventy-eight percent")
        self.assertEqual(self.ch("the 21st century and 3rd place"), "the twenty-first century and third place")


class KiemTraDocLaiBienThe(unittest.TestCase):
    """đọc lại phần lỗi phải ra bản khác (đổi nhẹ nhịp, cao độ), không lặp đúng âm thanh cũ."""

    def test_bien_the_nhac_dieu(self):
        mn = doc_voice.mn
        nd = mn.NHAC_DIEU["Kể chuyện"]
        self.assertEqual(mn.nhac_dieu_bien_the(nd, 0), nd)
        cac = {tuple(mn.nhac_dieu_bien_the(nd, v).items()) for v in range(len(mn.BIEN_THE))}
        self.assertEqual(len(cac), len(mn.BIEN_THE))            # mỗi biến thể một cặp nhịp, cao độ khác nhau
        for v in range(len(mn.BIEN_THE)):
            r = mn.nhac_dieu_bien_the(nd, v)
            self.assertRegex(r["rate"], r"^[+-]\d+%$")
            self.assertRegex(r["pitch"], r"^[+-]\d+Hz$")
            self.assertLessEqual(abs(int(r["rate"][:-1]) - int(nd["rate"][:-1])), 12)

    def test_phan_da_doc_lan_truoc_thi_doc_lai_bang_bien_the_khac(self):
        gc = doc_voice.gc
        import numpy as np
        import tempfile
        thay = []

        class MayGia:
            khung = 260
            nhan_tien_to = False
            ho_tro_bien_the = True
            nguong_wer = 0.1

            def bat(self, **kw):
                return {"loaded": True}

            def sinh(self, chu, ref, ra, cfg=2.0, buoc=16, muc=None, bien_the=0):
                thay.append((chu.split()[0], bien_the))
                sr = 22050
                n = int(sr * 0.32 * len(chu.split()))
                x = np.concatenate([np.zeros(1000), 0.2 * np.sin(2 * np.pi * 180 * np.arange(n) / sr), np.zeros(1000)])
                gc.ghi_wav(ra, x, sr)

        with tempfile.TemporaryDirectory() as tam:
            ref = os.path.join(tam, "ref.wav")
            gc.ghi_wav(ref, np.zeros(4800), 22050)
            goc, goc_hk = gc.duong_mau, gc.hau_ky
            gc.duong_mau = lambda slug: ref
            gc.hau_ky = lambda ff, vao, mp3, wav="", **kw: open(mp3, "wb").close()
            try:
                vb = "Alpha one two. Bravo three four. Charlie five six."
                doc = gc.DocDaiClone(tam, MayGia(), None, ffmpeg="ffmpeg-gia")
                kq = doc.chay(vb, "arthur", "Kể chuyện", os.path.join(tam, "ra.mp3"))
                self.assertEqual([t for t in thay], [("Alpha", 0), ("Bravo", 0), ("Charlie", 0)])
                # xoá phần 1 như kiem_chat_luong.xoa_phan_de_sinh_lai rồi chạy lại: chỉ phần 1 được đọc, bằng biến thể khác
                file_so = os.path.join(kq["thu_muc"], "so_viec.json")
                so = json.load(open(file_so, encoding="utf-8"))
                so["phan"].pop("00001")
                os.remove(os.path.join(kq["thu_muc"], "phan_00001.wav"))
                json.dump(so, open(file_so, "w", encoding="utf-8"))
                thay.clear()
                doc.chay(vb, "arthur", "Kể chuyện", os.path.join(tam, "ra.mp3"))
                self.assertEqual(len(thay), 1)
                self.assertEqual(thay[0][0], "Bravo")
                self.assertNotEqual(thay[0][1], 0)
            finally:
                gc.duong_mau, gc.hau_ky = goc, goc_hk


class KiemTraBanDayDu(unittest.TestCase):
    """Bản đầy đủ (exe kèm giong_mau/arthur/ref.wav, dựng cho máy chủ dự án) không tự cập nhật sang bản GitHub,
    vì bản GitHub chỉ nhúng dấu giọng và sẽ làm mất mẫu cho Chuẩn cao. Bản chỉ có dấu giọng vẫn tự cập nhật."""

    def _goc(self, voi_wav):
        import tempfile
        tam = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tam, True)
        d = os.path.join(tam, "giong_nhung", "arthur")
        os.makedirs(d)
        open(os.path.join(d, "se.npy"), "wb").close()
        if voi_wav:
            d = os.path.join(tam, "giong_mau", "arthur")
            os.makedirs(d)
            open(os.path.join(d, "ref.wav"), "wb").close()
        return tam

    def _kiem(self, voi_wav):
        import io
        import sys
        from unittest import mock
        goi = []

        def urlopen(req, timeout=None):
            goi.append(req)
            return io.BytesIO(json.dumps({"tag_name": "v9.9.9", "assets": [{
                "name": "DocVoice.exe", "size": 3, "digest": "sha256:00",
                "browser_download_url": doc_voice.LINK_TAI_HOP_LE + "v9.9.9/DocVoice.exe"}]}).encode())
        goc = self._goc(voi_wav)
        with (mock.patch.object(sys, "frozen", True, create=True),
              mock.patch.object(sys, "executable", os.path.join(goc, "DocVoice.exe")),
              mock.patch.object(doc_voice.gc, "thu_muc_goc_app", lambda: goc),
              mock.patch.object(doc_voice.urllib.request, "urlopen", urlopen)):
            return doc_voice.ban_day_du(), doc_voice.kiem_ban_moi(), goi

    def test_ban_day_du_khong_goi_github(self):
        day_du, moi, goi = self._kiem(voi_wav=True)
        self.assertTrue(day_du)
        self.assertIsNone(moi)
        self.assertEqual(goi, [])

    def test_ban_chi_dau_giong_van_tu_cap_nhat(self):
        day_du, moi, goi = self._kiem(voi_wav=False)
        self.assertFalse(day_du)
        self.assertEqual(moi[0], "9.9.9")
        self.assertEqual(len(goi), 1)

    def test_chay_bang_python_khong_phai_ban_day_du(self):
        self.assertFalse(doc_voice.ban_day_du())


class KiemTraKhongDungKhoaChuDuAn(unittest.TestCase):
    """Quét mã nguồn: đường tạo voice không đọc khoá, token, cookie của chủ dự án.
    Quét MỌI file .py ở gốc dự án (trừ file test), nên file mới cũng bị quét. Biến môi trường chỉ được đọc theo
    đúng tên của app; nhắc tới os.environ ở dạng khác (dict(os.environ), gán lại, from os import environ, getenv
    tên động) đều bị bắt, trừ hai chỗ truyền nguyên môi trường cho tiến trình con đã liệt kê."""
    CAM = ["youtube-hq", ".youtube", "google.json", "gemini", "api_key", "apikey", "api-key", "secret",
           "cookie", "bearer", "authorization", "oauth", "access_token", "refresh_token", "openai", "anthropic"]
    BIEN_MOI_TRUONG_DUOC = ("DOCVOICE_", "DOCGIONG_", "APPDATA", "LOCALAPPDATA", "TEMP", "TQDM_DISABLE", "PYTHONIOENCODING",
                            "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY")
    # truyền nguyên môi trường (không đọc giá trị nào) cho tiến trình con
    DUOC_TRUYEN_NGUYEN = ('dict(os.environ, PYTHONIOENCODING="utf-8", TQDM_DISABLE="1")',
                          'for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"')

    def _goc(self):
        return os.path.dirname(os.path.abspath(__file__))

    def _files(self):
        ds = sorted(f for f in os.listdir(self._goc()) if f.endswith(".py") and f != os.path.basename(__file__))
        self.assertIn("doc_voice.py", ds)
        return ds

    def _doc(self, ten):
        with open(os.path.join(self._goc(), ten), encoding="utf-8") as f:
            return f.read()

    def test_khong_nhac_khoa_token_cookie(self):
        for ten in self._files():
            for so, dong in enumerate(self._doc(ten).splitlines(), 1):
                thap = dong.lower()
                for tu in self.CAM:
                    self.assertNotIn(tu, thap, f"{ten}:{so} nhắc {tu!r}: {dong.strip()[:100]}")

    def _vi_pham(self, ma, ten="x.py"):
        """Danh sách chỗ đọc biến môi trường không được phép trong đoạn mã ma."""
        import re
        loi = []
        mau_duoc = re.compile(r"""os\.environ(?:\.get\(|\.setdefault\(|\[)\s*["']([A-Za-z0-9_]+)["']|os\.getenv\(\s*["']([A-Za-z0-9_]+)["']""")
        for so, dong in enumerate(ma.splitlines(), 1):
            if not re.search(r"environ|getenv", dong):
                continue
            if any(c in dong for c in self.DUOC_TRUYEN_NGUYEN):
                continue
            con_lai = mau_duoc.sub("", dong)
            for m in mau_duoc.finditer(dong):
                bien = m.group(1) or m.group(2)
                if not bien.startswith(self.BIEN_MOI_TRUONG_DUOC):
                    loi.append(f"{ten}:{so} đọc biến môi trường {bien}")
            if re.search(r"environ|getenv", con_lai):
                loi.append(f"{ten}:{so} dùng environ hoặc getenv không theo tên cố định: {dong.strip()[:90]}")
        return loi

    def test_bien_moi_truong_doc_chi_la_cai_dat_cua_app(self):
        for ten in self._files():
            self.assertEqual(self._vi_pham(self._doc(ten), ten), [])

    def test_bat_duoc_cac_khe_cu(self):
        """Các cách lách đã biết phải làm test đỏ."""
        khe = ['x = dict(os.environ).get("GEMINI_API_KEY")', "E = os.environ", 'from os import environ',
               'k = os.environ.get(ten_bien)', 'os.getenv(ten)', 'v = os.environ["PEXELS_KEY"]',
               'import os as o; o.environ.get("X")', 'x = {**os.environ}']
        for ma in khe:
            self.assertTrue(self._vi_pham(ma), ma)
        ok = ['x = os.environ.get("DOCVOICE_TU_DONG")', 'os.environ.setdefault("TQDM_DISABLE", "1")',
              'p = os.path.join(os.environ.get("TEMP", "."), "DocVoice")']
        for ma in ok:
            self.assertEqual(self._vi_pham(ma), [], ma)


class KiemTraProxyChetVaArthurLucMo(unittest.TestCase):
    """Mở app từ tiến trình có proxy tạm đã chết (HTTPS_PROXY=http://127.0.0.1:<cổng>) và APPDATA trống:
    không được để ô Ngôn ngữ, ô Giọng trống; Arthur phải có sẵn và được chọn."""

    def _cong_chet(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        cong = s.getsockname()[1]
        s.close()
        return cong

    def test_bo_proxy_may_nay_da_chet(self):
        env = {"HTTPS_PROXY": "http://127.0.0.1:%d" % self._cong_chet(), "HTTP_PROXY": "127.0.0.1:%d" % self._cong_chet(),
               "NO_PROXY": ""}
        bo = doc_voice.bo_proxy_chet(env)
        self.assertEqual(sorted(bo), ["HTTPS_PROXY", "HTTP_PROXY"])
        self.assertNotIn("HTTPS_PROXY", env)
        self.assertIn("127.0.0.1", env["NO_PROXY"].split(","))
        self.assertIn("localhost", env["no_proxy"].split(","))

    def test_giu_proxy_may_nay_con_song_va_proxy_may_khac(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        self.addCleanup(s.close)
        song = "http://127.0.0.1:%d" % s.getsockname()[1]
        env = {"HTTPS_PROXY": song, "HTTP_PROXY": "http://proxy.congty.vn:8080", "NO_PROXY": "intranet"}
        self.assertEqual(doc_voice.bo_proxy_chet(env), [])
        self.assertEqual(env["HTTPS_PROXY"], song)
        self.assertEqual(env["HTTP_PROXY"], "http://proxy.congty.vn:8080")
        self.assertTrue(env["NO_PROXY"].startswith("intranet,"))

    def test_appdata_trong_va_mang_loi_van_co_arthur(self):
        import tempfile
        tam = tempfile.mkdtemp()
        cu = (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.gc.ds_giong, doc_voice.App._tai_giong)
        doc_voice.FILE_CAI_DAT = os.path.join(tam, "cai_dat.json")
        doc_voice.FILE_GIONG = os.path.join(tam, "giong.json")         # chưa có: như máy mới cài
        doc_voice.gc.ds_giong = lambda: {"arthur": {"ten": "Arthur (clone)", "gioi": "Nam"}}

        def loi_mang(self):
            self.after(0, lambda: self.trang_thai.set("Không tải được danh sách giọng: Cannot connect to host"))
        doc_voice.App._tai_giong = loi_mang

        def tra():
            (doc_voice.FILE_CAI_DAT, doc_voice.FILE_GIONG, doc_voice.gc.ds_giong, doc_voice.App._tai_giong) = cu
            shutil.rmtree(tam, ignore_errors=True)
        self.addCleanup(tra)
        app = mo_app_an(self)
        self.assertTrue(app.ngon_ngu.get())
        self.assertTrue(app.chon_giong.get())
        self.assertEqual(app._locales[app.ngon_ngu.current()], doc_voice.MA_CLONE)
        self.assertEqual(app._tham_so()[0], "clone:arthur")


class KiemTraKiemDocKhongTreo(unittest.TestCase):
    """Bộ kiểm whisper nạp model treo (ổ HDD bận, mạng không trả lời) thì app không được đứng ở "Bật bộ kiểm đọc";
    model.bin phải được đọc trước bằng khối lớn rồi mới nạp (NV-1299)."""

    def test_doc_dong_co_han_tra_rong_khi_treo(self):
        import subprocess
        import sys
        import time
        gc = doc_voice.gc
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.PIPE, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.addCleanup(p.kill)
        t = time.time()
        self.assertEqual(gc._doc_dong_co_han(p.stdout, 1), "")
        self.assertLess(time.time() - t, 5)

    def test_kiem_doc_treo_thi_bo_kiem(self):
        import sys
        import tempfile
        import time
        gc = doc_voice.gc
        cu = (gc._MA_KIEM, gc.KiemDoc.CHO_NAP)
        gc._MA_KIEM, gc.KiemDoc.CHO_NAP = "import time" + "\n" + "time.sleep(60)" + "\n", 1

        def tra():
            gc._MA_KIEM, gc.KiemDoc.CHO_NAP = cu
        self.addCleanup(tra)
        tam = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tam, True)
        t = time.time()
        kd = gc.KiemDoc(sys.executable, tam)
        self.assertFalse(kd.co)
        self.assertLess(time.time() - t, 10)

    def test_ma_kiem_nap_bo_nho_dem_truoc(self):
        self.assertIn("local_files_only=True", doc_voice.gc._MA_KIEM)
        import kiem_chat_luong
        with open(kiem_chat_luong.__file__, encoding="utf-8") as f:
            self.assertIn("local_files_only=True", f.read())

    def test_doc_truoc_model_truoc_khi_nap(self):
        self.assertEqual(kcl._MA_DOC_TRUOC, gc._MA_DOC_TRUOC)
        for ten, ma in (("giong_clone._MA_KIEM", gc._MA_KIEM), ("kiem_chat_luong._MA_NANG", kcl._MA_NANG)):
            goi = re.search(r"^\s*doc_truoc_model\(", ma, re.M)
            nap = re.search(r"WhisperModel\(", ma)
            self.assertTrue(goi and nap and goi.start() < nap.start(), ten + ": phải đọc trước model.bin rồi mới nạp")
            compile(ma, ten, "exec")

    def test_doc_truoc_model_doc_het_file_bang_khoi_lon(self):
        import sys
        import tempfile
        import types
        tam = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tam, True)
        with open(os.path.join(tam, "model.bin"), "wb") as f:
            f.write(os.urandom(40 << 20))
        doc = []

        class Tep:
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

            def readinto(self, b):
                n = self.f.readinto(b)
                doc.append((len(b), n))
                return n

        g = {"open": lambda *a, **k: Tep(open(*a, **k))}
        exec(gc._MA_DOC_TRUOC, g)
        utils = types.ModuleType("faster_whisper.utils")
        utils.download_model = lambda ten, local_files_only=False: tam if local_files_only else self.fail("gọi mạng")
        goi = types.ModuleType("faster_whisper")
        goi.utils = utils
        with patch.dict(sys.modules, {"faster_whisper": goi, "faster_whisper.utils": utils}):
            g["doc_truoc_model"]("small")
        self.assertEqual(sum(n for _, n in doc), 40 << 20)
        self.assertTrue(all(c >= 16 << 20 for c, _ in doc))

        def chua_co(ten, local_files_only=False):
            raise FileNotFoundError(ten)
        utils.download_model = chua_co
        doc.clear()
        with patch.dict(sys.modules, {"faster_whisper": goi, "faster_whisper.utils": utils}):
            g["doc_truoc_model"]("small")                   # chưa tải model: bỏ qua, không lỗi
        self.assertEqual(doc, [])


class KiemTraTkChiHuyOLuongChinh(unittest.TestCase):
    """Cả bộ test chạy một tiến trình (cổng test của phat-hanh.ps1) không được chết vì Tcl_AsyncDelete:
    bộ thông dịch Tcl của App test chỉ được huỷ ở luồng chính, kể cả khi bộ gom rác chạy ở luồng phụ."""

    # tiến trình con: mở App qua mo_app_an, một App còn bị "luồng nền" giữ lúc test dọn, rồi gom rác ở luồng phụ
    MA_CON = "\n".join([
        "import gc, os, sys, tempfile, threading, unittest",
        "sys.path.insert(0, sys.argv[1])",
        "import kiem_tra_doc_voice as k",
        "d = k.doc_voice",
        "tam = tempfile.mkdtemp()",
        "d.FILE_CAI_DAT, d.FILE_GIONG = os.path.join(tam, 'cd.json'), os.path.join(tam, 'g.json')",
        "d.App._tai_giong = lambda self: None",
        "giu = []",
        "class T(unittest.TestCase):",
        "    def test_mo(self):",
        "        k.mo_app_an(self)",
        "    def test_mo_con_bi_giu(self):",
        "        giu.append(k.mo_app_an(self))",
        "gc.disable()",
        "kq = unittest.TextTestRunner(verbosity=0).run(unittest.defaultTestLoader.loadTestsFromTestCase(T))",
        "giu.clear()",
        "t = threading.Thread(target=gc.collect)",
        "t.start()",
        "t.join()",
        "print('SONG', kq.wasSuccessful(), flush=True)",
    ])

    def test_moi_app_test_mo_qua_mo_app_an(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            ma = f.read()
        self.assertEqual(len(re.findall(r"doc_voice\.App\(", ma)), 1)        # chỉ một chỗ: trong mo_app_an
        self.assertEqual(re.findall(r"\b(?:tk|tkinter)\.Tk\(", ma), [])

    def test_gom_rac_o_luong_phu_khong_lam_chet_tien_trinh(self):
        import sys
        goc = os.path.dirname(os.path.abspath(__file__))
        r = subprocess.run([sys.executable, "-X", "utf8", "-c", self.MA_CON, goc], cwd=goc, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600, creationflags=CREATE_NO_WINDOW)
        ra = r.stdout + r.stderr
        self.assertNotIn("Tcl_AsyncDelete", ra)
        self.assertEqual(r.returncode, 0, ra[-1500:])
        self.assertIn("SONG True", r.stdout)


if __name__ == "__main__":
    unittest.main()
