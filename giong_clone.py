"""Giọng clone local cho Đọc Voice.

Đường đi: văn bản gốc -> chia câu, đoạn (giữ chữ gốc cho phụ đề) -> chuẩn hoá cách đọc tiếng Anh
(số, ký hiệu, viết tắt) -> máy đọc VoxCPM2 đang nóng của DG Voice Studio (HTTP 127.0.0.1, model nạp
một lần, không mở thêm tiến trình GPU) sinh từng phần -> kiểm lại bằng whisper local, lệch thì sinh
lại -> lưu từng phần vào thư mục tạm để chạy tiếp khi dừng -> cắt lặng ở điểm qua 0, fade, chèn
khoảng lặng đều -> hậu kỳ ffmpeg (lọc dưới 80 Hz, khử ồn nhẹ, de-esser, loudnorm 2 lượt -14 LUFS)
-> mp3 320 kbps 48 kHz (+ wav tuỳ chọn) và srt khớp mốc thời gian thật.

Không dùng API trả phí, không đọc khoá hay token nào. Chỉ cần: DG Voice Studio (máy đọc VoxCPM2),
ffmpeg, và (tuỳ chọn) một python có faster-whisper để kiểm từng phần.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import wave
import zipfile

import numpy as np

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
TIEN_TO_CLONE = "clone:"

# Mức cảm xúc: tiền tố mô tả giọng đặt trong ngoặc ở đầu câu (Controllable Voice Cloning của
# VoxCPM2, model không đọc phần ngoặc) + cfg. Tiền tố ngắn để cả câu lọt khung 120 ký tự của
# máy đọc nóng (quá khung máy đọc tự cắt và phần sau mất tiền tố, giọng lệch cảm xúc).
MUC_CAM_XUC = {
    "Trung tính": {"tien_to": "", "cfg": 2.0, "toc": 0.94},
    "Kể chuyện": {"tien_to": "(warm storyteller)", "cfg": 2.0, "toc": 0.97},
    "Hào hứng": {"tien_to": "(very excited, enthusiastic, energetic, upbeat)", "cfg": 2.5, "toc": 1.0},
}
MUC_TU_DONG = "Tự động theo kịch bản"
# Tự động: mỗi câu tự gán một sắc thái theo luật và từ khoá chạy trên máy (không gọi AI, không khoá).
# Sắc thái chỉ đổi tiền tố, cfg (VoxCPM2) hoặc nhịp, cao độ Edge (máy Nhẹ); giọng mẫu ref.wav giữ nguyên.
MUC_CAM_XUC[MUC_TU_DONG] = {"tien_to": "", "cfg": 2.0, "toc": 0.97, "tu_dong": True}
MUC_MAC_DINH = MUC_TU_DONG
SAC_THAI = {
    "hồi hộp": {"tien_to": "(suspenseful, curious, hushed)", "cfg": 2.2},
    "căng thẳng": {"tien_to": "(tense, serious, slow, deliberate)", "cfg": 2.2},
    "hào hứng": {"tien_to": "(excited, energetic, amazed)", "cfg": 2.4},
    "kể chuyện": {"tien_to": "(warm storyteller)", "cfg": 2.0},
    "thân thiện": {"tien_to": "(friendly, warm, relaxed, slower)", "cfg": 2.0},
    "trầm": {"tien_to": "(calm, low, reflective)", "cfg": 2.0},
    "nhấn": {"tien_to": "(emphatic, deliberate)", "cfg": 2.2},
}
SAC_NHE = "kể chuyện"         # câu đọc ra lệch giọng thì sinh lại bằng sắc thái nhẹ này
THE_SAC_THAI = {               # thẻ người dùng ghi trong kịch bản để ghi đè, không bị đọc ra
    "hồi hộp": "hồi hộp", "hoi hop": "hồi hộp", "suspense": "hồi hộp", "tò mò": "hồi hộp",
    "căng thẳng": "căng thẳng", "cang thang": "căng thẳng", "tense": "căng thẳng",
    "hào hứng": "hào hứng", "hao hung": "hào hứng", "excited": "hào hứng",
    "kể chuyện": "kể chuyện", "ke chuyen": "kể chuyện", "narrate": "kể chuyện",
    "thân thiện": "thân thiện", "than thien": "thân thiện", "friendly": "thân thiện",
    "trầm": "trầm", "tram": "trầm", "calm": "trầm",
    "nhấn": "nhấn", "nhan": "nhấn", "emphasis": "nhấn",
}
_RE_THE = re.compile(r"\[\s*([^\]\n]{2,20}?)\s*\]")
_KT_KEU_GOI = re.compile(r"\b(subscrib\w*|like (this|the) video|hit (the )?like|smash (that|the) like|comment\w*|"
                         r"notification|bell icon|thanks? (you )?for watching|see you (in|next)|share this)\b", re.I)
_KT_NGUY_HIEM = re.compile(r"\b(terrif\w*|danger\w*|wrong cut|destroy\w*|deadl\w*|fear\w*|scar(y|ed)|risk\w*|"
                           r"disaster\w*|collaps\w*|ruin\w*|nightmare\w*|warning|tension|crack(ed|s)? apart|"
                           r"lose everything|horrif\w*|threat\w*|panic\w*)\b", re.I)
_KT_KHAM_PHA = re.compile(r"\b(discover\w*|extraordinar\w*|speechless|incredibl\w*|amazing|unbelievabl\w*|"
                          r"stunning|masterpiece\w*|breathtaking|reveal\w*|treasure\w*|astonish\w*|"
                          r"jaw[- ]dropping|spectacular|wow)\b", re.I)


def la_the_sac_thai(chu):
    m = _RE_THE.fullmatch((chu or "").strip())
    return THE_SAC_THAI.get(m.group(1).strip().lower()) if m else None


def phan_loai_cau(cau, i_doan=1, so_doan=3):
    """Câu -> sắc thái, chạy bằng luật trên máy. Thứ tự ưu tiên: kêu gọi like, subscribe (thân thiện);
    câu một hai từ (nhấn); nguy hiểm, sợ hãi (căng thẳng); khám phá, kết quả (hào hứng);
    đoạn mở đầu (hook) hoặc câu hỏi (hồi hộp); còn lại kể chuyện."""
    tu = re.findall(r"[A-Za-z0-9']+", cau or "")
    if _KT_KEU_GOI.search(cau):
        return "thân thiện"
    if 0 < len(tu) <= 2:
        return "nhấn"
    if _KT_NGUY_HIEM.search(cau):
        return "căng thẳng"
    if _KT_KHAM_PHA.search(cau):
        return "hào hứng"
    if i_doan < (2 if so_doan >= 5 else 1) or cau.rstrip(" '\")]").endswith("?"):
        return "hồi hộp"
    return "kể chuyện"

# Kho giọng clone: thư mục giong_mau/<slug>/ref.wav (+ ref.txt là lời của mẫu).
GIONG_CLONE = {
    "arthur": {"ten": "Arthur (clone)", "gioi": "Nam", "locale": "en-US",
               "mo_ta": "Giọng nam Mỹ kể chuyện, audiobook, documentary",
               "quyen_dung": "Giọng Arthur dựng bằng tổng hợp giọng nói, kèm sẵn trong ứng dụng."},
}

KHUNG_MAY_DOC = 118          # máy đọc nóng tự cắt câu dài hơn 120 ký tự
LANG_CAU = 0.36              # giây lặng giữa hai câu
LANG_DOAN = 0.78             # giây lặng giữa hai đoạn văn
LANG_PHAY = 0.14             # giây lặng ở chỗ câu dài bị cắt đôi
LANG_LUNG = 0.62             # giây lặng sau dấu ba chấm giữa đoạn (ngắt lửng)
XFADE_MS = 18                # fade ở mỗi mép ghép
SRT_DONG = 42                # ký tự tối đa mỗi dòng phụ đề
SRT_SO_DONG = 2


# ---------------------------------------------------------------- chuẩn hoá cách đọc tiếng Anh
_DON_VI = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
           "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
           "eighteen", "nineteen"]
_CHUC = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_NHOM = [(10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand")]
_THU_TU_DAC_BIET = {"one": "first", "two": "second", "three": "third", "five": "fifth",
                    "eight": "eighth", "nine": "ninth", "twelve": "twelfth"}


def so_thanh_chu(n):
    """Số nguyên -> chữ tiếng Anh (3000 -> three thousand)."""
    n = int(n)
    if n < 0:
        return "minus " + so_thanh_chu(-n)
    if n < 20:
        return _DON_VI[n]
    if n < 100:
        return _CHUC[n // 10] + ("" if n % 10 == 0 else "-" + _DON_VI[n % 10])
    if n < 1000:
        du = n % 100
        return _DON_VI[n // 100] + " hundred" + ("" if du == 0 else " " + so_thanh_chu(du))
    for gia_tri, ten in _NHOM:
        if n >= gia_tri:
            du = n % gia_tri
            return so_thanh_chu(n // gia_tri) + " " + ten + ("" if du == 0 else " " + so_thanh_chu(du))
    return str(n)


def nam_thanh_chu(n):
    """Năm -> cách đọc năm (1969 -> nineteen sixty-nine, 2004 -> two thousand four)."""
    n = int(n)
    if 2000 <= n <= 2009:
        return so_thanh_chu(n)
    dau, cuoi = divmod(n, 100)
    if cuoi == 0:
        return so_thanh_chu(dau) + " hundred"
    if cuoi < 10:
        return so_thanh_chu(dau) + " oh " + so_thanh_chu(cuoi)
    return so_thanh_chu(dau) + " " + so_thanh_chu(cuoi)


def thu_tu_thanh_chu(n):
    chu = so_thanh_chu(n)
    dau, _, cuoi = chu.rpartition(" ")
    tach = cuoi.split("-")
    w = tach[-1]
    if w in _THU_TU_DAC_BIET:
        w = _THU_TU_DAC_BIET[w]
    elif w.endswith("y"):
        w = w[:-1] + "ieth"
    else:
        w = w + "th"
    tach[-1] = w
    return ((dau + " ") if dau else "") + "-".join(tach)


def _so_thap_phan(s):
    s = s.replace(",", "")
    if "." in s:
        nguyen, le = s.split(".", 1)
        return so_thanh_chu(int(nguyen or 0)) + " point " + " ".join(_DON_VI[int(c)] for c in le)
    return so_thanh_chu(int(s))


_VIET_TAT = [
    (r"\bDr\.", "Doctor"), (r"\bMr\.", "Mister"), (r"\bMrs\.", "Missus"), (r"\bMs\.", "Miz"),
    (r"\bSt\.", "Saint"), (r"\bvs\.?", "versus"), (r"\be\.g\.", "for example"),
    (r"\bi\.e\.", "that is"), (r"\betc\.", "et cetera"), (r"\bapprox\.", "approximately"),
    (r"\bU\.S\.A?\.?", "U S"), (r"\bU\.K\.", "U K"), (r"\bNo\.\s*(?=\d)", "number "),
]
_DON_VI_DO = {"km": "kilometers", "kg": "kilograms", "cm": "centimeters", "mm": "millimeters",
              "mph": "miles per hour", "km/h": "kilometers per hour", "m": "meters",
              "ft": "feet", "lb": "pounds", "lbs": "pounds", "°C": "degrees Celsius",
              "°F": "degrees Fahrenheit", "GB": "gigabytes", "MB": "megabytes"}


def chuan_hoa_tieng_anh(chu):
    """Đưa chữ về dạng máy đọc phát âm đúng: số, năm, %, $, ký hiệu, viết tắt, dấu đặc biệt."""
    s = chu
    s = s.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = re.sub(r"\s*[\u2013\u2014]\s*", ", ", s)          # gạch dài -> ngắt phẩy
    s = s.replace("\u2026", "...")
    for mau, thay in _VIET_TAT:
        s = re.sub(mau, thay, s)
    s = s.replace("&", " and ").replace("+", " plus ").replace("=", " equals ")
    # $12.5 million, $300
    s = re.sub(r"\$(\d[\d,]*(?:\.\d+)?)(?:\s*(million|billion|trillion|thousand))?",
               lambda m: _so_thap_phan(m.group(1)) + (" " + m.group(2) if m.group(2) else "") + " dollars", s)
    s = re.sub(r"(\d[\d,]*(?:\.\d+)?)\s*%", lambda m: _so_thap_phan(m.group(1)) + " percent", s)
    # khoảng năm 1990s, 1800s
    s = re.sub(r"\b(1\d|20)(\d0)s\b", lambda m: re.sub(r"y$", "ies", nam_thanh_chu(m.group(1) + m.group(2)))
               if not nam_thanh_chu(m.group(1) + m.group(2)).endswith("hundred")
               else nam_thanh_chu(m.group(1) + m.group(2)) + "s", s)
    # thứ tự 1st 2nd 23rd 4th
    s = re.sub(r"\b(\d+)(st|nd|rd|th)\b", lambda m: thu_tu_thanh_chu(m.group(1)), s)
    # đơn vị đo sau số
    s = re.sub(r"(\d[\d,]*(?:\.\d+)?)\s?(km/h|°C|°F|km|kg|cm|mm|mph|ft|lbs|lb|GB|MB|m)\b",
               lambda m: m.group(1) + " " + _DON_VI_DO[m.group(2)], s)
    # năm đứng một mình (1000 đến 2099, không có dấu phẩy nghìn)
    s = re.sub(r"(?<![\d,.])(1[0-9]{3}|20[0-9]{2})(?![\d,.]\d)", lambda m: nam_thanh_chu(m.group(1)), s)
    # số thập phân, số có dấu phẩy nghìn, số nguyên
    s = re.sub(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d+", lambda m: _so_thap_phan(m.group(0)), s)
    s = re.sub(r"\s*[()\[\]]\s*", ", ", s)     # ngoặc giữa câu -> ngắt phẩy (ngoặc đầu câu là lệnh giọng)
    s = re.sub(r"[*#_`~^|<>{}]", " ", s)
    s = re.sub(r"(,\s*)+", ", ", s)
    s = re.sub(r"^[,\s]+|,\s*(?=[.!?;:]|$)", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.!?;:])", r"\1", s)
    return s


def chu_cho_wer(chu):
    """Chuẩn hoá để so lời: chữ thường, số -> chữ, bỏ dấu câu, gạch nối thành khoảng trắng."""
    s = chuan_hoa_tieng_anh(chu).lower()
    s = s.replace("-", " ")
    s = re.sub(r"[^a-z0-9' ]+", " ", s)
    s = s.replace("'", "")
    return s.split()


def wer(goc, nghe):
    """Word error rate giữa danh sách từ gốc và danh sách từ nghe được (Levenshtein theo từ)."""
    a, b = goc, nghe
    if not a:
        return 0.0 if not b else 1.0
    truoc = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        hien = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            hien[j] = min(truoc[j] + 1, hien[j - 1] + 1, truoc[j - 1] + (a[i - 1] != b[j - 1]))
        truoc = hien
    return truoc[-1] / len(a)


# ---------------------------------------------------------------- chia câu cho giọng clone
_CAT_CAU = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])")


def _cat_cau_dai(cau, toi_da):
    """Câu dài quá khung: chia thành ít phần nhất, các phần dài gần bằng nhau, ưu tiên cắt ở
    dấu phẩy, chấm phẩy, hai chấm; không có dấu thì cắt ở khoảng trắng gần giữa nhất."""
    ra = []
    con = cau.strip()
    while len(chuan_hoa_tieng_anh(con)) > toi_da:
        dai = len(chuan_hoa_tieng_anh(con))
        so_phan = -(-dai // toi_da)
        dich = dai / so_phan
        tot, diem_tot = -1, None
        for m in re.finditer(r"\s", con):
            dau = chuan_hoa_tieng_anh(con[: m.start()])
            if not dau or len(dau) > toi_da:
                continue
            diem = abs(len(dau) - dich)
            if con[m.start() - 1] in ",;:":
                diem -= 18
            if diem_tot is None or diem < diem_tot:
                tot, diem_tot = m.start(), diem
        if tot <= 0:
            break
        ra.append(con[:tot].strip())
        con = con[tot:].strip()
    if con:
        ra.append(con)
    return ra


def chia_phan_clone(van_ban, toi_da=KHUNG_MAY_DOC, dau_ngan=0, tu_dong=False):
    """Văn bản -> danh sách phần {chu (gốc, cho phụ đề), ngat (lặng sau phần: phay|cau|lung|doan)}.
    Đoạn văn tách bởi dòng trống hoặc xuống dòng; câu tách bởi . ! ? và dấu ba chấm; câu dài cắt ở dấu phẩy.
    dau_ngan > 0: phần đầu tiên không dài quá dau_ngan ký tự để bấm Nghe là có tiếng ngay.
    Thẻ sắc thái [hồi hộp], [hào hứng]... không bị đọc; tu_dong=True thì mỗi phần có thêm "sac"
    (thẻ gần nhất phía trước trong đoạn, không có thẻ thì phan_loai_cau)."""
    phan = []
    doan_van = [d.strip() for d in re.split(r"\n\s*\n|\n", van_ban or "") if re.search(r"\w", d)]
    so_doan = len(doan_van)
    for di, doan in enumerate(doan_van):
        cac_cau, the = [], None
        for manh_the in re.split(r"(\[[^\]\n]{2,20}\])", doan):
            st = la_the_sac_thai(manh_the)
            if st:
                the = st
                continue
            for c in _CAT_CAU.split(re.sub(r"\s+", " ", manh_the)):
                for c2 in re.split(r"(?<=…)\s+|(?<=\.\.\.)\s+", c):
                    if re.search(r"\w", c2):
                        cac_cau.append((c2.strip(), the))
        if not cac_cau:
            continue
        for ci, (cau, the_cau) in enumerate(cac_cau):
            sac = (the_cau or phan_loai_cau(cau, di, so_doan)) if tu_dong else None
            manh = _cat_cau_dai(cau, toi_da)
            if dau_ngan and not phan and len(chuan_hoa_tieng_anh(cau)) > dau_ngan:
                ngan = _cat_cau_dai(cau, dau_ngan)
                manh = [ngan[0]] + (_cat_cau_dai(" ".join(ngan[1:]), toi_da) if len(ngan) > 1 else [])
            for mi, m in enumerate(manh):
                if mi < len(manh) - 1:
                    ngat = "phay"
                elif ci < len(cac_cau) - 1:
                    ngat = "lung" if re.search(r"(…|\.\.\.)[\"')\]]*$", m) else "cau"
                else:
                    ngat = "doan"
                phan.append({"chu": m, "ngat": ngat, **({"sac": sac} if tu_dong else {})})
    if phan:
        phan[-1]["ngat"] = "het"
    return phan


def chia_theo_may(van_ban, may, muc=MUC_MAC_DINH):
    """Chia phần đúng như DocDaiClone.chay sẽ chia cho máy đọc này (để giao diện biết câu thứ mấy)."""
    cx = MUC_CAM_XUC.get(muc, MUC_CAM_XUC[MUC_MAC_DINH])
    tu_dong = bool(cx.get("tu_dong"))
    khung = getattr(may, "khung", KHUNG_MAY_DOC)
    if getattr(may, "nhan_tien_to", True):
        khung -= max(len(v["tien_to"]) for v in SAC_THAI.values()) if tu_dong else len(cx["tien_to"])
    return chia_phan_clone(van_ban, khung, getattr(may, "khung_dau", 0), tu_dong=tu_dong)


def lang_sau(ngat):
    return {"phay": LANG_PHAY, "cau": LANG_CAU, "doan": LANG_DOAN, "lung": LANG_LUNG}.get(ngat, 0.0)


# ---------------------------------------------------------------- âm thanh: đọc, cắt lặng, ghép
def doc_wav(duong):
    with wave.open(duong, "rb") as w:
        sr, kenh, rong = w.getframerate(), w.getnchannels(), w.getsampwidth()
        du_lieu = w.readframes(w.getnframes())
    if rong == 2:
        x = np.frombuffer(du_lieu, dtype="<i2").astype(np.float32) / 32768.0
    elif rong == 4:
        x = np.frombuffer(du_lieu, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"wav {rong * 8} bit chưa hỗ trợ")
    if kenh > 1:
        x = x.reshape(-1, kenh).mean(axis=1)
    return x, sr


def ghi_wav(duong, x, sr):
    y = np.clip(np.asarray(x, dtype=np.float32), -1.0, 1.0)
    with wave.open(duong, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((y * 32767.0).astype("<i2").tobytes())


def _diem_qua_0(x, i, tam):
    """Dời điểm cắt i về điểm qua 0 gần nhất trong khoảng ±tam mẫu."""
    a, b = max(0, i - tam), min(len(x) - 1, i + tam)
    if b - a < 2:
        return i
    doan = x[a:b]
    doi_dau = np.where(np.signbit(doan[:-1]) != np.signbit(doan[1:]))[0]
    if len(doi_dau) == 0:
        return i
    return int(a + doi_dau[np.argmin(np.abs(doi_dau + a - i))])


def cat_lang(x, sr, nguong_db=-42.0, dem_ms=40):
    """Cắt lặng đầu và cuối theo năng lượng khung 10 ms, chừa đệm, cắt ở điểm qua 0."""
    if len(x) == 0:
        return x
    khung = max(1, int(sr * 0.01))
    n = len(x) // khung
    if n == 0:
        return x
    nang = np.sqrt(np.mean(x[: n * khung].reshape(n, khung) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(nang)
    nguong = max(nguong_db, db.max() - 45.0)
    co_tieng = np.where(db > nguong)[0]
    if len(co_tieng) == 0:
        return x[:0]
    dem = int(sr * dem_ms / 1000)
    dau = max(0, co_tieng[0] * khung - dem)
    cuoi = min(len(x), (co_tieng[-1] + 1) * khung + dem)
    tam = int(sr * 0.005)
    dau = _diem_qua_0(x, dau, tam)
    cuoi = _diem_qua_0(x, cuoi, tam)
    return x[dau:cuoi]


def lam_mem_mep(x, sr, ms=XFADE_MS):
    n = min(len(x) // 2, int(sr * ms / 1000))
    if n <= 0:
        return x
    y = x.copy()
    duong = np.linspace(0.0, 1.0, n, dtype=np.float32)
    y[:n] *= duong
    y[-n:] *= duong[::-1]
    return y


def ghep_phan(cac_phan, sr):
    """cac_phan: [(mảng âm thanh đã cắt lặng, giây lặng sau)].
    Trả (âm thanh ghép, [(bắt đầu, kết thúc)] từng phần). Mép mỗi phần fade (crossfade với lặng),
    phần nối liền (lặng 0) thì chồng mép XFADE_MS để không vấp."""
    ra, moc = [], []
    tong = 0
    xf = int(sr * XFADE_MS / 1000)
    lang_truoc = None
    for am, lang in cac_phan:
        am = lam_mem_mep(np.asarray(am, dtype=np.float32), sr)
        if lang_truoc == 0 and ra and len(ra[-1]) >= xf and len(am) >= xf:
            # chồng mép: đuôi phần trước đã fade-out, đầu phần này fade-in -> cộng lại
            duoi = ra[-1]
            duoi[-xf:] = duoi[-xf:] + am[:xf]
            am = am[xf:]
            bat_dau = tong - xf
        else:
            bat_dau = tong
        ra.append(am)
        tong += len(am)
        moc.append((bat_dau / sr, tong / sr))
        if lang > 0:
            khoang = np.zeros(int(round(sr * lang)), dtype=np.float32)
            ra.append(khoang)
            tong += len(khoang)
        lang_truoc = lang
    if not ra:
        return np.zeros(0, dtype=np.float32), []
    return np.concatenate(ra), moc


# ---------------------------------------------------------------- phụ đề
def gio_srt(giay):
    ms = int(round(max(0.0, giay) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def ngat_dong(chu, rong=SRT_DONG):
    """Ngắt một cụm phụ đề thành tối đa 2 dòng, mỗi dòng <= rong ký tự, cân hai dòng."""
    chu = " ".join(chu.split())
    if len(chu) <= rong:
        return [chu]
    tu = chu.split(" ")
    tot, diem = None, None
    for i in range(1, len(tu)):
        a, b = " ".join(tu[:i]), " ".join(tu[i:])
        if len(a) <= rong and len(b) <= rong:
            d = abs(len(a) - len(b))
            if diem is None or d < diem:
                tot, diem = [a, b], d
    return tot or [chu]


def _vua_khung(chu, rong, so_dong):
    dong = ngat_dong(chu, rong)
    return len(dong) <= so_dong and all(len(d) <= rong for d in dong)


def chia_cum_srt(chu, rong=SRT_DONG, so_dong=SRT_SO_DONG):
    """Chia chữ thành ít cụm nhất, các cụm dài gần bằng nhau (không để sót một hai từ lẻ ở cụm
    cuối), mỗi cụm vừa so_dong dòng x rong ký tự."""
    tu = chu.split()
    if not tu:
        return []
    if _vua_khung(chu, rong, so_dong):
        return [" ".join(tu)]
    n = max(2, -(-len(chu) // (rong * so_dong - 6)))
    while n <= len(tu):
        dich = len(" ".join(tu)) / n
        cum, hien, ok = [], [], True
        for i, t in enumerate(tu):
            thu = " ".join(hien + [t])
            con_lai = n - len(cum) - 1
            if hien and (len(thu) > dich + 4 or not _vua_khung(thu, rong, so_dong)) and con_lai > 0:
                cum.append(" ".join(hien))
                hien = [t]
            else:
                hien.append(t)
        cum.append(" ".join(hien))
        if all(_vua_khung(c, rong, so_dong) for c in cum):
            return cum
        n += 1
    return tu


def tao_srt_clone(muc_thoi_gian, kieu_shorts=False, chia_shorts=None):
    """muc_thoi_gian: [(bắt đầu, kết thúc, chữ gốc)] từng phần. Phần dài chia cụm theo tỉ lệ ký tự."""
    dong, so = [], 0
    for bd, kt, chu in muc_thoi_gian:
        if kieu_shorts and chia_shorts:
            cum = chia_shorts(chu)
        else:
            cum = chia_cum_srt(chu)
        tong = sum(len(c) for c in cum) or 1
        t = bd
        for c in cum:
            dai = (kt - bd) * len(c) / tong
            so += 1
            loi = [c] if kieu_shorts else ngat_dong(c)
            dong.append(f"{so}\n{gio_srt(t)} --> {gio_srt(t + dai)}\n" + "\n".join(loi) + "\n")
            t += dai
    return "\n".join(dong)


# ---------------------------------------------------------------- máy đọc VoxCPM2 (DG Voice Studio)
def thu_muc_goc_app():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


# Thư mục giọng người dùng nhập (app đặt lại = %APPDATA%\\DocVoice\\giong_clone khi chạy exe)
THU_MUC_GIONG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "giong_clone_nguoi_dung")
DUOI_GOI = ".dgvoice"
GOI_TOI_DA = 30 * 1024 * 1024          # gói giọng tối đa 30 MB
_TEN_TRONG_GOI = {"voice.json", "ref.wav", "ref.txt"}


def _thu_muc_giong(slug):
    return os.path.join(THU_MUC_GIONG, safe_slug(slug))


def safe_slug(slug):
    s = re.sub(r"[^a-z0-9-]+", "-", str(slug or "").lower()).strip("-")
    return s[:40]


def duong_se_dong_goi(slug):
    """Dấu giọng (embedding OpenVoice, 256 số, 1 KB) đóng sẵn trong app, không kèm âm thanh mẫu."""
    return os.path.join(thu_muc_goc_app(), "giong_nhung", safe_slug(slug), "se.npy")


def la_dau_giong(duong):
    """True khi 'mẫu' chỉ là file dấu giọng .npy (chỉ máy Nhẹ dùng được), không phải âm thanh mẫu."""
    return str(duong).lower().endswith(".npy")


def duong_mau(slug):
    """Mẫu giọng: ưu tiên giọng người dùng nhập, rồi mẫu .wav đóng sẵn (bản có kèm mẫu),
    rồi dấu giọng .npy đóng sẵn (bản gửi người ngoài)."""
    rieng = os.path.join(_thu_muc_giong(slug), "ref.wav")
    if os.path.isfile(rieng):
        return rieng
    dong_goi = os.path.join(thu_muc_goc_app(), "giong_mau", slug, "ref.wav")
    if os.path.isfile(dong_goi):
        return dong_goi
    se = duong_se_dong_goi(slug)
    return se if os.path.isfile(se) else dong_goi


def ds_giong():
    """{slug: thông tin} gồm giọng đóng sẵn (mẫu .wav hoặc dấu giọng .npy) và giọng người dùng đã nhập."""
    ds = {}
    for slug, tt in GIONG_CLONE.items():
        if os.path.isfile(os.path.join(thu_muc_goc_app(), "giong_mau", slug, "ref.wav")) or                 os.path.isfile(duong_se_dong_goi(slug)):
            ds[slug] = dict(tt, nguon="đóng sẵn")
    if os.path.isdir(THU_MUC_GIONG):
        for slug in sorted(os.listdir(THU_MUC_GIONG)):
            tt = _doc(os.path.join(THU_MUC_GIONG, slug, "voice.json"), None)
            if tt and os.path.isfile(os.path.join(THU_MUC_GIONG, slug, "ref.wav")):
                ds[slug] = dict(tt, nguon="đã nhập")
    return ds


def xuat_goi_giong(slug, duong_ra, quyen_dung=""):
    """Đóng một giọng thành file .dgvoice (zip: voice.json, ref.wav, ref.txt) để gửi người khác."""
    slug = safe_slug(slug)
    tt = ds_giong().get(slug)
    if not tt:
        raise LoiClone(f"Không có giọng '{slug}'")
    ref = duong_mau(slug)
    if la_dau_giong(ref):
        raise LoiClone("Giọng này đóng sẵn trong app dưới dạng dấu giọng, không có âm thanh mẫu để xuất thành gói .dgvoice.")
    meta = {k: v for k, v in tt.items() if k not in ("nguon",)}
    meta.update({"slug": slug, "dinh_dang": 1, "app": "Doc Voice",
                 "muc_cam_xuc": {k: {kk: vv for kk, vv in v.items()} for k, v in MUC_CAM_XUC.items()},
                 "quyen_dung": quyen_dung or tt.get("quyen_dung") or ""})
    tam = duong_ra + ".part"
    with zipfile.ZipFile(tam, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("voice.json", json.dumps(meta, ensure_ascii=False, indent=1))
        z.write(ref, "ref.wav")
        txt = os.path.join(os.path.dirname(ref), "ref.txt")
        if os.path.isfile(txt):
            z.write(txt, "ref.txt")
    os.replace(tam, duong_ra)
    return duong_ra


def nhap_goi_giong(duong, ghi_de=True):
    """Kiểm và nhập gói .dgvoice vào kho giọng người dùng. Chặn tên file lạ, đường dẫn thoát thư mục,
    gói quá lớn, wav hỏng. Trả thông tin giọng."""
    if os.path.getsize(duong) > GOI_TOI_DA:
        raise LoiClone("Gói giọng quá lớn (tối đa 30 MB)")
    try:
        z = zipfile.ZipFile(duong)
    except zipfile.BadZipFile:
        raise LoiClone("File không phải gói giọng .dgvoice")
    with z:
        ten = set(z.namelist())
        la = [n for n in ten if n not in _TEN_TRONG_GOI]
        if la:
            raise LoiClone(f"Gói giọng có file lạ: {', '.join(sorted(la))[:200]}")
        if not {"voice.json", "ref.wav"} <= ten:
            raise LoiClone("Gói giọng thiếu voice.json hoặc ref.wav")
        if sum(i.file_size for i in z.infolist()) > GOI_TOI_DA:
            raise LoiClone("Gói giọng giải nén quá lớn")
        try:
            meta = json.loads(z.read("voice.json").decode("utf-8"))
        except ValueError:
            raise LoiClone("voice.json hỏng")
        slug = safe_slug(meta.get("slug") or meta.get("ten"))
        if not slug:
            raise LoiClone("Gói giọng thiếu tên")
        dich = _thu_muc_giong(slug)
        if os.path.isdir(dich) and not ghi_de:
            raise LoiClone(f"Đã có giọng '{slug}'")
        tam = dich + ".nhap"
        shutil.rmtree(tam, ignore_errors=True)
        os.makedirs(tam)
        for n in ten:
            with z.open(n) as nguon, open(os.path.join(tam, n), "wb") as f:
                shutil.copyfileobj(nguon, f)
    try:
        x, sr = doc_wav(os.path.join(tam, "ref.wav"))
        giay = len(x) / sr
    except Exception:
        shutil.rmtree(tam, ignore_errors=True)
        raise LoiClone("ref.wav trong gói không đọc được (cần WAV PCM 16 bit)")
    if not 3 <= giay <= 60:
        shutil.rmtree(tam, ignore_errors=True)
        raise LoiClone(f"Mẫu giọng dài {giay:.1f} giây, cần 3 đến 60 giây")
    tt = {"ten": str(meta.get("ten") or slug)[:60], "gioi": str(meta.get("gioi") or "")[:10],
          "locale": str(meta.get("locale") or "en-US")[:10], "mo_ta": str(meta.get("mo_ta") or "")[:300],
          "quyen_dung": str(meta.get("quyen_dung") or "")[:500], "slug": slug}
    with open(os.path.join(tam, "voice.json"), "w", encoding="utf-8") as f:
        json.dump(tt, f, ensure_ascii=False, indent=1)
    shutil.rmtree(dich, ignore_errors=True)
    os.replace(tam, dich)
    return tt


class LoiClone(RuntimeError):
    pass


class MayDocVoxCPM:
    """Gọi máy đọc nóng của DG Voice Studio. Chưa nóng thì bật nó (ẩn, CREATE_NO_WINDOW)."""

    def __init__(self, thu_muc_studio=r"E:\DG Voice Studio"):
        self.studio = thu_muc_studio

    def _cfg(self):
        try:
            with open(os.path.join(self.studio, "config.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def cong(self):
        try:
            with open(os.path.join(self.studio, ".daemon.json"), encoding="utf-8") as f:
                return int(json.load(f).get("port"))
        except (OSError, ValueError, TypeError):
            return int(self._cfg().get("daemon_port") or 8796)

    def _http(self, phuong_thuc, duong, du_lieu=None, cho=900):
        url = f"http://127.0.0.1:{self.cong()}{duong}"
        body = json.dumps(du_lieu).encode("utf-8") if du_lieu is not None else None
        req = urllib.request.Request(url, data=body, method=phuong_thuc,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=cho) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"ok": False, "error": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def trang_thai(self):
        r = self._http("GET", "/ping", cho=5)
        return r if r.get("app") == "dg-voice-daemon" else {"running": False, "error": r.get("error")}

    def co_san(self):
        return os.path.isfile(os.path.join(self.studio, "voice_daemon.py"))

    def bat(self, cho_toi_da=900, bao=None):
        st = self.trang_thai()
        if st.get("loaded"):
            return st
        if not st.get("app"):
            py = self._cfg().get("voxcpm_python") or r"E:\MONEY X3\telegram-launch-bot\.venv-voxcpm\Scripts\python.exe"
            if not os.path.isfile(py):
                raise LoiClone(f"Không thấy python của VoxCPM: {py}")
            nhat_ky = open(os.path.join(self.studio, "daemon.log"), "a", encoding="utf-8")
            subprocess.Popen([py, os.path.join(self.studio, "voice_daemon.py")], cwd=self.studio,
                             stdout=nhat_ky, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             creationflags=NO_WINDOW)
        bat_dau = time.time()
        while time.time() - bat_dau < cho_toi_da:
            st = self.trang_thai()
            if st.get("loaded"):
                return st
            if bao:
                bao(f"Đang nạp máy đọc giọng clone ({int(time.time() - bat_dau)} giây)...")
            time.sleep(3)
        raise LoiClone("Máy đọc giọng clone không lên sau 15 phút, xem E:\\DG Voice Studio\\daemon.log")

    ten = "Chuẩn cao (GPU)"
    khung = KHUNG_MAY_DOC
    nhan_tien_to = True
    kiem_song_song = True      # sinh ở tiến trình GPU khác: CPU nghe lại câu trước cùng lúc

    def sinh(self, chu, ref, ra, cfg=2.0, buoc=16, muc=None):
        r = self._http("POST", "/say", {"text": chu, "ref": ref, "out": ra, "cfg": cfg, "timesteps": buoc},
                       cho=1800)
        if not r.get("ok") or not os.path.isfile(ra):
            raise LoiClone(r.get("error") or "máy đọc không trả âm thanh")
        return r


# ---------------------------------------------------------------- máy đọc GPU riêng của Đọc Voice
# Chạy trong python của VoxCPM (venv của DG Voice Studio), nạp VoxCPM2-fp16 một lần, mã hoá ref.wav một lần
# (prompt cache) cho cả bài, inference_mode + cudnn.benchmark. Không đụng daemon dùng chung của DG Voice Studio.
_MA_GPU = r'''
import sys, json, time, os, wave
import numpy as np
import torch
torch.backends.cudnn.benchmark = True
from voxcpm import VoxCPM
yc = json.loads(sys.argv[1])
t0 = time.time()
m = VoxCPM.from_pretrained(yc["model"], load_denoiser=False, optimize=bool(yc.get("optimize")), device="cuda",
                           cache_dir=yc.get("cache_dir"), local_files_only=True)
tts = m.tts_model
sr = int(tts.sample_rate)
bo_dem = {}
print(json.dumps({"san_sang": True, "sr": sr, "giay_nap": round(time.time() - t0, 1)}), flush=True)
for dong in sys.stdin:
    try:
        r = json.loads(dong)
        ref = r["ref"]
        k = (ref, os.path.getmtime(ref))
        if k not in bo_dem:
            bo_dem[k] = tts.build_prompt_cache(prompt_text=None, prompt_wav_path=None, reference_wav_path=ref)
        chu = " ".join(r["chu"].split())
        try:
            so_token = len(tts.text_tokenizer(chu))
        except Exception:
            so_token = max(1, len(chu) // 3)
        t1 = time.time()
        with torch.inference_mode():
            wav, _a, _b = tts.generate_with_prompt_cache(
                target_text=chu, prompt_cache=bo_dem[k], min_len=2,
                max_len=max(8, min(int(so_token * 6.0 + 10), 512)), inference_timesteps=int(r.get("buoc", 16)),
                cfg_value=float(r.get("cfg", 2.0)), retry_badcase=bool(r.get("thu_lai", True)),
                retry_badcase_max_times=3, retry_badcase_ratio_threshold=6.0)
        x = wav.squeeze(0).float().cpu().numpy()
        with wave.open(r["ra"], "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
        print(json.dumps({"ok": True, "giay_sinh": round(time.time() - t1, 2), "giay": round(len(x) / sr, 2)}), flush=True)
    except Exception as e:
        print(json.dumps({"ok": False, "loi": repr(e)[:500]}), flush=True)
'''


class MayDocGPURieng(MayDocVoxCPM):
    """Chuẩn cao (GPU) nhanh: tiến trình VoxCPM2 riêng của Đọc Voice, nói chuyện qua stdin/stdout (không cổng),
    giữ nóng suốt phiên app. Đọc cấu hình model và python từ DG Voice Studio, không sửa daemon của Studio."""

    ten = "Chuẩn cao (GPU)"

    def __init__(self, thu_muc_studio=r"E:\DG Voice Studio", buoc=None, optimize=False):
        super().__init__(thu_muc_studio)
        self.buoc = buoc
        self.optimize = optimize
        self.tt = None
        self.sr = None
        self._khoa = threading.Lock()

    def _duong_model(self):
        goc = self._cfg().get("dg_root") or r"E:\DG Media Holding AI Mascot Building Map"
        return (os.path.join(goc, "voice-bank", "local-models", "VoxCPM2-fp16"),
                os.path.join(goc, "voice-bank", "local-cache"))

    def co_san(self):
        return os.path.isdir(self._duong_model()[0])

    def trang_thai(self):
        return {"loaded": self.tt is not None and self.tt.poll() is None, "app": "doc-voice-gpu"}

    def bat(self, cho_toi_da=900, bao=None):
        with self._khoa:
            if self.tt is not None and self.tt.poll() is None:
                return self.trang_thai()
            py = self._cfg().get("voxcpm_python") or r"E:\MONEY X3\telegram-launch-bot\.venv-voxcpm\Scripts\python.exe"
            if not os.path.isfile(py):
                raise LoiClone(f"Không thấy python của VoxCPM: {py}")
            model, cache = self._duong_model()
            if bao:
                bao("Đang nạp máy đọc GPU riêng (VoxCPM2, khoảng 1 đến 5 phút lần đầu)...")
            self.tt = subprocess.Popen([py, "-c", _MA_GPU, json.dumps({"model": model, "cache_dir": cache,
                                                                       "optimize": self.optimize})],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, encoding="utf-8", creationflags=NO_WINDOW)
            dong = self.tt.stdout.readline()
            try:
                tt = json.loads(dong)
            except ValueError:
                tt = {}
            if not tt.get("san_sang"):
                self.dong()
                raise LoiClone("Máy đọc GPU riêng không lên (thiếu VRAM hoặc model).")
            self.sr = tt.get("sr")
            self.giay_nap = tt.get("giay_nap")
            return self.trang_thai()

    def sinh(self, chu, ref, ra, cfg=2.0, buoc=16, muc=None):
        self.bat()
        with self._khoa:
            self.tt.stdin.write(json.dumps({"chu": chu, "ref": ref, "ra": ra, "cfg": cfg,
                                            "buoc": self.buoc or buoc}) + "\n")
            self.tt.stdin.flush()
            dong = self.tt.stdout.readline()
        try:
            r = json.loads(dong)
        except ValueError:
            r = {"ok": False, "loi": "máy đọc GPU riêng dừng"}
        if not r.get("ok") or not os.path.isfile(ra):
            raise LoiClone(r.get("loi") or "máy đọc GPU riêng không trả âm thanh")
        return r

    def dong(self):
        if self.tt is not None:
            try:
                self.tt.stdin.close()
                self.tt.wait(timeout=10)
            except Exception:
                self.tt.kill()
            self.tt = None


# ---------------------------------------------------------------- kiểm đọc bằng whisper local
_MA_KIEM = r'''
import sys, json
from faster_whisper import WhisperModel
m = WhisperModel(sys.argv[1], device="cpu", compute_type="int8", cpu_threads=4)
print(json.dumps({"san_sang": True}), flush=True)
for dong in sys.stdin:
    try:
        yc = json.loads(dong)
        segs, _ = m.transcribe(yc["wav"], language="en", beam_size=3, condition_on_previous_text=False)
        print(json.dumps({"chu": " ".join(s.text.strip() for s in segs)}), flush=True)
    except Exception as e:
        print(json.dumps({"loi": str(e)}), flush=True)
'''


def tim_python_whisper(goi_y=""):
    ung_vien = [goi_y] if goi_y else []
    for ten in ("python", "py"):
        p = shutil.which(ten)
        if p and "WindowsApps" not in p:
            ung_vien.append(p)
    for p in ung_vien:
        if not p or not os.path.isfile(p):
            continue
        try:
            r = subprocess.run([p, "-c", "import faster_whisper"], capture_output=True, timeout=120,
                               creationflags=NO_WINDOW)
            if r.returncode == 0:
                return p
        except Exception:
            continue
    return ""


class KiemDoc:
    """Tiến trình whisper (CPU) chạy nền, nhận đường wav, trả chữ nghe được."""

    def __init__(self, python, thu_muc, model="small"):
        self.p = None
        if not python:
            return
        ma = os.path.join(thu_muc, "kiem_doc_whisper.py")
        with open(ma, "w", encoding="utf-8") as f:
            f.write(_MA_KIEM)
        self.p = subprocess.Popen([python, ma, model], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                                  creationflags=NO_WINDOW)
        dau = self.p.stdout.readline()
        if "san_sang" not in dau:
            self.dong()

    @property
    def co(self):
        return self.p is not None and self.p.poll() is None

    def nghe(self, wav):
        if not self.co:
            return None
        self.p.stdin.write(json.dumps({"wav": wav}) + "\n")
        self.p.stdin.flush()
        r = json.loads(self.p.stdout.readline() or "{}")
        return r.get("chu")

    def dong(self):
        if self.p:
            try:
                self.p.stdin.close()
                self.p.terminate()
            except Exception:
                pass
        self.p = None


# ---------------------------------------------------------------- hậu kỳ ffmpeg
def tim_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    goc = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    for thu in (os.path.join(goc, "DocVoice", "may_nhe", "ffmpeg", "ffmpeg.exe"),   # máy đọc nhẹ tự tải
                r"C:\ffmpeg\bin\ffmpeg.exe", r"E:\ffmpeg\bin\ffmpeg.exe"):
        if os.path.isfile(thu):
            return thu
    return ""


LOC_HAU_KY = "highpass=f=80,afftdn=nr=8:nf=-42,deesser=i=0.25:m=0.5:f=0.5"


def _chay_ffmpeg(ffmpeg, args, cho=3600):
    r = subprocess.run([ffmpeg, "-hide_banner", "-nostdin", "-y"] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=cho, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise LoiClone("ffmpeg lỗi: " + (r.stderr or "")[-600:])
    return r.stderr or ""


def _doc_json_loudnorm(log):
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", log, re.S)
    return json.loads(m[-1]) if m else None


def hau_ky(ffmpeg, wav_vao, mp3_ra, wav_ra="", lufs=-14.0, tp=-1.5, toc=1.0):
    """Lọc (80 Hz, khử ồn nhẹ, de-esser, atempo nếu toc khác 1) một lần ra wav float trung gian,
    rồi loudnorm 2 lượt trên bản đã lọc: lượt 1 đo, lượt 2 áp và mã hoá mp3 320 kbps (+ wav 24 bit)."""
    truoc = LOC_HAU_KY + (f",atempo={toc:.4f}" if abs(toc - 1.0) > 1e-3 else "")
    loc_wav = mp3_ra + ".loc.wav"
    _chay_ffmpeg(ffmpeg, ["-i", wav_vao, "-af", truoc, "-ac", "1", "-c:a", "pcm_f32le", loc_wav])
    try:
        log = _chay_ffmpeg(ffmpeg, ["-i", loc_wav, "-af", f"loudnorm=I={lufs}:TP={tp}:LRA=11:print_format=json",
                                    "-f", "null", "-"])
        do = _doc_json_loudnorm(log)
        if do:
            ln = (f"loudnorm=I={lufs}:TP={tp}:LRA=11:measured_I={do['input_i']}:measured_TP={do['input_tp']}:"
                  f"measured_LRA={do['input_lra']}:measured_thresh={do['input_thresh']}:"
                  f"offset={do['target_offset']}:linear=true")
        else:
            ln = f"loudnorm=I={lufs}:TP={tp}:LRA=11"
        # loudnorm đo đỉnh ở 192 kHz nhưng hạ về 48 kHz rồi mã hoá mp3 làm đỉnh vượt lại khoảng 0,7 dB:
        # hạ mẫu trước, chặn đỉnh mẫu ở -2 dBFS (lookahead 5 ms), để true peak sau mp3 vẫn ≤ -1 dBTP
        ln += ",aresample=48000,alimiter=limit=0.7943:attack=5:release=50:level=false"
        ra = ["-af", ln, "-ar", "48000", "-ac", "1", "-c:a", "libmp3lame", "-b:a", "320k", mp3_ra + ".part.mp3"]
        if wav_ra:
            ra += ["-af", ln, "-ar", "48000", "-ac", "1", "-c:a", "pcm_s24le", wav_ra + ".part.wav"]
        _chay_ffmpeg(ffmpeg, ["-i", loc_wav] + ra)
        os.replace(mp3_ra + ".part.mp3", mp3_ra)
        if wav_ra:
            os.replace(wav_ra + ".part.wav", wav_ra)
    finally:
        _xoa(loc_wav)
    return do


def do_am_luong(ffmpeg, duong):
    """Đo LUFS tích hợp và true peak của file đầu ra (ebur128 peak=true)."""
    log = _chay_ffmpeg(ffmpeg, ["-i", duong, "-af", "ebur128=peak=true", "-f", "null", "-"])
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", log)
    return {"lufs": float(i[-1]) if i else None, "true_peak": float(p[-1]) if p else None}


# ---------------------------------------------------------------- điều phối đọc dài
def ma_viec(slug, muc, van_ban, may="voxcpm"):
    # tốc độ (toc) chỉ áp ở hậu kỳ nên không vào mã việc: đổi tốc độ không phải đọc lại
    cx = {k: v for k, v in (MUC_CAM_XUC.get(muc) or {}).items() if k != "toc"}
    goc = f"{slug}|{muc}|{json.dumps(cx, sort_keys=True)}|{van_ban}"
    if may != "voxcpm":                 # giữ mã cũ cho VoxCPM để thư mục tạm cũ vẫn chạy tiếp được
        goc = may + "|" + goc
    h = hashlib.sha1(goc
                     .encode("utf-8")).hexdigest()
    return h[:16]


class DocDaiClone:
    """Đọc văn bản dài bất kỳ bằng giọng clone. Mỗi phần lưu ngay vào thư mục tạm của việc,
    chạy lại cùng văn bản và cùng mức thì bỏ qua phần đã có (chạy tiếp khi dừng giữa chừng)."""

    def __init__(self, thu_muc_tam, may_doc=None, kiem=None, ffmpeg="", bao=None, dung=None,
                 nguong_wer=0.2, so_lan_sinh=3, nguong_giong=None):
        self.thu_muc_tam = os.path.abspath(thu_muc_tam)
        self.may = may_doc or MayDocVoxCPM()
        self.kiem = kiem
        self.ffmpeg = ffmpeg or tim_ffmpeg()
        self.bao = bao or (lambda chu, pt: None)
        self.dung = dung            # threading.Event: đặt thì dừng sau phần đang sinh
        self.nguong_wer = nguong_wer
        self.so_lan_sinh = so_lan_sinh
        # ngưỡng giống giọng từng câu theo thang của máy đọc (máy Nhẹ: cosine dấu giọng OpenVoice)
        self.nguong_giong = nguong_giong if nguong_giong is not None else getattr(self.may, "nguong_giong", 0.85)

    def _sinh_mot(self, chu_doc, ref, ra, muc, sac=None, kiem_ngay=True):
        """sac: sắc thái của câu ở mức Tự động. Máy đọc có do_giong thì đo giống giọng từng câu,
        dưới nguong_giong thì sinh lại bằng sắc thái nhẹ (SAC_NHE), giọng mẫu không đổi."""
        do_giong = getattr(self.may, "do_giong", None) if sac else None
        tot = None
        so_tu = max(1, len(chu_doc.split()))
        for lan in range(self.so_lan_sinh):
            cx = SAC_THAI[sac] if sac else MUC_CAM_XUC.get(muc, MUC_CAM_XUC[MUC_MAC_DINH])
            chu_gui = (cx["tien_to"] + chu_doc) if (cx["tien_to"] and getattr(self.may, "nhan_tien_to", True)) else chu_doc
            tam = ra + f".lan{lan}.wav"
            t0 = time.time()
            self.may.sinh(chu_gui, ref, tam, cfg=cx["cfg"], muc=sac or muc)
            giay_sinh = time.time() - t0
            x, sr = doc_wav(tam)
            dai = len(cat_lang(x, sr)) / sr
            giay_moi_tu = dai / so_tu
            nghe = self.kiem.nghe(tam) if (kiem_ngay and self.kiem and self.kiem.co) else None
            loi = wer(chu_cho_wer(chu_doc), chu_cho_wer(nghe)) if nghe is not None else None
            hop_ly = 0.17 <= giay_moi_tu <= 0.75 or so_tu < 3
            giong = None
            if do_giong is not None:
                try:
                    giong = round(float(do_giong(tam, ref)), 3)
                except Exception:
                    giong = None
            lech_giong = giong is not None and giong < self.nguong_giong
            diem = (loi if loi is not None else 0.0) + (0 if hop_ly else 1.0) + (0.5 if lech_giong else 0.0)
            ket = {"lan": lan + 1, "wer": loi, "nghe": nghe, "giay": round(dai, 2),
                   "giay_sinh": round(giay_sinh, 1), "hop_ly": hop_ly, "tam": tam, "diem": diem,
                   **({"sac": sac, "giong": giong} if sac else {})}
            if tot is None or diem < tot["diem"]:
                if tot:
                    _xoa(tot["tam"])
                tot = ket
            else:
                _xoa(tam)
            if hop_ly and (loi is None or loi <= self.nguong_wer) and not lech_giong:
                break
            if lech_giong and sac != SAC_NHE:
                sac = SAC_NHE                 # sắc thái làm lệch giọng: lần sau đọc nhẹ hơn
        os.replace(tot["tam"], ra)
        tot.pop("tam")
        return tot

    def chay(self, van_ban, slug="arthur", muc=MUC_MAC_DINH, mp3_ra="", wav_ra="", srt_ra="",
             kieu_shorts=False, chia_shorts=None, ap_cach_doc=None, toc=None, bat_dau=0, phat=None):
        """ap_cach_doc: hàm áp từ điển cách đọc của người dùng lên chữ trước khi chuẩn hoá.
        bat_dau: sinh từ phần này trước (bấm câu nào nghe từ câu đó), rồi vòng lại các phần trước.
        phat(su_kien, **kw): báo cho bộ phát luồng: "phan" (danh sách phần, thư mục việc, tập phần
        đã có) một lần, rồi "xong" (i) mỗi phần mới. Đổi self.uu_tien trong lúc chạy để nhảy câu."""
        if not self.ffmpeg:
            raise LoiClone("Không thấy ffmpeg trên máy (cần để ghép và chuẩn âm lượng).")
        ref = duong_mau(slug)
        if not os.path.isfile(ref):
            raise LoiClone(f"Thiếu mẫu giọng: {ref}")
        if la_dau_giong(ref) and isinstance(self.may, MayDocVoxCPM):
            raise LoiClone("Giọng này chỉ có ở chế độ Nhẹ (chỉ có dấu giọng, không có âm thanh mẫu cho chế độ Chuẩn cao).")
        cx = MUC_CAM_XUC.get(muc, MUC_CAM_XUC[MUC_MAC_DINH])
        phan = chia_theo_may(van_ban, self.may, muc)
        if not phan:
            raise LoiClone("Văn bản không có chữ nào để đọc.")
        ten_may = "voxcpm" if isinstance(self.may, MayDocVoxCPM) else type(self.may).__name__.lower()
        viec = os.path.join(self.thu_muc_tam, ma_viec(slug, muc, van_ban, ten_may))
        os.makedirs(viec, exist_ok=True)
        file_so = os.path.join(viec, "so_viec.json")
        so = _doc(file_so, {"phan": {}})
        so.update({"slug": slug, "muc": muc, "tong": len(phan)})
        def da_co(i):
            cu = so["phan"].get(f"{i:05d}")
            return bool(cu and cu.get("chu") == phan[i]["chu"] and cu.get("sac_goc") == phan[i].get("sac")
                        and os.path.isfile(os.path.join(viec, f"phan_{i:05d}.wav")))
        san = {i for i in range(len(phan)) if da_co(i)}   # phần đã đọc ở lượt trước: chạy tiếp, không đọc lại
        self.phan, self.viec, self.san, self.so = phan, viec, san, so
        self.uu_tien = max(0, min(len(phan) - 1, int(bat_dau or 0)))
        if phat:
            phat("phan", phan=phan, viec=viec, san=san)
        self.may.bat(bao=lambda c: self.bao(c, 0))

        def chu_doc_cua(i):
            return chuan_hoa_tieng_anh(ap_cach_doc(phan[i]["chu"]) if ap_cach_doc else phan[i]["chu"])

        def tiep_theo():
            """Phần chưa có gần nhất tính từ uu_tien, vòng lại đầu bài nếu đã hết phía sau."""
            u = self.uu_tien
            for i in list(range(u, len(phan))) + list(range(0, u)):
                if i not in san:
                    return i
            return None
        t_dau = time.time()
        moi = 0
        # Kiểm whisper song song (máy GPU): GPU sinh câu sau trong lúc CPU nghe lại câu trước;
        # câu lệch thì sinh lại, giữ bản có WER thấp hơn.
        song_song = bool(self.kiem is not None and self.kiem.co and getattr(self.may, "kiem_song_song", False))
        cho_kiem, ex = {}, None
        if song_song:
            from concurrent.futures import ThreadPoolExecutor
            ex = ThreadPoolExecutor(max_workers=1)

        def thu_kiem(cho=False):
            for j, (fut, cd) in list(cho_kiem.items()):
                if not (cho or fut.done()):
                    continue
                del cho_kiem[j]
                try:
                    nghe = fut.result()
                except Exception:
                    nghe = None
                k = f"{j:05d}"
                rec = so["phan"][k]
                w = os.path.join(viec, f"phan_{k}.wav")
                loi = wer(chu_cho_wer(cd), chu_cho_wer(nghe)) if nghe is not None else None
                rec.update({"wer": loi, "nghe": nghe})
                tot = rec.pop("tot", None)
                if tot is not None and os.path.isfile(w + ".tot.wav"):
                    if loi is None or loi <= tot["wer"]:
                        _xoa(w + ".tot.wav")
                    else:                                  # bản sinh lại kém hơn: trả bản cũ
                        os.replace(w + ".tot.wav", w)
                        rec.update(tot["rec"])
                        loi = tot["wer"]
                if loi is not None and loi > self.nguong_wer and rec.get("lan_kiem", 1) < self.so_lan_sinh:
                    os.replace(w, w + ".tot.wav")
                    rec["tot"] = {"wer": loi, "rec": {kk: vv for kk, vv in rec.items() if kk != "tot"}}
                    rec["lan_kiem"] = rec.get("lan_kiem", 1) + 1
                    san.discard(j)
                _ghi(file_so, so)
        try:
            while True:
                if song_song:
                    thu_kiem()
                i = tiep_theo()
                if i is None:
                    if cho_kiem:
                        thu_kiem(cho=True)
                        continue
                    break
                moi += self._mot_vong(i, phan, viec, so, san, file_so, ref, muc, chu_doc_cua, phat,
                                      song_song, cho_kiem, ex)
                if moi < 0:
                    return {"dung": True, "xong": len(san), "tong": len(phan), "thu_muc": viec}
                con = len(phan) - len(san)
                toc_tb = (time.time() - t_dau) / max(1, moi)
                self.bao(f"Giọng clone: xong {len(san)}/{len(phan)} phần, còn khoảng "
                         f"{int(con * toc_tb / 60)} phút", len(san) / len(phan) * 95)
        finally:
            if ex is not None:
                ex.shutdown(wait=False)
        return self.xuat(phan, viec, so, mp3_ra, wav_ra, srt_ra, kieu_shorts, chia_shorts,
                         toc if toc is not None else cx.get("toc", 1.0))

    def _mot_vong(self, i, phan, viec, so, san, file_so, ref, muc, chu_doc_cua, phat, song_song, cho_kiem, ex):
        """Sinh một phần. Trả 1 nếu sinh xong, -10**9 nếu người dùng bấm dừng."""
        if True:
            p = phan[i]
            khoa = f"{i:05d}"
            wav = os.path.join(viec, f"phan_{khoa}.wav")
            if hasattr(self.may, "chuan_bi"):    # máy nhẹ: tải trước vài câu Edge kế tiếp trong lúc đổi chất giọng
                sau, j = [], i + 1
                while len(sau) < 6 and j < len(phan):
                    if j not in san:
                        sau.append((chu_doc_cua(j), muc))
                    j += 1
                self.may.chuan_bi(sau)
            if self.dung is not None and self.dung.is_set():
                _ghi(file_so, so)
                return -10 ** 9
            chu_doc = chu_doc_cua(i)
            cu = so["phan"].get(khoa) or {}
            kq = self._sinh_mot(chu_doc, ref, wav, muc, p.get("sac"), kiem_ngay=not song_song)
            kq.update({"chu": p["chu"], "doc": chu_doc, "ngat": p["ngat"], "sac_goc": p.get("sac")})
            for kk in ("lan_kiem", "tot"):
                if kk in cu:
                    kq[kk] = cu[kk]
            so["phan"][khoa] = kq
            _ghi(file_so, so)
            san.add(i)
            if song_song:
                cho_kiem[i] = (ex.submit(self.kiem.nghe, wav), chu_doc)
            if self.uu_tien == i:
                self.uu_tien = i + 1 if i + 1 < len(phan) else 0
            if phat:
                phat("xong", i=i)
            return 1

    def xuat(self, phan, viec, so, mp3_ra, wav_ra, srt_ra, kieu_shorts, chia_shorts, toc=1.0):
        self.bao("Đang ghép và chuẩn âm lượng...", 96)
        cac, sr = [], None
        for i, p in enumerate(phan):
            x, sr_i = doc_wav(os.path.join(viec, f"phan_{i:05d}.wav"))
            if sr is None:
                sr = sr_i
            elif sr_i != sr:
                raise LoiClone("các phần khác tần số lấy mẫu")
            cac.append((cat_lang(x, sr), lang_sau(p["ngat"])))
        am, moc = ghep_phan(cac, sr)
        dinh = float(np.max(np.abs(am))) if len(am) else 0.0
        if dinh > 0.89:                      # chừa -1 dBFS trước hậu kỳ, tránh cắt đỉnh khi ghi 16 bit
            am = am * (0.89 / dinh)
        tho = os.path.join(viec, "ghep_tho.wav")
        ghi_wav(tho, am, sr)
        do_vao = hau_ky(self.ffmpeg, tho, mp3_ra, wav_ra, toc=toc)
        muc_tg = [(bd / toc, kt / toc, p["chu"]) for (bd, kt), p in zip(moc, phan)]
        _ghi(os.path.join(viec, "moc.json"), {"toc": toc, "muc_tg": muc_tg,
                                              "ngat": [p["ngat"] for p in phan], "mp3": mp3_ra})
        if srt_ra:
            with open(srt_ra, "w", encoding="utf-8") as f:
                f.write(tao_srt_clone(muc_tg, kieu_shorts, chia_shorts))
        wers = [v["wer"] for v in so["phan"].values() if v.get("wer") is not None]
        kq = {"dung": False, "tong": len(phan), "thu_muc": viec, "mp3": mp3_ra, "srt": srt_ra,
              "giay": len(am) / sr / toc, "muc_tg": muc_tg, "loudnorm_vao": do_vao,
              "wer_tb_tung_phan": (sum(wers) / len(wers)) if wers else None,
              "sinh_lai": sum(1 for v in so["phan"].values() if v.get("lan", 1) > 1)}
        self.bao(f"Xong: {mp3_ra}", 100)
        return kq


def _xoa(p):
    try:
        os.remove(p)
    except OSError:
        pass


def _doc(p, mac_dinh):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return mac_dinh


def _ghi(p, du_lieu):
    tam = p + ".tmp"
    with open(tam, "w", encoding="utf-8") as f:
        json.dump(du_lieu, f, ensure_ascii=False, indent=1)
    os.replace(tam, p)


# ---------------------------------------------------------------- chạy không giao diện
def sua_console_utf8(*luong):
    """Console Windows mặc định cp1252 làm print chữ Việt chết (UnicodeEncodeError): đổi sang utf-8,
    ký tự không in được thì thay thế, không bao giờ làm dừng việc đọc."""
    for st in (luong or (sys.stdout, sys.stderr)):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None):
    """python giong_clone.py <văn bản.txt> <ra.mp3> [--muc "Kể chuyện"] [--tam thư_mục] [--wav]
    [--khong-kiem] [--nhat-ky log.txt]. Chạy lại cùng lệnh thì đọc tiếp phần còn thiếu."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("van_ban")
    ap.add_argument("mp3")
    ap.add_argument("--muc", default=MUC_MAC_DINH, choices=list(MUC_CAM_XUC))
    ap.add_argument("--slug", default="arthur")
    ap.add_argument("--tam", default=os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "clone_tam"))
    ap.add_argument("--wav", action="store_true")
    ap.add_argument("--khong-kiem", action="store_true")
    ap.add_argument("--python-kiem", default="")
    ap.add_argument("--nhat-ky", default="")
    ap.add_argument("--studio", default=r"E:\DG Voice Studio")
    ap.add_argument("--may", default="gpu", choices=["gpu", "nhe"], help="gpu = VoxCPM2 (DG Voice Studio), nhe = Edge + OpenVoice CPU")
    a = ap.parse_args(argv)
    sua_console_utf8()
    with open(a.van_ban, encoding="utf-8") as f:
        van_ban = f.read()
    os.makedirs(a.tam, exist_ok=True)
    nk = open(a.nhat_ky, "a", encoding="utf-8") if a.nhat_ky else None

    def bao(chu, pt):
        dong = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {pt:5.1f}% {chu}"
        print(dong, flush=True)
        if nk:
            nk.write(dong + "\n")
            nk.flush()
    kiem = None
    if not a.khong_kiem:
        py = tim_python_whisper(a.python_kiem or (sys.executable if not getattr(sys, "frozen", False) else ""))
        kiem = KiemDoc(py, a.tam) if py else None
        bao(f"kiem doc whisper: {'co' if kiem and kiem.co else 'khong'} ({py})", 0)
    t0 = time.time()
    try:
        if a.may == "nhe":
            import may_nhe
            if not may_nhe.da_cai():
                may_nhe.cai_dat(bao=lambda v, d, t: bao(f"{v} {d / 1e6:.0f}/{t / 1e6:.0f} MB", 0))
            may = may_nhe.MayDocNhe()
        else:
            may = MayDocVoxCPM(a.studio)
        doc = DocDaiClone(a.tam, may, kiem, bao=bao)
        kq = doc.chay(van_ban, a.slug, a.muc, a.mp3, os.path.splitext(a.mp3)[0] + ".wav" if a.wav else "",
                      os.path.splitext(a.mp3)[0] + ".srt")
    finally:
        if kiem:
            kiem.dong()
    kq.pop("muc_tg", None)
    kq["giay_chay"] = round(time.time() - t0, 1)
    bao("KET QUA " + json.dumps(kq, ensure_ascii=False), 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
