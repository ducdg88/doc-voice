import json
import os
import shutil
import subprocess
import unittest
import doc_voice

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
        dai = doc_voice.tinh_do_dai_doan(b"123", fake_cau)
        self.assertAlmostEqual(dai, 3.5)
        # Khi không có câu, tính theo kích thước mp3
        dai_rong = doc_voice.tinh_do_dai_doan(b"\x00" * 12000, [])
        self.assertAlmostEqual(dai_rong, 2.0)

    def test_dich_tieng_viet(self):
        cau_goc = "Hello world"
        try:
            dich = doc_voice.dich_tieng_viet(cau_goc)
        except RuntimeError as e:      # test gọi Google Translate thật: bị giới hạn lượt (429) hay mất mạng thì bỏ qua, không phải lỗi mã
            if "HTTP Error 429" in str(e) or "urlopen error" in str(e) or "timed out" in str(e):
                self.skipTest(f"dịch thật không gọi được: {e}")
            raise
        self.assertTrue(len(dich.strip()) > 0)

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

    def test_danh_sach_giong_co_arthur(self):
        gc = doc_voice.gc
        self.assertIn("arthur", gc.GIONG_CLONE)
        self.assertEqual(set(gc.MUC_CAM_XUC), {"Trung tính", "Kể chuyện", "Hào hứng", gc.MUC_TU_DONG})
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
        app = doc_voice.App()
        app.withdraw()
        self.addCleanup(app.destroy)
        app.update()
        return app

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
        app = doc_voice.App()
        app.withdraw()
        self.addCleanup(app.destroy)
        app.update()
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
        app = doc_voice.App()
        app.withdraw()
        self.addCleanup(app.destroy)
        app.update()
        return app

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


if __name__ == "__main__":
    unittest.main()
