# -*- coding: utf-8 -*-
"""Kiểm kịch bản TRƯỚC khi đọc: tìm những chỗ dễ làm bản đọc không đạt và gợi ý cách sửa.

Chạy thuần chữ, không cần máy đọc, không gọi mạng, không dùng khoá nào. Mỗi điểm báo:
  {"muc": "sua" | "luu_y", "loai": ..., "dong": số dòng (từ 1), "cot": vị trí ký tự trong dòng,
   "bd": vị trí bắt đầu trong cả văn bản, "kt": vị trí kết thúc, "trich": đoạn chữ, "goi_y": cách sửa}
Giọng Arthur đọc tiếng Anh. Dùng cùng bộ chuẩn hoá của giong_clone (số, năm, %, đơn vị) nên chỉ báo những gì
bộ đó chưa xử lý được hoặc dễ đọc sai.
"""
import re

CAU_DAI_TU = 40          # câu dài hơn ngần này từ: dễ vấp, dễ mất hơi
CAU_DAI_KY_TU = 240
DOAN_DAI_TU = 160        # đoạn dài hơn ngần này từ: nên xuống dòng cho có nhịp
DAY_SO_DAI = 8           # dãy chữ số liền từ ngần này: số điện thoại, mã, không đọc tự nhiên

_BIEU_TUONG = re.compile("[←-⇿⌀-⏿①-➿⤀-⥿⬀-⯿\U0001f000-\U0001faff️]")
_KY_HIEU_LA = re.compile(r"[\x5c|~^*#_\x60{}<>@=]")
_DAU_GACH_DAI = re.compile("[–—]")
_TIENG_VIET = re.compile("[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.I)
_URL = re.compile(r"(https?://\S+|www\.\S+|\b[\w.-]+@[\w-]+\.[\w.]+|\b[\w-]+\.(?:com|net|org|io|vn|co)\b)", re.I)
_NGAY_SO = re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b")
_GIO = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[ap]\.?m\.?)?", re.I)
_DAY_SO = re.compile(r"(?<![\d,.])\d{%d,}(?![\d,.]\d)" % DAY_SO_DAI)
_SO_DAI_PHAY = re.compile(r"\b\d{1,3}(?:,\d{3}){4,}\b")
_VIET_HOA = re.compile(r"\b[A-Z]{2,6}\b")
_VIET_TAT_DA_BIET = {"US", "USA", "UK", "UN", "FBI", "CIA", "NASA", "CEO", "AI", "API", "CPU", "GPU", "USB", "PDF", "GPS",
                     "WHO", "TV", "DNA", "BC", "AD", "AM", "PM", "OK", "IT", "PC", "HD", "VR", "AR", "SEO", "UFO", "EU",
                     "NATO", "MIT", "REM", "OLED", "SSD", "RAM", "HR", "PR", "ID", "HTML", "CSS", "II", "III", "IV", "VI",
                     "VII", "VIII", "IX", "XI", "XII", "ATM", "GDP", "LGBT", "ER", "ICU", "COVID", "SUV", "DIY", "WWII"}
_LAP_TU = re.compile(r"\b([A-Za-z]{2,})\s+\1\b", re.I)
_DAU_LAP = re.compile(r"([!?.,;:])\1{2,}")
_CHU_SO_DINH = re.compile(r"\b\d+[A-Za-z]+\d*[A-Za-z0-9]*\b|\b[A-Za-z]+\d+[A-Za-z0-9]*\b")
_KHONG_CHU_CAI = re.compile(r"^[\W\d_]+\Z")
_DON_VI_OK = re.compile(r"\d+(?:st|nd|rd|th|s|km|kg|cm|mm|mph|ft|lbs?|gb|mb|kb|hz|khz|mhz|kw|kwh|am|pm|px|k|m|x)", re.I)
_DAU_KET = ".!?:;,\"\x27)"


def _vi_tri(van_ban):
    """Vị trí đầu mỗi dòng, để đổi vị trí ký tự thành (dòng, cột)."""
    dau_dong = [0]
    for m in re.finditer(r"\n", van_ban):
        dau_dong.append(m.end())
    return dau_dong


def _dong_cot(dau_dong, vt):
    lo, hi = 0, len(dau_dong) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if dau_dong[mid] <= vt:
            lo = mid
        else:
            hi = mid - 1
    return lo + 1, vt - dau_dong[lo]


def _them(ds, dau_dong, muc, loai, bd, kt, van_ban, goi_y):
    d, c = _dong_cot(dau_dong, bd)
    trich = van_ban[bd:kt].replace("\n", " ")
    ds.append({"muc": muc, "loai": loai, "dong": d, "cot": c, "bd": bd, "kt": kt,
               "trich": trich if len(trich) <= 70 else trich[:67] + "...", "goi_y": goi_y})


_HET_CAU = re.compile(r"(?<!\bMr)(?<!\bMrs)(?<!\bMs)(?<!\bDr)(?<!\bSt)(?<!\bJr)(?<!\bSr)(?<!\bProf)(?<!\bvs)(?<!\bNo)"
                      r"(?<!\be\.g)(?<!\bi\.e)(?<!\betc)(?<!\bU\.S)(?<!\bU\.K)(?<!\bapprox)(?<!\b[A-Z])"
                      r"(?<=[.!?…])[\"')\]]*\s+")


def _cat_cau(doan):
    """(bd, kt) của từng câu trong một đoạn: tách ở . ! ? và ba chấm, không tách sau Mr. Dr. e.g. U.S."""
    ra, dau = [], 0
    for m in _HET_CAU.finditer(doan):
        ra.append((dau, m.start()))
        dau = m.end()
    ra.append((dau, len(doan)))
    return [(a, b) for a, b in ra if b > a and re.search(r"\w", doan[a:b])]


def kiem(van_ban):
    """Trả danh sách điểm cần chú ý, xếp theo vị trí. Văn bản rỗng thì trả danh sách rỗng."""
    ds = []
    van_ban = van_ban or ""
    if not van_ban.strip():
        return ds
    dd = _vi_tri(van_ban)

    for m in re.finditer(r"[^\n]+", van_ban):          # 1. chữ không phải tiếng Anh
        dong = m.group(0)
        tu_viet = list(_TIENG_VIET.finditer(dong))
        if len(tu_viet) >= 3:
            _them(ds, dd, "sua", "khong_phai_tieng_anh", m.start() + tu_viet[0].start(), m.end(), van_ban,
                  "Dòng có chữ tiếng Việt: giọng Arthur chỉ đọc tiếng Anh, tiếng Việt sẽ đọc sai. Dịch sang tiếng Anh, "
                  "hoặc dùng giọng Edge tiếng Việt.")
    for m in _BIEU_TUONG.finditer(van_ban):            # 2. biểu tượng, emoji
        _them(ds, dd, "sua", "bieu_tuong", m.start(), m.end(), van_ban,
              "Biểu tượng, emoji, mũi tên không đọc được thành tiếng. Xoá, hoặc viết thành chữ.")
    for m in _KY_HIEU_LA.finditer(van_ban):            # 3. ký hiệu lạ
        _them(ds, dd, "luu_y", "ky_hieu_la", m.start(), m.end(), van_ban,
              f"Ký hiệu {m.group(0)} sẽ bị bỏ hoặc đọc lạ. Bỏ đi, hoặc viết thành chữ (ví dụ @ thành at, = thành equals).")
    for m in _DAY_SO.finditer(van_ban):                # 4. dãy số dài, số quá lớn
        _them(ds, dd, "sua", "day_so_dai", m.start(), m.end(), van_ban,
              "Dãy số dài (số điện thoại, mã, số thẻ) đọc không tự nhiên. Viết thành chữ từng nhóm, ví dụ "
              "five five five, one two three four, hoặc bỏ khỏi lời đọc.")
    for m in _SO_DAI_PHAY.finditer(van_ban):
        _them(ds, dd, "luu_y", "so_rat_lon", m.start(), m.end(), van_ban,
              "Số rất lớn: nên viết tròn bằng chữ, ví dụ 4 trillion hoặc about 12 billion.")
    for m in _NGAY_SO.finditer(van_ban):               # 5. ngày kiểu số, giờ
        _them(ds, dd, "sua", "ngay_dang_so", m.start(), m.end(), van_ban,
              "Ngày viết bằng số (03/04/2024) dễ đọc thành three slash four... hoặc nhầm thứ tự ngày tháng. "
              "Viết April 3, 2024 (tháng bằng chữ).")
    for m in _GIO.finditer(van_ban):
        _them(ds, dd, "luu_y", "gio", m.start(), m.end(), van_ban,
              "Giờ ghi bằng số (8:30): nên viết eight thirty hoặc half past eight cho chắc.")
    for m in _URL.finditer(van_ban):                   # 6. đường dẫn, email
        _them(ds, dd, "sua", "duong_dan", m.start(), m.end(), van_ban,
              "Đường dẫn hay email đọc từng ký tự rất dài. Bỏ khỏi lời đọc (đưa vào phần mô tả video), hoặc viết "
              "our website, dot com.")
    for m in _CHU_SO_DINH.finditer(van_ban):           # 7. mã lẫn chữ và số
        t = m.group(0)
        if _DON_VI_OK.fullmatch(t):
            continue
        _them(ds, dd, "luu_y", "ma_chu_so", m.start(), m.end(), van_ban,
              f"{t} lẫn chữ và số: máy có thể đọc sai. Viết rõ cách đọc, ví dụ X two cho X2.")
    thay = set()                                       # 8. viết tắt in hoa lạ
    for m in _VIET_HOA.finditer(van_ban):
        t = m.group(0)
        if t in _VIET_TAT_DA_BIET or t in thay:
            continue
        thay.add(t)
        _them(ds, dd, "luu_y", "viet_tat_la", m.start(), m.end(), van_ban,
              f"Chữ in hoa {t}: máy có thể đánh vần từng chữ hoặc đọc như một từ. Nghe thử; nếu sai thì thêm vào "
              "Từ điển cách đọc (ví dụ ABC = ay bee see).")
    for m in _DAU_LAP.finditer(van_ban):               # 9. dấu lặp, từ lặp
        _them(ds, dd, "luu_y", "dau_lap", m.start(), m.end(), van_ban,
              "Dấu câu lặp (!!! hoặc ???) dễ làm máy ngắt lạ. Chỉ giữ một dấu.")
    for m in _LAP_TU.finditer(van_ban):
        _them(ds, dd, "luu_y", "tu_lap", m.start(), m.end(), van_ban,
              f"Từ {m.group(1)} lặp liền nhau: có thể là lỗi gõ và sẽ bị báo là đọc lặp. Bỏ một từ nếu không chủ ý.")
    for m in re.finditer(r"[^\n]+", van_ban):          # 10. câu quá dài, đoạn quá dài
        dong = m.group(0)
        if not re.search(r"[A-Za-z]", dong):
            continue
        n_dong = len(dong.split())
        if n_dong > DOAN_DAI_TU:
            _them(ds, dd, "luu_y", "doan_dai", m.start(), min(m.end(), m.start() + 80), van_ban,
                  f"Đoạn dài {n_dong} từ không xuống dòng: nên chia thành đoạn 3 đến 6 câu để giọng đọc có nhịp nghỉ.")
        for a, b in _cat_cau(dong):
            cau = dong[a:b]
            n = len(cau.split())
            if n > CAU_DAI_TU or len(cau) > CAU_DAI_KY_TU:
                _them(ds, dd, "sua", "cau_dai", m.start() + a, m.start() + b, van_ban,
                      f"Câu dài {n} từ: dễ vấp và mất hơi. Tách thành 2 hoặc 3 câu ngắn (ở chỗ có dấu phẩy, and, but, which).")
    for m in re.finditer(r"[^\n]+", van_ban):          # 11. dòng ngắn không có dấu kết (tiêu đề)
        dong = m.group(0).strip()
        if dong and re.search(r"[A-Za-z]", dong) and len(dong.split()) <= 8 and dong[-1] not in _DAU_KET and \
                not _KHONG_CHU_CAI.match(dong):
            _them(ds, dd, "luu_y", "thieu_dau_ket", m.start(), m.end(), van_ban,
                  "Dòng ngắn không có dấu chấm cuối (tiêu đề?): thêm dấu chấm để máy dừng đúng chỗ và không dính sang câu sau.")
    ds.sort(key=lambda e: (e["bd"], e["loai"]))
    return ds


def tom_tat(ds):
    """Số điểm theo mức, dùng cho dòng báo ngắn."""
    sua = sum(1 for e in ds if e["muc"] == "sua")
    return {"sua": sua, "luu_y": len(ds) - sua, "tong": len(ds)}


def tu_sua_an_toan(van_ban):
    """Chỉ sửa những gì chắc chắn không đổi nghĩa: xoá emoji và biểu tượng, gộp dấu câu lặp, bỏ từ lặp liền nhau do gõ
    nhầm, gộp khoảng trắng, đổi dấu gạch dài thành dấu phẩy. Trả (văn bản mới, có đổi hay không)."""
    cu = van_ban or ""
    s = _BIEU_TUONG.sub("", cu)
    s = _DAU_LAP.sub(lambda m: m.group(1), s)
    s = _LAP_TU.sub(lambda m: m.group(1), s)
    s = _DAU_GACH_DAI.sub(",", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r" +([,.!?;:])", r"\1", s)
    return s, s != cu
