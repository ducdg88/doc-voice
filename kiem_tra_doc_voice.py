import sys
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

    def test_dich_tieng_viet(self):
        cau_goc = "Hello world"
        dich = doc_voice.dich_tieng_viet(cau_goc)
        self.assertTrue(len(dich.strip()) > 0)
        print("Ket qua dich thu nghiem:", dich)

if __name__ == "__main__":
    unittest.main()
