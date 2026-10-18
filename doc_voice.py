"""Đọc Voice: app đọc văn bản bằng giọng Edge (Microsoft Neural) và giọng clone local, chạy trên máy.

Giọng clone (ví dụ Arthur) sinh bằng máy đọc VoxCPM2 của DG Voice Studio trên máy này, đọc dài
không giới hạn, có mức cảm xúc, chạy tiếp được khi dừng giữa chừng (xem giong_clone.py).

Giao diện 2 bảng song song: bảng bên trái chứa văn bản đọc (tiếng Anh hoặc gốc),
bảng bên phải chứa bản dịch tiếng Việt để theo dõi nội dung.
Highlight nền vàng nhảy theo từng câu đọc theo thời gian thực (karaoke highlight).
Văn bản dài được chia đoạn theo câu rồi ghép lại, kèm phụ đề .srt khớp thời lượng.
Bản exe tự kiểm bản mới trên GitHub Releases và tự cập nhật an toàn.
"""
import asyncio
import ctypes
import html
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import zipfile
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import edge_tts
import numpy as np

import giong_clone as gc
import may_nhe as mn
import phat_luong as pl


def thu_muc_documents():
    buf = ctypes.create_unicode_buffer(260)
    if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
        return buf.value
    return os.path.join(os.path.expanduser("~"), "Documents")


if getattr(sys, "frozen", False):  # bản exe gửi người khác: dữ liệu theo từng người dùng
    appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
    GOC = os.path.join(appdata, "DocVoice")
    # Di chuyển dữ liệu cũ từ DocGiong nếu có
    goc_cu = os.path.join(appdata, "DocGiong")
    if not os.path.exists(GOC) and os.path.exists(goc_cu):
        try:
            shutil.copytree(goc_cu, GOC)
        except Exception:
            pass
    THU_MUC_RA = os.path.join(thu_muc_documents(), "Doc Voice")
else:
    GOC = os.path.dirname(os.path.abspath(__file__))
    THU_MUC_RA = os.path.join(GOC, "da_chuyen")
os.makedirs(GOC, exist_ok=True)
FILE_GIONG = os.path.join(GOC, "giong.json")
FILE_CAI_DAT = os.path.join(GOC, "cai_dat.json")
DO_DAI_DOAN = 2500  # ký tự mỗi lượt gửi máy chủ
SO_LAN_THU = 4
SONG_SONG = 3  # số đoạn gửi máy chủ cùng lúc
PHIEN_BAN = "1.5.0"
KHO_GITHUB = "ducdg88/doc-voice"
TEN_EXE = "DocVoice.exe"
LINK_TAI_HOP_LE = f"https://github.com/{KHO_GITHUB}/releases/download/"
FILE_TU_DIEN = os.path.join(GOC, "tu_dien.txt")
FILE_BO_DEM_DICH = os.path.join(GOC, "bo_dem_dich.json")
BYTE_MOI_GIAY = 6000  # edge-tts trả mp3 48 kbps: 6000 byte = 1 giây

TEN_NGON_NGU = {
    "vi-VN": "Tiếng Việt", "en-US": "English (US)", "en-GB": "English (UK)",
    "en-AU": "English (Australia)", "en-CA": "English (Canada)", "en-IN": "English (India)",
    "fr-FR": "Français", "de-DE": "Deutsch", "es-ES": "Español", "ja-JP": "日本語",
    "ko-KR": "한국어", "zh-CN": "中文 (简体)", "zh-TW": "中文 (繁體)", "th-TH": "ไทย",
    "pt-BR": "Português (BR)", "it-IT": "Italiano", "ru-RU": "Русский",
    "clone": "★ Giọng clone (máy này)",
}
MA_CLONE = "clone"
HUONG_DAN_NHAP_GIONG = ("Chưa có giọng clone trên máy này. Có file giọng .dgvoice của team thì bấm \"Nhập giọng (.dgvoice)\": "
                       "giọng được chọn sẵn và nhớ cho lần sau.")
THU_MUC_CLONE_TAM = os.path.join(GOC, "clone_tam")
gc.THU_MUC_GIONG = os.path.join(GOC, "giong_clone")   # giọng nhập từ gói .dgvoice
PHUT_MOI_1000_TU = {mn.MayDocNhe.ten: 4.5, gc.MayDocVoxCPM.ten: 50.0}   # đo thật
# Máy Nhẹ theo số nhân logic (đo thật 18/10/2026,
# luồng onnx = số nhân tối đa 4): phút đọc mỗi 1.000 từ. Giữa các mốc thì nội suy, trên 8 nhân dùng mốc 8.
PHUT_MOI_1000_TU_NHE_THEO_NHAN = {1: 20.0, 2: 5.2, 4: 4.1, 8: 4.5}


def phut_moi_1000_tu(may_ten, so_nhan=None):
    """Ước tính phút đọc 1.000 từ, không gọi mạng. Máy Nhẹ tính theo số nhân CPU, máy khác theo bảng cố định."""
    if may_ten != mn.MayDocNhe.ten:
        return PHUT_MOI_1000_TU.get(may_ten, 5)
    n = max(1, int(so_nhan if so_nhan is not None else (os.cpu_count() or 1)))
    bang = PHUT_MOI_1000_TU_NHE_THEO_NHAN
    if n in bang:
        return bang[n]
    moc = sorted(bang)
    if n <= moc[0]:
        return bang[moc[0]]
    if n >= moc[-1]:
        return bang[moc[-1]]
    for a, b in zip(moc, moc[1:]):
        if a <= n <= b:
            return bang[a] + (bang[b] - bang[a]) * (n - a) / (b - a)
    return PHUT_MOI_1000_TU[may_ten]


def canh_bao_may_yeu(may_ten, so_nhan=None):
    """Máy Nhẹ chỉ có 1 nhân thì sinh giọng chậm hơn tiếng phát (RTF khoảng 2): báo trước."""
    n = so_nhan if so_nhan is not None else (os.cpu_count() or 1)
    if may_ten == mn.MayDocNhe.ten and n <= 1:
        return "Máy yếu (1 nhân CPU), sẽ đọc chậm hơn nghe."
    return ""


def co_nvidia():
    return bool(shutil.which("nvidia-smi"))


def tach_chuong(kich_ban):
    """Kịch bản YouTube -> (văn bản để đọc, [(tiêu đề chương, đoạn đầu của chương)]).
    Dòng tiêu đề '# ...', '## ...' là chương, không đọc. Phần ghi chú [..], (B-roll...) lọc bằng
    loc_kich_ban_youtube như nút Lọc kịch bản YT."""
    khoi, tieu_de, than = [], None, []
    for dong in (kich_ban or "").splitlines():
        m = re.match(r"^\s*#{1,6}\s*(.+?)\s*#*\s*$", dong)
        if m:
            khoi.append((tieu_de, "\n".join(than)))
            tieu_de, than = re.sub(r"[*_`]", "", m.group(1)).strip(), []
        else:
            than.append(dong)
    khoi.append((tieu_de, "\n".join(than)))
    doc, chuong = [], []
    for td, t in khoi:
        sach, _ = loc_kich_ban_youtube(t)
        if not re.search(r"\w", sach):
            continue
        doc.append(sach)
        chuong.append((td or "Intro", sach))
    return "\n\n".join(doc), chuong


def chapters_youtube(chuong, muc_tg):
    """Mốc chương theo thời gian thật của bản đọc: chương bắt đầu ở phần đọc đầu tiên khớp đầu chương."""
    def chuan(x):
        return re.sub(r"\s+", " ", x).strip()
    ra, i = [], 0
    for td, than in chuong:
        dau = chuan(than)
        while i < len(muc_tg) and not dau.startswith(chuan(muc_tg[i][2])):
            i += 1
        if i >= len(muc_tg):
            break
        ra.append((muc_tg[i][0], td))
    if ra:
        ra[0] = (0.0, ra[0][1])          # YouTube cần chương đầu ở 00:00

    def gio(t):
        t = int(t)
        return f"{t // 3600}:{t // 60 % 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"
    return "\n".join(f"{gio(t)} {td}" for t, td in ra)


# ---------- phát âm thanh không mở cửa sổ (Windows MCI) ----------
_mci = ctypes.windll.winmm.mciSendStringW


def mci(lenh):
    _mci(lenh, None, 0, None)


def phat(duong_dan):
    mci("close docvoice")
    mci(f'open "{duong_dan}" type mpegvideo alias docvoice')
    mci("play docvoice")


def vi_tri_phat():
    """Giây đã phát thật của file đang phát qua MCI (None nếu không đọc được)."""
    buf = ctypes.create_unicode_buffer(64)
    if _mci("set docvoice time format milliseconds", None, 0, None) != 0:
        return None
    if _mci("status docvoice position", buf, 64, None) != 0:
        return None
    try:
        return int(buf.value) / 1000.0
    except ValueError:
        return None


def dung_phat():
    mci("stop docvoice")
    mci("close docvoice")


# ---------- dữ liệu ----------
def doc_json(duong_dan, mac_dinh):
    try:
        with open(duong_dan, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return mac_dinh


def ghi_json(duong_dan, du_lieu):
    with open(duong_dan, "w", encoding="utf-8") as f:
        json.dump(du_lieu, f, ensure_ascii=False, indent=1)


def ten_hien_thi(v):
    ten = v["ShortName"].split("-", 2)[2].replace("Neural", "")
    if ten.endswith("Multilingual"):
        ten = ten[: -len("Multilingual")] + " (Đa ngôn ngữ)"
    gioi = "Nam" if v.get("Gender") == "Male" else "Nữ"
    return f"{ten} · {gioi}"


def chia_doan(van_ban, toi_da=DO_DAI_DOAN):
    """Chia theo đoạn rồi theo câu, mỗi phần không quá toi_da ký tự."""
    cau = re.split(r"(?<=[.!?。！？…])\s+|\n+", van_ban)
    doan, hien_tai = [], ""
    for c in cau:
        c = c.strip()
        if not c:
            continue
        while len(c) > toi_da:  # câu quá dài: cắt ở dấu phẩy hoặc khoảng trắng
            cat = max(c.rfind(",", 0, toi_da), c.rfind(" ", 0, toi_da))
            cat = cat if cat > 0 else toi_da
            if c[: cat + 1].strip():
                doan.append(c[: cat + 1].strip())
            c = c[cat + 1:].strip()
        if len(hien_tai) + len(c) + 1 > toi_da:
            if hien_tai:
                doan.append(hien_tai)
            hien_tai = c
        else:
            hien_tai = f"{hien_tai} {c}".strip()
    if hien_tai:
        doan.append(hien_tai)
    return doan


def doc_docx(duong_dan):
    """Lấy chữ từ file Word .docx, mỗi đoạn một dòng (không cần thư viện ngoài)."""
    with zipfile.ZipFile(duong_dan) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"<mc:Fallback>.*?</mc:Fallback>", "", xml, flags=re.S)
    xml = re.sub(r"<w:txbxContent>(.*?)</w:txbxContent>", lambda m: "<w:t> " + " ".join(
        re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", m.group(1), flags=re.S)) + " </w:t>", xml, flags=re.S)
    dong = []
    for p in re.findall(r"<w:p(?:\s[^>]*)?/>|<w:p(?:\s[^>]*)?>.*?</w:p>", xml, flags=re.S):
        p = p.replace("<w:tab/>", "<w:t> </w:t>").replace("<w:br/>", "<w:t>\n</w:t>")
        dong.append(html.unescape("".join(re.findall(r"<w:t(?: [^>]*)?>(.*?)</w:t>", p, flags=re.S))))
    return "\n".join(dong)


def doc_tu_dien():
    """Mỗi dòng: từ = cách đọc. Dòng bắt đầu bằng # là ghi chú."""
    ds = []
    try:
        with open(FILE_TU_DIEN, encoding="utf-8") as f:
            for dong in f:
                if "=" in dong and not dong.lstrip().startswith("#"):
                    tu, doc = dong.split("=", 1)
                    if tu.strip():
                        ds.append((tu.strip(), doc.strip()))
    except FileNotFoundError:
        pass
    return ds


def ap_tu_dien(van_ban, ds):
    for tu, doc in sorted(ds, key=lambda x: -len(x[0])):
        van_ban = re.sub(rf"(?<!\w){re.escape(tu)}(?!\w)", lambda m: doc, van_ban)
    return van_ban


def tra_chu_goc(chu, ds):
    """Đổi cách đọc trong phụ đề về lại chữ gốc của từ điển."""
    for tu, doc in sorted(ds, key=lambda x: -len(x[1])):
        if doc:
            chu = re.sub(rf"(?<!\w){re.escape(doc)}(?!\w)", lambda m: tu, chu)
    return chu


def tu_dien_cho_srt(goc, ds):
    """Bỏ những mục mà cách đọc đã có sẵn trong văn bản gốc, để không đổi nhầm chữ người dùng viết."""
    return [(tu, doc) for tu, doc in ds
            if doc and not re.search(rf"(?<!\w){re.escape(doc)}(?!\w)", goc)]


def chuan_cau(c):
    return re.sub(r"\s+", " ", c).strip()


def ban_do_cau(goc, ds):
    """{câu đã thay theo từ điển: câu gốc}, để phụ đề hiện đúng chữ người dùng viết."""
    if not ds:
        return {}
    return {chuan_cau(ap_tu_dien(c, ds)): chuan_cau(c)
            for c in re.split(r"(?<=[.!?。！？…])\s+|\n+", goc) if c.strip()}


def gio_srt(giay):
    ms = int(round(giay * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def tinh_do_dai_doan(mp3, cau):
    """Tính độ dài đoạn audio: ưu tiên mốc câu của Microsoft Neural, dự phòng theo kích thước mp3."""
    if cau:
        return max(bd + dai for bd, dai, _ in cau)
    return len(mp3) / BYTE_MOI_GIAY


def chia_cau_shorts(chu, max_tu=7, max_ky_tu=38):
    """Chia câu dài thành các cụm từ ngắn phù hợp màn hình dọc Shorts / TikTok (tối đa max_tu từ hoặc max_ky_tu ký tự)."""
    cac_tu = chu.split()
    if not cac_tu:
        return []
    if len(cac_tu) <= max_tu and len(chu) <= max_ky_tu:
        return [chu]
    cum, hien_tai = [], []
    for tu in cac_tu:
        thu_them = " ".join(hien_tai + [tu])
        if len(hien_tai) >= max_tu or (hien_tai and len(thu_them) > max_ky_tu):
            cum.append(" ".join(hien_tai))
            hien_tai = [tu]
        else:
            hien_tai.append(tu)
    if hien_tai:
        cum.append(" ".join(hien_tai))
    return cum


def loc_kich_ban_youtube(van_ban):
    """Lọc bỏ các ghi chú đạo diễn, phân cảnh, âm nhạc và markdown thường gặp trong kịch bản video."""
    if not van_ban:
        return "", 0
    dem = 0

    def thay_ngoac_vuong(m):
        nonlocal dem
        if gc.la_the_sac_thai(m.group(0)):      # thẻ sắc thái [hồi hộp], [hào hứng]... giữ cho giọng clone
            return m.group(0)
        dem += 1
        return ""
    vb = re.sub(r"\[[^\]\n]*\]", thay_ngoac_vuong, van_ban)

    tu_khoa_dao_dien = r"cười|nhạc|music|b-roll|camera|voice|intro|outro|cut|quay|cảnh|âm thanh|sfx|sound|tiếng|hiệu ứng|vỗ tay|hét|thì thầm|pause|nghỉ"
    def thay_ngoac_tron(m):
        nonlocal dem
        noi_dung = m.group(0)
        if re.search(tu_khoa_dao_dien, noi_dung, re.IGNORECASE):
            dem += 1
            return ""
        return noi_dung
    vb = re.sub(r"\([^\)\n]*\)", thay_ngoac_tron, vb)

    vb = re.sub(r"[*#_`]", "", vb)
    vb = re.sub(r"[ \t]+", " ", vb)
    vb = re.sub(r"\n{3,}", "\n\n", vb)
    return vb.strip(), dem


def tao_srt(ket_qua, tu_dien=({}, ()), kieu_shorts=False):
    """ket_qua: [(mp3, [(bắt_đầu_giây, dài_giây, câu)])] theo thứ tự đoạn.
    tu_dien: (bản đồ câu, từ điển dự phòng) để trả phụ đề về chữ gốc."""
    ban_do, du_phong = tu_dien
    dong, lech, so = [], 0.0, 0
    for mp3, cau in ket_qua:
        for bd, dai, chu in cau:
            chu = ban_do.get(chuan_cau(chu)) or tra_chu_goc(chu, du_phong)
            if kieu_shorts and len(chu.split()) > 7:
                cum = chia_cau_shorts(chu, max_tu=7)
                tong_len = sum(len(c) for c in cum) or 1
                t_hien_tai = lech + bd
                for c in cum:
                    so += 1
                    t_doan = dai * (len(c) / tong_len)
                    dong.append(f"{so}\n{gio_srt(t_hien_tai)} --> {gio_srt(t_hien_tai + t_doan)}\n{c}\n")
                    t_hien_tai += t_doan
            else:
                so += 1
                dong.append(f"{so}\n{gio_srt(lech + bd)} --> {gio_srt(lech + bd + dai)}\n{chu}\n")
        lech += tinh_do_dai_doan(mp3, cau)
    return "\n".join(dong)


def lay_danh_sach_cau(ket_qua, tu_dien=({}, ())):
    """Trích xuất danh sách [(bat_dau, ket_thuc, chu_that)] theo timeline audio để karaoke highlight."""
    ban_do, du_phong = tu_dien
    cau_list, lech = [], 0.0
    for mp3, cau in ket_qua:
        for bd, dai, chu in cau:
            chu_that = ban_do.get(chuan_cau(chu)) or tra_chu_goc(chu, du_phong)
            cau_list.append((lech + bd, lech + bd + dai, chu_that.strip()))
        lech += tinh_do_dai_doan(mp3, cau)
    return cau_list


def dich_tieng_viet(van_ban):
    """Dịch văn bản sang tiếng Việt bằng endpoint miễn phí không cần key."""
    if not van_ban.strip():
        return ""
    cac_doan = [d for d in chia_doan(van_ban, toi_da=1800) if d.strip()]
    ket_qua = []
    for doan in cac_doan:
        try:
            params = urllib.parse.urlencode({
                "client": "gtx",
                "sl": "auto",
                "tl": "vi",
                "dt": "t",
                "q": doan
            })
            url = f"https://translate.googleapis.com/translate_a/single?{params}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read().decode("utf-8"))
                if data and isinstance(data, list) and data[0]:
                    dich_doan = "".join(item[0] for item in data[0] if item and isinstance(item, list) and item[0])
                    ket_qua.append(dich_doan)
        except Exception as e:
            raise RuntimeError(f"Lỗi dịch đoạn '{doan[:30]}...': {e}")
    return "\n\n".join(ket_qua)


_RE_HET_CAU = re.compile(r"[.!?\u2026]+[\x22\x27\u201d\u2019)\]]*(?=\s+\S)")


def tach_cau_hien_thi(txt):
    """Câu trong ô chữ: [(đầu, cuối, dòng)] theo vị trí ký tự, mỗi dòng (đoạn) tách ở . ! ? và ba chấm
    để ghép cặp câu Anh với câu Việt và tô đúng câu đang đọc."""
    ra = []
    for m_dong in re.finditer(r"[^\n]*", txt or ""):
        dong = m_dong.group(0)
        if not re.search(r"\w", dong):
            continue
        goc, dau = m_dong.start(), len(dong) - len(dong.lstrip())
        for m in _RE_HET_CAU.finditer(dong):
            if re.search(r"\w", dong[dau:m.end()]):
                ra.append((goc + dau, goc + m.end(), m_dong.start()))
                dau = m.end() + len(dong[m.end():]) - len(dong[m.end():].lstrip())
        cuoi = len(dong.rstrip())
        if dau < cuoi and re.search(r"\w", dong[dau:cuoi]):
            ra.append((goc + dau, goc + cuoi, m_dong.start()))
    return ra


def _goi_dich(doan):
    params = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "vi", "dt": "t", "q": doan})
    req = urllib.request.Request(f"https://translate.googleapis.com/translate_a/single?{params}",
                                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    return "".join(it[0] for it in (data[0] or []) if it and isinstance(it, list) and it[0])


def dich_cau(ds_cau, goi=None):
    """Dịch danh sách câu (không trùng) sang tiếng Việt, gom nhiều câu một lượt, mỗi câu một dòng.
    Lượt gom trả số dòng lệch thì dịch lẻ từng câu của lượt đó. Trả {câu: bản dịch}. Không dùng khoá nào."""
    goi = goi or _goi_dich
    ra, lo, dai = {}, [], 0

    def xa(lo):
        if not lo:
            return
        kq = [k.strip() for k in goi("\n".join(lo)).split("\n") if k.strip()]
        if len(kq) == len(lo):
            ra.update(zip(lo, kq))
        else:
            for c in lo:
                ra[c] = goi(c).strip()
    for c in ds_cau:
        if lo and dai + len(c) > 1500:
            xa(lo)
            lo, dai = [], 0
        lo.append(c)
        dai += len(c) + 1
    xa(lo)
    return ra


def so_phien_ban(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def che_do_thu():
    """Máy chủ giả chỉ dùng khi chạy bằng Python, hoặc exe có file DOCVOICE_THU đặt cạnh (máy dev)."""
    if not getattr(sys, "frozen", False):
        return True
    d = os.path.dirname(sys.executable)
    return os.path.exists(os.path.join(d, "DOCVOICE_THU")) or os.path.exists(os.path.join(d, "DOCGIONG_THU"))


def kiem_ban_moi():
    """Trả (phiên bản, asset exe) nếu GitHub có bản mới hơn, không thì None."""
    api = (che_do_thu() and (os.environ.get("DOCVOICE_API") or os.environ.get("DOCGIONG_API"))) or \
        f"https://api.github.com/repos/{KHO_GITHUB}/releases/latest"
    req = urllib.request.Request(api,
                                 headers={"User-Agent": "DocVoice", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        rel = json.load(r)
    tag = rel.get("tag_name", "")
    if not tag or so_phien_ban(tag) <= so_phien_ban(PHIEN_BAN):
        return None
    for a in rel.get("assets", []):
        if a["name"].lower() == TEN_EXE.lower():
            if not (a["browser_download_url"].startswith(LINK_TAI_HOP_LE) or che_do_thu()):
                return None
            return tag.lstrip("v"), a
    return None


def thay_exe(asset, mo_lai=True):
    """Tải exe mới, kiểm đủ byte và sha256, đổi tên exe đang chạy (Windows cho phép),
    đặt exe mới vào chỗ cũ, mở lại. Lỗi ở bất kỳ bước nào thì exe cũ vẫn nguyên."""
    exe = sys.executable
    thu_muc = os.path.dirname(exe)
    moi = os.path.join(thu_muc, TEN_EXE + ".new")
    cu = os.path.join(thu_muc, "DocVoice.old.exe")
    can_size = asset.get("size")
    can_sha = (asset.get("digest") or "").removeprefix("sha256:").lower()
    try:
        h, n = hashlib.sha256(), 0
        req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "DocVoice"})
        with urllib.request.urlopen(req, timeout=60) as r, open(moi, "wb") as f:
            dai = r.headers.get("Content-Length")
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
                h.update(b)
                n += len(b)
        if dai is not None and n != int(dai):
            raise RuntimeError(f"Tải chưa đủ ({n}/{dai} byte), mạng bị ngắt")
        if can_size and n != can_size:
            raise RuntimeError(f"Kích thước không khớp ({n}/{can_size} byte)")
        if can_sha and h.hexdigest() != can_sha:
            raise RuntimeError("Mã kiểm sha256 không khớp, file tải về đã bị sửa")
        if not can_size and not can_sha and not che_do_thu():
            raise RuntimeError("Bản phát hành thiếu thông tin kiểm tra")
        with open(moi, "rb") as f:
            if f.read(2) != b"MZ":
                raise RuntimeError("File tải về không phải exe")
    except BaseException:
        if os.path.exists(moi):
            os.remove(moi)
        raise
    if os.path.exists(cu):
        os.remove(cu)
    os.replace(exe, cu)
    try:
        os.replace(moi, exe)
    except BaseException:
        os.replace(cu, exe)  # trả exe cũ về chỗ
        if os.path.exists(moi):
            os.remove(moi)
        raise
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    if not mo_lai:
        return
    subprocess.Popen([exe], cwd=thu_muc, env=env, close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def don_exe_cu():
    if getattr(sys, "frozen", False):
        thu_muc = os.path.dirname(sys.executable)
        for ten in ("DocVoice.old.exe", "DocGiong.old.exe"):
            cu = os.path.join(thu_muc, ten)
            if os.path.exists(cu):
                for _ in range(20):
                    try:
                        os.remove(cu)
                        break
                    except OSError:
                        time.sleep(0.5)


async def tao_doan(text, giong, toc_do, cao_do):
    """Trả (mp3, [(bắt_đầu_giây, dài_giây, câu)])."""
    loi = None
    for lan in range(SO_LAN_THU):
        try:
            comm = edge_tts.Communicate(text, giong, rate=toc_do, pitch=cao_do)
            mp3, cau = bytearray(), []
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    mp3.extend(chunk["data"])
                elif chunk["type"] == "SentenceBoundary":
                    bd = chunk["offset"] / 10_000_000
                    dai = chunk["duration"] / 10_000_000
                    cau.append((bd, dai, chunk["text"]))
            return bytes(mp3), cau
        except Exception as e:
            loi = e
            if lan < SO_LAN_THU - 1:
                await asyncio.sleep(1 + lan)
    raise loi


def chup_cua_so(hwnd, duong_bmp):
    """Chụp một cửa sổ (kể cả khi bị che) bằng PrintWindow, lưu BMP 32 bit. Dùng cho chế độ tự thử."""
    u32, g32 = ctypes.windll.user32, ctypes.windll.gdi32

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]
    rc = RECT()
    u32.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = rc.r - rc.l, rc.b - rc.t
    hdc = u32.GetWindowDC(hwnd)
    mdc = g32.CreateCompatibleDC(hdc)
    bmp = g32.CreateCompatibleBitmap(hdc, w, h)
    g32.SelectObject(mdc, bmp)
    u32.PrintWindow(hwnd, mdc, 2)
    hdr = (ctypes.c_byte * 40)()
    ctypes.memmove(hdr, (40).to_bytes(4, "little") + w.to_bytes(4, "little", signed=True) +
                   (-h).to_bytes(4, "little", signed=True) + (1).to_bytes(2, "little") +
                   (32).to_bytes(2, "little") + bytes(24), 40)
    buf = (ctypes.c_byte * (w * h * 4))()
    g32.GetDIBits(mdc, bmp, 0, h, buf, hdr, 0)
    with open(duong_bmp, "wb") as f:
        f.write(b"BM" + (54 + w * h * 4).to_bytes(4, "little") + bytes(4) + (54).to_bytes(4, "little"))
        f.write(bytes(hdr))
        f.write(bytes(buf))
    g32.DeleteObject(bmp)
    g32.DeleteDC(mdc)
    u32.ReleaseDC(hwnd, hdc)


# ---------- giao diện ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Đọc Voice · Text to Speech · v{PHIEN_BAN}")
        self.geometry("980x720")
        self.minsize(800, 600)
        self.configure(bg="#f6f8fa")
        self.giong = doc_json(FILE_GIONG, [])
        self.cd = doc_json(FILE_CAI_DAT, {})
        self._khoa_trang_thai = threading.Lock()
        self.dang_chay = False
        self.file_cuoi = None
        self._ban_moi_cho = None
        self._danh_sach_cau = []
        self._dang_theo_doi_phat = False
        self._cau_highlight_cu = -1
        self._dung_clone = threading.Event()
        self._tu_dong = None
        self.thu_muc_ra = self.cd.get("thu_muc_ra", THU_MUC_RA)
        os.makedirs(self.thu_muc_ra, exist_ok=True)
        self._dung_giao_dien()
        if self.giong:
            self._nap_ngon_ngu()
        else:
            self.trang_thai.set("Đang tải danh sách giọng...")
        threading.Thread(target=self._tai_giong, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._thoat)
        threading.Thread(target=don_exe_cu, daemon=True).start()
        if getattr(sys, "frozen", False) and self.cd.get("tu_kiem_cap_nhat", True):
            threading.Thread(target=self._kiem_cap_nhat, args=(False,), daemon=True).start()
        kb = os.environ.get("DOCVOICE_TU_DONG")
        if kb and che_do_thu():  # chế độ tự thử: bấm như người dùng rồi ghi kết quả
            self._tu_dong = doc_json(kb, {})
            self.geometry(self._tu_dong.get("vi_tri", "1180x760+40+40"))
            self.after(2500, self._chay_tu_dong)
        else:
            self.after(300, lambda: self.state("zoomed"))  # vừa vùng làm việc, không lấn taskbar

    def _dung_giao_dien(self):
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", font=("Segoe UI", 10), background="#f6f8fa")
        s.configure("Tieu.TLabel", font=("Segoe UI Semibold", 16))
        s.configure("Xanh.TButton", foreground="white", background="#16a34a", padding=(14, 7))
        s.map("Xanh.TButton", background=[("active", "#15803d"), ("disabled", "#9ca3af")])
        s.configure("TButton", padding=(10, 6))
        s.configure("green.Horizontal.TProgressbar", background="#16a34a")

        khung = ttk.Frame(self, padding=16)
        khung.pack(fill="both", expand=True)

        ttk.Label(khung, text="Chuyển văn bản thành giọng nói (Đọc Voice)", style="Tieu.TLabel").pack(anchor="w")
        ttk.Label(khung, text="Giọng Microsoft Edge Neural và giọng clone trên máy (Arthur), 2 bảng song song tiếng Anh và bản dịch, highlight theo tiến trình đọc.",
                  foreground="#6b7280").pack(anchor="w", pady=(0, 10))

        hang1 = ttk.Frame(khung)
        hang1.pack(fill="x", pady=(0, 8))
        ttk.Button(hang1, text="Mở file văn bản", command=self._mo_file).pack(side="left")
        ttk.Button(hang1, text="Từ điển", command=self._tu_dien).pack(side="left", padx=(6, 0))
        ttk.Button(hang1, text="⚙ Cài đặt", command=self._cai_dat).pack(side="left", padx=(6, 0))
        ttk.Button(hang1, text="Cập nhật", command=lambda: threading.Thread(
            target=self._kiem_cap_nhat, args=(True,), daemon=True).start()).pack(side="right")
        ttk.Label(hang1, text="  Ngôn ngữ").pack(side="left")
        self.ngon_ngu = ttk.Combobox(hang1, state="readonly", width=22)
        self.ngon_ngu.pack(side="left", padx=6)
        self.ngon_ngu.bind("<<ComboboxSelected>>", lambda e: (self._nap_giong(), self._luu_cai_dat()))
        ttk.Label(hang1, text="Giọng").pack(side="left")
        self.chon_giong = ttk.Combobox(hang1, state="readonly", width=28)
        self.chon_giong.pack(side="left", padx=6)
        self.chon_giong.bind("<<ComboboxSelected>>", lambda e: (self._cap_nhat_cam_xuc(), self._luu_cai_dat()))
        ttk.Label(hang1, text="Cảm xúc").pack(side="left")
        self.cam_xuc = ttk.Combobox(hang1, state="disabled", width=20,
                                    values=[gc.MUC_TU_DONG] + [k for k in gc.MUC_CAM_XUC if k != gc.MUC_TU_DONG])
        self.cam_xuc.set(self.cd.get("cam_xuc", gc.MUC_MAC_DINH))
        self.cam_xuc.pack(side="left", padx=6)
        self.cam_xuc.bind("<<ComboboxSelected>>", lambda e: (self._cap_nhat_nhan_giong(), self._luu_cai_dat()))

        hang_cl = ttk.Frame(khung)
        hang_cl.pack(fill="x", pady=(0, 8))
        ttk.Label(hang_cl, text="Máy đọc giọng clone").pack(side="left")
        self.may_doc = ttk.Combobox(hang_cl, state="readonly", width=18,
                                    values=[mn.MayDocNhe.ten, gc.MayDocVoxCPM.ten])
        self.may_doc.set(self.cd.get("may_doc", mn.MayDocNhe.ten))
        self.may_doc.pack(side="left", padx=6)
        self.may_doc.bind("<<ComboboxSelected>>", lambda e: (self._cap_nhat_nhan_giong(), self._lam_nong(), self._luu_cai_dat()))
        ttk.Button(hang_cl, text="★ Giọng Arthur", command=self._chon_arthur).pack(side="left", padx=(6, 0))
        ttk.Button(hang_cl, text="Nhập giọng (.dgvoice)", command=self._nhap_giong).pack(side="left", padx=(6, 0))
        ttk.Button(hang_cl, text="Xuất giọng", command=self._xuat_giong).pack(side="left", padx=(6, 0))
        ttk.Button(hang_cl, text="▶ Đọc kịch bản YouTube", style="Xanh.TButton",
                   command=self._doc_kich_ban_youtube).pack(side="left", padx=(12, 0))
        self.nhan_may = ttk.Label(hang_cl, foreground="#6b7280")
        self.nhan_may.pack(side="left", padx=(10, 0))
        self.after(500, self._cap_nhat_nhan_may)

        hang2 = ttk.Frame(khung)
        hang2.pack(fill="x", pady=(0, 8))
        self.cao_do = tk.IntVar(value=self.cd.get("cao_do", 0))
        self.toc_do = tk.IntVar(value=self.cd.get("toc_do", 0))
        self.nhan_cao = ttk.Label(hang2, width=32)
        self.nhan_cao.grid(row=0, column=0, sticky="w")
        self.nhan_toc = ttk.Label(hang2, width=32)
        self.nhan_toc.grid(row=0, column=1, sticky="w", padx=(24, 0))
        ttk.Scale(hang2, from_=-50, to=50, variable=self.cao_do,
                  command=lambda v: self._cap_nhat_nhan()).grid(row=1, column=0, sticky="ew")
        ttk.Scale(hang2, from_=-50, to=100, variable=self.toc_do,
                  command=lambda v: self._cap_nhat_nhan()).grid(row=1, column=1, sticky="ew", padx=(24, 0))
        hang2.columnconfigure(0, weight=1)
        hang2.columnconfigure(1, weight=1)
        ttk.Button(hang2, text="Mặc định", command=self._mac_dinh).grid(row=1, column=2, padx=(12, 0))
        self._cap_nhat_nhan()

        self.nhan_giong = ttk.Label(khung, font=("Segoe UI Semibold", 10))
        self.nhan_giong.pack(side="bottom", anchor="w", pady=(4, 0))
        self.trang_thai = tk.StringVar(value="Sẵn sàng")
        ttk.Label(khung, textvariable=self.trang_thai, foreground="#374151").pack(side="bottom", anchor="w", pady=(4, 0))
        self.tien_do = ttk.Progressbar(khung, style="green.Horizontal.TProgressbar", maximum=100)
        self.tien_do.pack(side="bottom", fill="x", pady=(8, 0))

        hang3 = ttk.Frame(khung)
        hang3.pack(side="bottom", fill="x", pady=(8, 0))
        self.nut_chuyen = ttk.Button(hang3, text="Chuyển đổi", style="Xanh.TButton", command=self._chuyen)
        self.nut_chuyen.pack(side="left")
        self.nut_thu = ttk.Button(hang3, text="▶ Nghe", command=self._nghe_thu)
        self.nut_thu.pack(side="left", padx=6)
        self.nut_tam = ttk.Button(hang3, text="⏸ Tạm dừng", command=self._tam_dung_tiep)
        self.nut_tam.pack(side="left", padx=(0, 6))
        ttk.Button(hang3, text="Dừng chuyển", command=self._dung_chuyen).pack(side="left", padx=(0, 6))
        ttk.Button(hang3, text="Dừng phát", command=self._dung_phat_app).pack(side="left")
        ttk.Button(hang3, text="Phát lại", command=self._phat_lai).pack(side="left", padx=6)
        ttk.Button(hang3, text="Đã chuyển đổi", command=lambda: os.startfile(self.thu_muc_ra)).pack(side="left")
        ttk.Button(hang3, text="Xoá bảng đọc", command=lambda: self.o_chu.delete("1.0", "end")).pack(side="left", padx=6)
        ttk.Button(hang3, text="Xoá cả 2", command=self._xoa_ca_hai).pack(side="left")

        # Khung văn bản 2 bảng chia đôi
        khung_paned = ttk.PanedWindow(khung, orient="horizontal")
        khung_paned.pack(fill="both", expand=True)

        # Bảng trái: Văn bản đọc
        cot_trai = ttk.Frame(khung_paned)
        khung_paned.add(cot_trai, weight=1)

        tieu_trai = ttk.Frame(cot_trai)
        tieu_trai.pack(fill="x", pady=(0, 4))
        ttk.Label(tieu_trai, text="Văn bản đọc (chỉ đọc mục này)", font=("Segoe UI Semibold", 10),
                  foreground="#1f2937").pack(side="left")
        ttk.Button(tieu_trai, text="Lọc kịch bản YT", command=self._loc_kich_ban_yt).pack(side="left", padx=(8, 0))
        self.dem_trai = ttk.Label(tieu_trai, foreground="#dc2626", font=("Segoe UI", 9))
        self.dem_trai.pack(side="right")

        khung_text_trai = ttk.Frame(cot_trai)
        khung_text_trai.pack(fill="both", expand=True)
        self.o_chu = tk.Text(khung_text_trai, wrap="word", font=("Segoe UI", 11), relief="solid",
                             borderwidth=1, padx=10, pady=8, undo=True)
        cuon_trai = ttk.Scrollbar(khung_text_trai, command=self.o_chu.yview)
        self.o_chu.configure(yscrollcommand=cuon_trai.set)
        self.o_chu.pack(side="left", fill="both", expand=True)
        cuon_trai.pack(side="right", fill="y")
        self.o_chu.tag_config("dang_doc", background="#fef08a", foreground="#000000")
        self.o_chu.bind("<Double-Button-1>", self._nghe_tu_cau)   # giọng clone: bấm đúp câu nào nghe từ câu đó

        # Bảng phải: Bản dịch tham khảo
        cot_phai = ttk.Frame(khung_paned)
        khung_paned.add(cot_phai, weight=1)

        tieu_phai = ttk.Frame(cot_phai)
        tieu_phai.pack(fill="x", pady=(0, 4))
        ttk.Label(tieu_phai, text="Bản dịch tham khảo (tiếng Việt)", font=("Segoe UI Semibold", 10),
                  foreground="#1f2937").pack(side="left")
        self.nut_dich = ttk.Button(tieu_phai, text="Dịch sang tiếng Việt", command=self._dich_van_ban)
        self.nut_dich.pack(side="right")
        ttk.Button(tieu_phai, text="Xoá dịch", command=lambda: self.o_dich.delete("1.0", "end")).pack(side="right", padx=4)

        khung_text_phai = ttk.Frame(cot_phai)
        khung_text_phai.pack(fill="both", expand=True)
        self.o_dich = tk.Text(khung_text_phai, wrap="word", font=("Segoe UI", 11), relief="solid",
                              borderwidth=1, padx=10, pady=8, undo=True, bg="#fcfcfd")
        cuon_phai = ttk.Scrollbar(khung_text_phai, command=self.o_dich.yview)
        self.o_dich.configure(yscrollcommand=cuon_phai.set)
        self.o_dich.pack(side="left", fill="both", expand=True)
        cuon_phai.pack(side="right", fill="y")
        self.o_dich.tag_config("dang_doc", background="#fef08a", foreground="#000000")

        # Nạp dữ liệu cũ
        self.o_chu.insert("1.0", self.cd.get("van_ban", ""))
        self._bo_dem_dich = doc_json(FILE_BO_DEM_DICH, {})
        txt0 = self.o_chu.get("1.0", "end-1c")
        if self.cd.get("ban_dich_cho") == hashlib.sha1(txt0.encode("utf-8")).hexdigest():
            self.o_dich.insert("1.0", self.cd.get("ban_dich", ""))     # bản dịch cũ không khớp chữ thì bỏ
        self.o_chu.bind("<<Modified>>", self._dem_chu)
        self._dem_chu()

    # --- danh sách giọng ---
    def _tai_giong(self):
        try:
            ds = asyncio.run(edge_tts.list_voices())
            ds = [{k: v[k] for k in ("ShortName", "Locale", "Gender")} for v in ds]
            ghi_json(FILE_GIONG, ds)
            moi = not self.giong
            self.giong = ds
            if moi:
                self.after(0, self._nap_ngon_ngu)
            if not getattr(self, "_gioi_thieu_clone", False):
                self.after(0, lambda: self.trang_thai.set(
                    f"Đã tải {len(ds)} giọng từ máy chủ." + ("" if gc.ds_giong() else " " + HUONG_DAN_NHAP_GIONG)))
        except Exception as e:
            self.after(0, lambda e=e: self.trang_thai.set(f"Không tải được danh sách giọng: {e}"))

    def _nap_ngon_ngu(self):
        co_san = {v["Locale"] for v in self.giong}
        uu_tien = ["vi-VN", "en-US", "en-AU", "en-GB"]
        ds = [MA_CLONE] + [loc for loc in uu_tien if loc in co_san] + sorted(co_san - set(uu_tien))
        self._locales = ds
        self.ngon_ngu["values"] = [f"{TEN_NGON_NGU.get(loc, loc)} ({loc})" for loc in ds]
        chon = self.cd.get("ngon_ngu", "en-AU")
        cl = gc.ds_giong()
        if cl and not self.cd.get("da_gioi_thieu_clone"):
            # bản đầu tiên có giọng clone mở lên là Arthur, không giữ giọng Edge cũ trong cai_dat.json
            self.cd["da_gioi_thieu_clone"] = True
            if not str(self.cd.get("giong", "")).startswith(gc.TIEN_TO_CLONE):
                chon = MA_CLONE
                self.cd["ngon_ngu"] = MA_CLONE
                self.cd["giong"] = gc.TIEN_TO_CLONE + ("arthur" if "arthur" in cl else next(iter(cl)))
                self._gioi_thieu_clone = True
        idx = ds.index(chon) if chon in ds else (ds.index("vi-VN") if "vi-VN" in ds else 0)
        self.ngon_ngu.current(idx)
        self._nap_giong()
        if not self.cd.get("da_gioi_thieu_tu_dong"):   # bản có cảm xúc tự động: đặt làm mặc định một lần
            self.cd["da_gioi_thieu_tu_dong"] = True
            self.cam_xuc.set(gc.MUC_TU_DONG)
            self._cap_nhat_nhan_giong()
        if getattr(self, "_gioi_thieu_clone", False):
            self.trang_thai.set("Bản này có giọng Arthur (clone): đã chọn sẵn. Muốn giọng Edge thì đổi ô Ngôn ngữ, "
                                "muốn quay lại Arthur bấm ★ Giọng Arthur.")

    def _nap_giong(self):
        loc = self._locales[self.ngon_ngu.current()]
        if loc == MA_CLONE:
            ds_cl = gc.ds_giong()
            self._ds_giong = [gc.TIEN_TO_CLONE + slug for slug in ds_cl] or [gc.TIEN_TO_CLONE]
            self.chon_giong["values"] = ([f"{v['ten']} · {v.get('gioi', '')}" for v in ds_cl.values()]
                                         or ["(chưa có, bấm Nhập giọng)"])
            luu = self.cd.get("giong")
            self.chon_giong.current(self._ds_giong.index(luu) if luu in self._ds_giong else 0)
            self._cap_nhat_cam_xuc()
            if not ds_cl:
                self.trang_thai.set(HUONG_DAN_NHAP_GIONG)
            self._lam_nong()
            return
        ds = [v for v in self.giong if v["Locale"] == loc]
        cl = gc.ds_giong() if loc.lower().startswith("en") else {}
        self._ds_giong = [gc.TIEN_TO_CLONE + slug for slug in cl] + [v["ShortName"] for v in ds]
        self.chon_giong["values"] = ([f"★ {v['ten']} · {v.get('gioi', '')}" for v in cl.values()]
                                     + [ten_hien_thi(v) for v in ds])
        luu = self.cd.get("giong")
        if luu in self._ds_giong:
            self.chon_giong.current(self._ds_giong.index(luu))
        else:
            multi = [i for i, v in enumerate(ds) if "Multilingual" in v["ShortName"]]
            self.chon_giong.current(len(cl) + (multi[0] if multi else 0))
        self._cap_nhat_cam_xuc()

    def _la_clone(self):
        ds = getattr(self, "_ds_giong", None)
        return bool(ds) and ds[self.chon_giong.current()].startswith(gc.TIEN_TO_CLONE)

    def _cap_nhat_cam_xuc(self):
        """Mức cảm xúc chỉ bật cho giọng clone (giọng Edge không có tham số này)."""
        self.cam_xuc.configure(state="readonly" if self._la_clone() else "disabled")
        self._cap_nhat_nhan_giong()
        if self._la_clone():
            self._lam_nong()

    def _ten_giong_dang_chon(self):
        try:
            ma = self._ds_giong[self.chon_giong.current()]
        except Exception:
            return ""
        if ma.startswith(gc.TIEN_TO_CLONE):
            tt = gc.ds_giong().get(ma[len(gc.TIEN_TO_CLONE):])
            if not tt:
                return "chưa có giọng clone (bấm Nhập giọng)"
            return f"{tt['ten']} · {self.cam_xuc.get() or gc.MUC_MAC_DINH} · máy {self.may_doc.get()}"
        v = next((x for x in self.giong if x["ShortName"] == ma), None)
        return f"{ten_hien_thi(v) if v else ma} (Edge, {ma.split('-')[0]}-{ma.split('-')[1]})" if ma.count("-") >= 2 else ma

    def _cap_nhat_nhan_giong(self):
        try:
            ten = self._ten_giong_dang_chon()
            self.nhan_giong.config(text=f"Giọng sẽ đọc: {ten}" if ten else "",
                                   foreground="#15803d" if self._la_clone() else "#b45309")
        except Exception:
            pass

    def _chon_arthur(self):
        """Nút nhanh: chuyển sang giọng Arthur (clone) ở bất kỳ ngôn ngữ nào."""
        cl = gc.ds_giong()
        if not cl:
            messagebox.showinfo("Đọc Voice", "Máy này chưa có giọng Arthur. Bấm Nhập giọng (.dgvoice) và chọn Arthur.dgvoice.")
            return
        self.cd["giong"] = gc.TIEN_TO_CLONE + ("arthur" if "arthur" in cl else next(iter(cl)))
        self.ngon_ngu.current(self._locales.index(MA_CLONE))
        self._nap_giong()

    # --- điều khiển ---
    def _san_sang(self):
        if getattr(self, "_ds_giong", None):
            return True
        messagebox.showinfo("Đọc Voice", "Đang tải danh sách giọng, chờ vài giây rồi bấm lại.")
        return False

    def _tham_so(self):
        cao, toc = self.cao_do.get(), self.toc_do.get()
        return self._ds_giong[self.chon_giong.current()], f"{toc:+d}%", f"{cao:+d}Hz"

    def _cap_nhat_nhan(self):
        def mo_ta(v):
            return "Bình thường" if v == 0 else f"{v:+d}"
        self.nhan_cao.config(text=f"Cao độ giọng nói ({mo_ta(self.cao_do.get())})")
        self.nhan_toc.config(text=f"Tốc độ đọc ({mo_ta(self.toc_do.get())}%)" if self.toc_do.get()
                             else "Tốc độ đọc (Bình thường)")
        if hasattr(self, "o_chu"):
            self._dem_chu()

    def _mac_dinh(self):
        self.cao_do.set(0)
        self.toc_do.set(0)
        self._cap_nhat_nhan()

    def _dem_chu(self, _=None):
        if self.o_chu.edit_modified() or _ is None:            # ngừng gõ 1,5 giây thì tự dịch
            if getattr(self, "_hen_dich", None):
                self.after_cancel(self._hen_dich)
            self._hen_dich = self.after(1500, self._tu_dich)
        t = self.o_chu.get("1.0", "end").strip()
        cac_tu = t.split()
        so_tu = len(cac_tu)
        so_ky_tu = len(t)
        if so_tu == 0:
            self.dem_trai.config(text="0 từ (0 ký tự)")
            self.o_chu.edit_modified(False)
            return

        toc = self.toc_do.get()
        he_so = max(0.5, (100 + toc) / 100.0)
        so_giay = max(1, int(so_tu / (2.5 * he_so)))
        phut = so_giay // 60
        giay = so_giay % 60
        chuoi_tg = f"{phut}p {giay:02d}s" if phut > 0 else f"{giay}s"

        if so_giay <= 60:
            badge = "Shorts"
        elif so_giay >= 480:
            badge = "Mid-roll"
        else:
            badge = "YouTube"

        self.dem_trai.config(text=f"{so_tu} từ ({so_ky_tu} ký tự) · ~{chuoi_tg} · {badge}")
        self.o_chu.edit_modified(False)

    def _loc_kich_ban_yt(self):
        t = self.o_chu.get("1.0", "end").strip()
        if not t:
            messagebox.showinfo("Đọc Voice", "Chưa có nội dung bên bảng đọc để lọc.")
            return
        vb_sach, dem = loc_kich_ban_youtube(t)
        if dem == 0:
            messagebox.showinfo("Đọc Voice", "Kịch bản đã sạch, không có ghi chú phân cảnh hay âm nhạc thừa.")
            return
        if messagebox.askyesno("Lọc kịch bản YouTube",
                               f"Tìm thấy {dem} ghi chú đạo diễn, phân cảnh, âm nhạc và markdown thừa.\n\n"
                               "Bạn có muốn lọc sạch các ghi chú này để đọc voice trơn tru không?"):
            self.o_chu.delete("1.0", "end")
            self.o_chu.insert("1.0", vb_sach)
            self._dem_chu()
            self.trang_thai.set(f"Đã lọc sạch {dem} ghi chú thừa trong kịch bản YouTube.")

    def _xoa_ca_hai(self):
        self.o_chu.delete("1.0", "end")
        self.o_dich.delete("1.0", "end")
        self._xoa_highlight()
        self._dem_chu()

    def _mo_file(self):
        p = filedialog.askopenfilename(filetypes=[("Văn bản, Word", "*.txt *.docx *.md *.srt"), ("Tất cả", "*.*")])
        if not p:
            return
        if p.lower().endswith(".docx"):
            try:
                noi_dung = doc_docx(p)
            except Exception as e:
                messagebox.showerror("Đọc Voice", f"Không đọc được file Word:\n{e}")
                return
            self.o_chu.delete("1.0", "end")
            self.o_chu.insert("1.0", noi_dung)
            self._dem_chu()
            return
        with open(p, "rb") as f:
            dau = f.read(2)
        for ma in (("utf-16",) if dau in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "cp1258")):
            try:
                with open(p, encoding=ma) as f:
                    noi_dung = f.read()
                break
            except Exception:
                continue
        else:
            messagebox.showerror("Đọc Voice", "Không đọc được file này.")
            return
        self.o_chu.delete("1.0", "end")
        self.o_chu.insert("1.0", noi_dung)
        self._dem_chu()

    # --- tự dịch theo câu: ghép cặp câu Anh, câu Việt để tô cùng lúc ---
    def _tu_dich(self, bat_ky=False):
        self._hen_dich = None
        txt = self.o_chu.get("1.0", "end-1c")
        ma = hashlib.sha1(txt.encode("utf-8")).hexdigest()
        if not txt.strip():
            self.o_dich.delete("1.0", "end")
            self._cap_dich = None
            return
        if not bat_ky and getattr(self, "_cap_dich", None) and self._cap_dich["ma"] == ma:
            return
        cau = tach_cau_hien_thi(txt)
        thieu = sorted({txt[a:b] for a, b, _d in cau} - set(self._bo_dem_dich))
        if not thieu:
            self._ve_ban_dich(txt, ma, cau)
            return
        if getattr(self, "_dang_dich", False):
            self._hen_dich = self.after(1500, self._tu_dich)
            return
        self._dang_dich = True
        self.trang_thai.set(f"Đang tự dịch {len(thieu)} câu sang tiếng Việt...")

        def lam():
            loi = None
            try:
                self._bo_dem_dich.update(dich_cau(thieu))
            except Exception as e:
                loi = e

            def xong():
                self._dang_dich = False
                if loi is not None:
                    self.trang_thai.set("Chưa dịch được bản tham khảo (kiểm mạng). Sửa chữ hoặc bấm Dịch để thử lại.")
                else:
                    self.trang_thai.set(f"Đã tự dịch {len(thieu)} câu.")
                if self.o_chu.get("1.0", "end-1c") == txt:
                    self._ve_ban_dich(txt, ma, cau)
                try:
                    if len(self._bo_dem_dich) > 6000:
                        self._bo_dem_dich = dict(list(self._bo_dem_dich.items())[-4000:])
                    ghi_json(FILE_BO_DEM_DICH, self._bo_dem_dich)
                except OSError:
                    pass
            self.after(0, xong)
        threading.Thread(target=lam, daemon=True).start()

    def _ve_ban_dich(self, txt, ma, cau):
        """Ô dịch giữ đúng các dòng, đoạn của ô chữ; mỗi câu Anh có vị trí câu Việt tương ứng."""
        theo_dong = {}
        for a, b, d in cau:
            theo_dong.setdefault(d, []).append((a, b))
        ra, vt, dau_dong, vi_tri = [], 0, 0, {}
        for dong in txt.split("\n"):
            phan = []
            for a, b in theo_dong.get(dau_dong, []):
                vi = self._bo_dem_dich.get(txt[a:b], "…")
                bd = vt + sum(len(x) + 1 for x in phan)
                vi_tri[(a, b)] = (bd, bd + len(vi))
                phan.append(vi)
            chu_dong = " ".join(phan)
            ra.append(chu_dong)
            vt += len(chu_dong) + 1
            dau_dong += len(dong) + 1
        self.o_dich.delete("1.0", "end")
        self.o_dich.insert("1.0", "\n".join(ra))
        self._cap_dich = {"ma": ma, "cau": cau, "vi": [vi_tri[(a, b)] for a, b, _d in cau]}
        self.cd["ban_dich_cho"] = ma

    def _to_dich_theo(self, vt_chu):
        """Tô câu Việt ứng với câu Anh chứa vị trí vt_chu (ký tự trong ô chữ)."""
        try:
            self.o_dich.tag_remove("dang_doc", "1.0", "end")
        except Exception:
            return None
        cd = getattr(self, "_cap_dich", None)
        if not cd or cd["ma"] != hashlib.sha1(self.o_chu.get("1.0", "end-1c").encode("utf-8")).hexdigest():
            return None
        k = None
        for i, (a, b, _d) in enumerate(cd["cau"]):
            if a <= vt_chu:
                k = i
        if k is None:
            return None
        a, b = cd["vi"][k]
        self.o_dich.tag_add("dang_doc", f"1.0+{a}c", f"1.0+{b}c")
        if self.cd.get("tu_cuon", True):
            self.o_dich.see(f"1.0+{a}c")
        return k

    def _dich_van_ban(self):
        van_ban = self.o_chu.get("1.0", "end").strip()
        if not van_ban:
            messagebox.showinfo("Đọc Voice", "Chưa có văn bản bên bảng đọc để dịch.")
            return
        if van_ban:                                # nút Dịch: dịch ngay theo câu, cùng đường với tự dịch
            self._tu_dich(bat_ky=True)
            return
        self.nut_dich.state(["disabled"])
        self.nut_dich.config(text="Đang dịch...")
        self.trang_thai.set("Đang dịch kịch bản sang tiếng Việt...")

        def lam():
            try:
                ban_dich = dich_tieng_viet(van_ban)
                def cap_nhat():
                    self.o_dich.delete("1.0", "end")
                    self.o_dich.insert("1.0", ban_dich)
                    self.trang_thai.set(f"Đã dịch xong ({len(ban_dich.split())} từ tiếng Việt).")
                    self.nut_dich.config(text="Dịch sang tiếng Việt")
                    self.nut_dich.state(["!disabled"])
                    self._luu_cai_dat()
                self.after(0, cap_nhat)
            except Exception as e:
                err_msg = str(e)
                def bao_loi(msg=err_msg):
                    self.trang_thai.set(f"Lỗi dịch: {msg}")
                    self.nut_dich.config(text="Dịch sang tiếng Việt")
                    self.nut_dich.state(["!disabled"])
                    messagebox.showerror("Đọc Voice", f"Không dịch được:\n{msg}")
                self.after(0, bao_loi)

        threading.Thread(target=lam, daemon=True).start()

    # --- highlight và phát âm thanh ---
    def _xoa_highlight(self):
        self._cau_highlight_cu = -1
        try:
            self.o_chu.tag_remove("dang_doc", "1.0", "end")
            self.o_dich.tag_remove("dang_doc", "1.0", "end")
        except Exception:
            pass

    def _highlight_cau(self, chu, idx):
        """Tô câu đang phát ở ô chữ (tìm tiếp từ câu trước, đúng thứ tự) và câu Việt ghép cặp ở ô dịch."""
        self._xoa_highlight()
        if not chu:
            return
        txt = self.o_chu.get("1.0", "end-1c")
        tu = chu.split()[:6]
        if not tu:
            return
        mau = re.compile(r"\s+".join(re.escape(t) for t in tu))
        m = mau.search(txt, getattr(self, "_vt_tim", 0)) or mau.search(txt)
        if not m:
            return
        self._vt_tim = m.start() + 1
        a, b = f"1.0+{m.start()}c", f"1.0+{min(len(txt), m.start() + len(chu))}c"
        self.o_chu.tag_add("dang_doc", a, b)
        if self.cd.get("tu_cuon", True):
            self.o_chu.see(a)
        self._to_dich_theo(m.start())

    def _theo_doi_phat(self):
        if not getattr(self, "_dang_theo_doi_phat", False):
            self._xoa_highlight()
            return
        vt = vi_tri_phat()
        da_chay = vt if vt is not None else time.time() - getattr(self, "_thoi_gian_phat", 0)

        cau_hien_tai = None
        idx_hien_tai = -1
        if self._danh_sach_cau:
            for idx, (bd, kt, chu) in enumerate(self._danh_sach_cau):
                if bd <= da_chay < kt:
                    cau_hien_tai = chu
                    idx_hien_tai = idx
                    break
            tong_tg = self._danh_sach_cau[-1][1]
            if da_chay > tong_tg + 0.5:
                self._dang_theo_doi_phat = False
                self._xoa_highlight()
                self.trang_thai.set("Đã phát xong.")
                return

        if idx_hien_tai != self._cau_highlight_cu:
            self._cau_highlight_cu = idx_hien_tai
            if cau_hien_tai:
                self._highlight_cau(cau_hien_tai, idx_hien_tai)
                self._lech_to.append(round(da_chay - self._danh_sach_cau[idx_hien_tai][0], 3))
            else:
                self._xoa_highlight()

        if getattr(self, "_dang_theo_doi_phat", False):
            self.after(40, self._theo_doi_phat)

    def _bat_dau_phat(self, duong_dan, cau_list=None):
        if cau_list:
            self._danh_sach_cau = cau_list
        self._cau_highlight_cu = -1
        self._vt_tim = 0
        self._lech_to = []
        self._thoi_gian_phat = time.time()
        self._dang_theo_doi_phat = True
        phat(duong_dan)
        self._theo_doi_phat()

    def _dung_phat_app(self):
        self._dang_theo_doi_phat = False
        dung_phat()
        if getattr(self, "_phat_luong", None) is not None:      # đang nghe luồng: dừng phát và dừng sinh
            self._phat_luong.dung()
            self._phat_luong = None
            if self.dang_chay and getattr(self, "_luot_luong", False):
                self._dung_clone.set()
        self.nut_tam.config(text="⏸ Tạm dừng")
        self._xoa_highlight()
        self.trang_thai.set("Đã dừng phát.")

    # --- nghe luồng giọng clone: bấm là nghe, máy đọc sinh trước phía sau trong lúc phát ---
    def _tam_dung_tiep(self):
        p = getattr(self, "_phat_luong", None)
        if p is None or not p.dang_chay:
            return
        if p.dang_tam_dung:
            p.tiep()
            self.nut_tam.config(text="⏸ Tạm dừng")
        else:
            p.tam_dung()
            self.nut_tam.config(text="▶ Tiếp")

    def _may_cho_chia(self):
        return gc.MayDocVoxCPM if self.may_doc.get() == gc.MayDocVoxCPM.ten else mn.MayDocNhe

    def _tim_vi_tri_phan(self, phan):
        """Vị trí ký tự bắt đầu của từng phần trong ô chữ (để tô câu đang nghe và bấm câu nghe từ đó)."""
        txt = self.o_chu.get("1.0", "end-1c")
        ra, tu = [], 0
        for p in phan:
            tu_dau = p["chu"].split()[:6]
            m = re.compile(r"\s+".join(re.escape(t) for t in tu_dau)).search(txt, tu) if tu_dau else None
            if m:
                ra.append((m.start(), m.start() + len(p["chu"])))
                tu = m.start() + 1
            else:
                ra.append(None)
        return ra

    def _nghe_tu_cau(self, event):
        if not self._la_clone() or not getattr(self, "_ds_giong", None):
            return None
        try:
            vt = len(self.o_chu.get("1.0", self.o_chu.index(f"@{event.x},{event.y}")))
        except Exception:
            return None
        return self._nhay_toi(vt)

    def _nhay_toi(self, vt):
        """vt: vị trí ký tự trong ô chữ. Đang nghe thì nhảy, chưa nghe thì bắt đầu nghe từ câu đó."""
        p = getattr(self, "_phat_luong", None)
        doc = getattr(self, "_doc_luong", None)
        if p is not None and p.dang_chay and getattr(self, "_vi_tri_phan", None):
            j = self._phan_tai(vt, self._vi_tri_phan)
            self._do_nhay = {"cau": j, "luc": time.time(), "giay": None, "da_san": j in doc.san if doc else None}
            if doc is not None and self.dang_chay:
                doc.uu_tien = j               # máy đọc sinh câu này trước
            p.phat_tu(j)
            self.nut_tam.config(text="⏸ Tạm dừng")
            return "break"
        if self.dang_chay:
            return None
        t = self.o_chu.get("1.0", "end").strip()
        if not t:
            return None
        t = self._chu_nghe(t)
        phan = gc.chia_theo_may(t, self._may_cho_chia(), self.cam_xuc.get() or gc.MUC_MAC_DINH)
        j = self._phan_tai(vt, self._tim_vi_tri_phan(phan))
        self._chay(t, self._ten_ra_mac_dinh(), luong=True, bat_dau=j)
        return "break"

    def _chu_nghe(self, t):
        """Kịch bản YouTube (có ghi chú [B-roll], tiêu đề ## ...) thì bỏ phần không đọc, giữ chapters."""
        vb, chuong = tach_chuong(t)
        if vb and vb != t:
            self._chuong_luot = chuong
            return vb
        return t

    @staticmethod
    def _phan_tai(vt, vi_tri):
        j = 0
        for k, v in enumerate(vi_tri):
            if v is not None and v[0] <= vt:
                j = k
        return j

    def _lam_nong(self):
        """Nạp sẵn máy đọc nhẹ và dấu giọng ngay khi chọn giọng clone, để bấm Nghe là ra tiếng ngay."""
        if getattr(self, "_dang_nong", False) or self.may_doc.get() == gc.MayDocVoxCPM.ten:
            return
        try:
            slug = self._tham_so()[0][len(gc.TIEN_TO_CLONE):]
        except Exception:
            return
        if not slug or not mn.da_cai():
            return
        self._dang_nong = True

        def lam():
            try:
                if getattr(self, "_may_nhe", None) is None:
                    self._may_nhe = mn.MayDocNhe()
                m = self._may_nhe
                m.bat()
                with m._khoa:
                    m.se_dich(gc.duong_mau(slug))
            except Exception:
                pass
            finally:
                self._dang_nong = False
        threading.Thread(target=lam, daemon=True).start()

    def _lay_phan_luong(self, i):
        doc = getattr(self, "_doc_luong", None)
        if doc is None or i not in doc.san:
            return None
        try:
            x, sr = gc.doc_wav(os.path.join(doc.viec, f"phan_{i:05d}.wav"))
        except Exception:
            return None
        y = gc.lam_mem_mep(gc.cat_lang(x, sr), sr)
        if self._tang_luong is None and len(y):        # một mức tăng cố định cho cả bài, đặt theo câu đầu
            rms = float(np.sqrt(np.mean(y ** 2)) + 1e-9)
            self._tang_luong = max(0.5, min(4.0, 0.1 / rms))
        y = y * (self._tang_luong or 1.0)
        lang = np.zeros(int(sr * gc.lang_sau(doc.phan[i]["ngat"])), dtype=np.float32)
        return np.clip(np.concatenate([y.astype(np.float32), lang]), -0.98, 0.98), sr

    def _khi_doi_phan_luong(self, i):
        doc = getattr(self, "_doc_luong", None)
        if doc is None:
            return
        nh = getattr(self, "_do_nhay", None)
        if nh and nh.get("cau") == i and nh.get("giay") is None:
            nh["giay"] = round(time.time() - nh["luc"], 2)
        n = len(doc.phan)
        self.trang_thai.set(f"Đang nghe câu {i + 1}/{n} · đã đọc sẵn {len(doc.san)}/{n} · bấm đúp câu khác để nghe từ đó")
        self.tien_do["value"] = (i + 1) / n * 100
        self._xoa_highlight()
        vt = (getattr(self, "_vi_tri_phan", None) or [None] * n)[i] if i < n else None
        if vt:
            a, b = f"1.0+{vt[0]}c", f"1.0+{vt[1]}c"
            self.o_chu.tag_add("dang_doc", a, b)
            if self.cd.get("tu_cuon", True):
                self.o_chu.see(a)
            self._to_dich_theo(vt[0])
        p = getattr(self, "_phat_luong", None)
        if p is not None and i in p.lech_doi_phan:      # trễ tô = trễ phát hiện + trễ giao diện
            lech, luc = p.lech_doi_phan[i]
            self._lech_to_luong.append(round(lech + (time.time() - luc), 3))

    def _khi_dem_luong(self, dang):
        """Máy đọc chậm hơn tiếng phát (máy bận): bộ phát gom sẵn vài câu rồi mới phát để khỏi giật."""
        if dang and getattr(self, "_phat_luong", None) is not None:
            self.trang_thai.set("Máy đang bận: đang đọc sẵn vài câu rồi mới phát để khỏi bị giật...")

    def _het_luong(self, p):
        if getattr(self, "_phat_luong", None) is p:
            self._thong_ke_luong = self._so_luong(p)
            self._phat_luong = None
            self.nut_tam.config(text="⏸ Tạm dừng")
            self._xoa_highlight()
            if p.loi:
                self.trang_thai.set(p.loi)

    def _so_luong(self, p):
        t = getattr(self, "_t_bam_luong", None)
        lt = getattr(self, "_lech_to_luong", []) or [0.0]
        return {"giay_ra_tieng_dau": round(p.t_ra_loa - t, 2) if (p.t_ra_loa and t) else None,
                "lech_to_cau_giay": {"so": len(lt), "tb": round(sum(lt) / len(lt), 3), "max": max(lt)},
                "so_lan_vap": p.so_lan_vap, "giay_vap": round(p.giay_vap, 2),
                "so_lan_dem": p.so_lan_dem, "giay_dem": round(p.giay_dem, 2),
                "giay_da_phat": round(p.giay_da_phat, 1), "loi_phat": p.loi}

    def _ten_ra_mac_dinh(self):
        ma = self._tham_so()[0]
        khong_dau = {"Trung tính": "TrungTinh", "Kể chuyện": "KeChuyen", "Hào hứng": "HaoHung", gc.MUC_TU_DONG: "TuDong"}
        giong = ma[len(gc.TIEN_TO_CLONE):].title() + "-" + khong_dau.get(self.cam_xuc.get(), "Clone")
        return os.path.join(self.thu_muc_ra, f"{time.strftime('%Y%m%d-%H%M%S')}-{giong}.mp3")

    def _phat_lai(self):
        if self.file_cuoi and os.path.exists(self.file_cuoi):
            self.trang_thai.set(f"Đang phát lại: {os.path.basename(self.file_cuoi)}")
            self._bat_dau_phat(self.file_cuoi, getattr(self, "_danh_sach_cau", None))

    def _nghe_thu(self):
        t = self.o_chu.get("1.0", "end").strip()
        if not t or not self._san_sang():
            return
        if self._la_clone():
            self._chay(self._chu_nghe(t), self._ten_ra_mac_dinh(), luong=True)
            return
        doan = (chia_doan(t, 300) or [""])[0]
        self._chay(doan, os.path.join(self.thu_muc_ra, "_nghe_thu.mp3"), nghe_thu=True)

    def _chuyen(self, duong_ra=None):
        t = self.o_chu.get("1.0", "end").strip()
        if not t:
            messagebox.showinfo("Đọc Voice", "Chưa có văn bản bên bảng đọc.")
            return
        if not self._san_sang():
            return
        if self.dang_chay and getattr(self, "_luot_luong", False):
            self.trang_thai.set("Đang đọc để nghe: đọc xong sẽ tự xuất mp3 + srt vào thư mục Đã chuyển đổi.")
            return
        ma = self._tham_so()[0]
        if ma.startswith(gc.TIEN_TO_CLONE):
            khong_dau = {"Trung tính": "TrungTinh", "Kể chuyện": "KeChuyen", "Hào hứng": "HaoHung", gc.MUC_TU_DONG: "TuDong"}
            giong = ma[len(gc.TIEN_TO_CLONE):].title() + "-" + khong_dau.get(self.cam_xuc.get(), "Clone")
        else:
            giong = ma.split("-")[2].replace("MultilingualNeural", "").replace("Neural", "")
        mac_dinh = f"{time.strftime('%Y%m%d-%H%M%S')}-{giong}.mp3"
        p = duong_ra or filedialog.asksaveasfilename(initialdir=self.thu_muc_ra, initialfile=mac_dinh,
                                                     defaultextension=".mp3", filetypes=[("MP3", "*.mp3")])
        if p:
            # giọng clone: cùng chữ đã lọc như Nghe để dùng lại các câu đã đọc (không đọc lại)
            self._chay(self._chu_nghe(t) if ma.startswith(gc.TIEN_TO_CLONE) else t, p)

    def _highlight_doan_dang_chuyen(self, text_doan):
        self._xoa_highlight()
        dau = text_doan[:min(40, len(text_doan))].strip()
        if dau:
            pos = self.o_chu.search(dau, "1.0", tk.END)
            if pos:
                end_pos = f"{pos}+{len(text_doan)}c"
                self.o_chu.tag_add("dang_doc", pos, end_pos)
                if self.cd.get("tu_cuon", True):
                    self.o_chu.see(pos)

    def _chay(self, van_ban, duong_ra, nghe_thu=False, luong=False, bat_dau=0):
        with self._khoa_trang_thai:
            if self.dang_chay:
                return
            self.dang_chay = True
        self._t_bam_luong = time.time()
        self._dung_phat_app()
        self._luot_luong = luong
        self.nut_chuyen.state(["disabled"])
        self.nut_thu.state(["disabled"])
        giong, toc, cao = self._tham_so()
        self._luu_cai_dat()
        td = doc_tu_dien()
        self._tu_dien_luot = (ban_do_cau(van_ban, td), tu_dien_cho_srt(van_ban, td))
        if giong.startswith(gc.TIEN_TO_CLONE):
            self._dung_clone.clear()
            threading.Thread(target=self._lam_clone, args=(van_ban, td, duong_ra, giong, nghe_thu, luong, bat_dau),
                             daemon=True).start()
            return
        van_ban_doc = re.sub(r"\[[^\]\n]{2,20}\]", lambda m: "" if gc.la_the_sac_thai(m.group(0)) else m.group(0),
                             ap_tu_dien(van_ban, td))
        threading.Thread(target=self._lam, args=(van_ban_doc, duong_ra, giong, toc, cao, nghe_thu),
                         daemon=True).start()

    def _dung_chuyen(self):
        if self.dang_chay and self._la_clone():
            self._dung_clone.set()
            self.trang_thai.set("Sẽ dừng sau câu đang đọc. Bấm Chuyển đổi lại cùng văn bản để chạy tiếp.")

    def _lam_clone(self, van_ban, td, duong_ra, giong, nghe_thu, luong=False, bat_dau=0):
        """Đọc bằng giọng clone: từng câu qua máy đọc, lưu tạm để chạy tiếp, ghép, chuẩn âm lượng.
        luong=True: phát ngay câu đầu tiên trong lúc sinh tiếp phía sau, đọc xong tự xuất mp3 + srt."""
        slug = giong[len(gc.TIEN_TO_CLONE):]
        muc = self.cam_xuc.get() or gc.MUC_MAC_DINH
        kiem = None
        t_bat_dau = time.time()
        ket = {"ok": False}
        try:
            os.makedirs(THU_MUC_CLONE_TAM, exist_ok=True)
            if not slug or not os.path.isfile(gc.duong_mau(slug)):
                raise gc.LoiClone("Chưa có giọng clone. Bấm Nhập giọng (.dgvoice) để thêm giọng (ví dụ Arthur.dgvoice).")
            may = self._chon_may_doc()
            so_tu = len(van_ban.split())
            phut = phut_moi_1000_tu(may.ten) * so_tu / 1000
            canh_bao = canh_bao_may_yeu(may.ten)
            self.after(0, self._bao, f"Giọng {slug.title()} · {may.ten}: {so_tu} từ, ước khoảng {max(1, round(phut))} phút..."
                       + (f" {canh_bao}" if canh_bao else ""), 0)
            if self.cd.get("kiem_whisper", True) and not nghe_thu and not luong:
                self.after(0, self._bao, "Đang bật bộ kiểm đọc (whisper)...", 0)
                py = gc.tim_python_whisper(self.cd.get("python_kiem", ""))
                kiem = gc.KiemDoc(py, THU_MUC_CLONE_TAM) if py else None
            toc = gc.MUC_CAM_XUC.get(muc, {}).get("toc", 1.0) * (1 + self.toc_do.get() / 100.0)
            bao_sinh = (lambda c, pt: None) if luong else (lambda c, pt: self.after(0, self._bao, c, pt))
            doc = gc.DocDaiClone(THU_MUC_CLONE_TAM, may, kiem, bao=bao_sinh, dung=self._dung_clone)
            phat_cb = None
            if luong:
                self._doc_luong = doc
                self._tang_luong = None
                self._lech_to_luong = []

                def phat_cb(su_kien, **kw):
                    if su_kien != "phan":
                        return
                    im = bool(os.environ.get("DOCVOICE_IM")) and che_do_thu()   # tự thử: đo đúng nhịp, không phát ra loa
                    p = pl.PhatLuong(len(kw["phan"]), self._lay_phan_luong, im_lang=im,
                                     khi_doi_phan=lambda i: self.after(0, self._khi_doi_phan_luong, i),
                                     do_rtf=lambda doc=doc: self._rtf(doc),
                                     khi_dem=lambda dang: self.after(0, self._khi_dem_luong, dang))
                    p.khi_het = lambda p=p: self.after(0, self._het_luong, p)
                    self._phat_luong = p
                    self._vi_tri_phan = None
                    self.after(0, lambda ph=kw["phan"]: setattr(self, "_vi_tri_phan", self._tim_vi_tri_phan(ph)))
                    p.phat_tu(bat_dau)
                self.after(0, self._bao, f"{may.ten}: đang đọc câu đầu, sắp có tiếng...", 0)
            wav_ra = os.path.splitext(duong_ra)[0] + ".wav" if (self.cd.get("xuat_wav") and not nghe_thu) else ""
            kq = doc.chay(van_ban, slug, muc, duong_ra, wav_ra,
                          "" if nghe_thu else os.path.splitext(duong_ra)[0] + ".srt",
                          kieu_shorts=self.cd.get("kieu_shorts", False),
                          chia_shorts=lambda c: chia_cau_shorts(c, max_tu=7),
                          ap_cach_doc=lambda c: ap_tu_dien(c, td), toc=max(0.5, min(2.0, toc)),
                          bat_dau=bat_dau, phat=phat_cb)
            if kq.get("dung") is False and getattr(self, "_chuong_luot", None) and not nghe_thu:
                with open(os.path.splitext(duong_ra)[0] + ".chapters.txt", "w", encoding="utf-8") as f:
                    f.write(chapters_youtube(self._chuong_luot, kq["muc_tg"]) + "\n")
            if kq.get("dung"):
                self.after(0, self._bao, f"Đã dừng ở {kq['xong']}/{kq['tong']} câu. Bấm Chuyển đổi lại "
                                         "cùng văn bản và cùng mức cảm xúc để chạy tiếp.", kq["xong"] / kq["tong"] * 95)
                ket = {"ok": False, "dung": True, **kq}
                return
            self.file_cuoi = duong_ra
            cau_list = [(bd, kt, chu) for bd, kt, chu in kq["muc_tg"]]
            self._danh_sach_cau = cau_list
            if luong:
                self._file_luong = duong_ra
                if getattr(self, "_phat_luong", None) is None:
                    self.after(0, self._bao, f"Đã xuất sẵn: {duong_ra}", 100)
            else:
                self.after(0, self._bao, "Đang phát thử..." if nghe_thu else f"Xong: {duong_ra}", 100)
            if not self._tu_dong and not luong:
                self.after(0, lambda: self._bat_dau_phat(duong_ra, cau_list))
            ket = {"ok": True, "mp3": duong_ra, "srt": kq.get("srt"), "giay": kq.get("giay"),
                   "tong": kq.get("tong"), "wer_tb_tung_phan": kq.get("wer_tb_tung_phan"),
                   "may_doc": may.ten, "giay_chay": round(time.time() - t_bat_dau, 1),
                   "rtf_sinh": self._rtf(doc) if luong else None, "luong": luong,
                   "chapters": os.path.splitext(duong_ra)[0] + ".chapters.txt" if getattr(self, "_chuong_luot", None) else ""}
        except Exception as e:
            ket = {"ok": False, "loi": str(e)}
            self.after(0, self._bao, f"Lỗi: {e}", 0)
            if not self._tu_dong:
                self.after(0, lambda e=e: messagebox.showerror("Đọc Voice", f"Không chuyển được giọng clone:\n{e}"))
        finally:
            if kiem:
                kiem.dong()
            self._ket_qua_luot = ket
            self._chuong_luot = None
            self.after(0, self._xong)

    @staticmethod
    def _rtf(doc):
        """Hệ số thời gian thực của máy đọc: giây sinh / giây âm thanh, trên các phần sinh mới lượt này."""
        ds = [v for v in list((getattr(doc, "so", {}) or {}).get("phan", {}).values()) if v.get("giay_sinh") is not None]
        am = sum(v.get("giay", 0) for v in ds)
        return round(sum(v["giay_sinh"] for v in ds) / am, 3) if am else None

    # --- máy đọc giọng clone ---
    def _studio_tat(self):
        return bool(os.environ.get("DOCVOICE_KHONG_STUDIO")) and che_do_thu()

    def _chon_may_doc(self):
        """Chuẩn cao (GPU): VoxCPM2 của DG Voice Studio, cần GPU NVIDIA. Nhẹ (mọi máy): Edge + đổi chất
        giọng OpenVoice trên CPU; lần đầu tự tải model (131 MB) và ffmpeg nếu máy chưa có."""
        if self.may_doc.get() == gc.MayDocVoxCPM.ten:
            if not co_nvidia():
                raise gc.LoiClone("Máy không có GPU NVIDIA nên không chạy được Chuẩn cao (GPU). Chọn Máy đọc: "
                                  "Nhẹ (mọi máy).")
            may = gc.MayDocVoxCPM(self.cd.get("thu_muc_studio", r"E:\DG Voice Studio"))
            if self._studio_tat() or (not may.co_san() and not may.trang_thai().get("loaded")):
                raise gc.LoiClone("Chuẩn cao (GPU) cần DG Voice Studio (máy đọc VoxCPM2) trên máy này. Chọn Máy "
                                  "đọc: Nhẹ (mọi máy).")
            return may
        if not mn.da_cai():
            self._cai_may_nhe()
        if getattr(self, "_may_nhe", None) is None:
            self._may_nhe = mn.MayDocNhe()
        return self._may_nhe

    def _cho_nghe_xong(self, bat_dau_cho=None):
        """Chế độ tự thử có "nghe": đọc xong vẫn chờ phát tới hết (hoặc đủ nghe_giay) rồi mới ghi kết quả."""
        kb = self._tu_dong or {}
        bat_dau_cho = bat_dau_cho or time.time()
        p = getattr(self, "_phat_luong", None)
        da_nghe = (p.giay_da_phat if p else 0)
        if p is not None and p.dang_chay and da_nghe < kb.get("nghe_giay", 300) and time.time() - bat_dau_cho < 3600:
            self.after(500, lambda: self._cho_nghe_xong(bat_dau_cho))
            return
        if p is not None:
            self._thong_ke_luong = self._so_luong(p)
            p.dung()
            self._phat_luong = None
        self._ket_thuc_tu_dong()

    def _cai_may_nhe(self):
        def bao(viec, da, tong):
            pt = da / tong * 100 if tong else 0
            self.after(0, self._bao, f"{viec}: {da / 1e6:.0f}/{tong / 1e6:.0f} MB (một lần, tải tiếp được nếu đứt)", pt)
        ok = mn.cai_dat(bao=bao, dung=self._dung_clone)
        if not ok:
            raise gc.LoiClone("Đã dừng tải máy đọc nhẹ. Bấm lại để tải tiếp.")
        self.after(0, self._cap_nhat_nhan_may)

    def _cap_nhat_nhan_may(self):
        try:
            if mn.da_cai():
                chu = f"Máy đọc nhẹ: đã cài ({mn.dung_luong_cai() / 1e6:.0f} MB)"
            else:
                chu = "Máy đọc nhẹ: chưa cài, lần đọc đầu tự tải khoảng 131 MB"
            chu += " · GPU NVIDIA: " + ("có" if co_nvidia() else "không")
            self.nhan_may.config(text=chu)
        except Exception:
            pass

    def _nhap_giong(self, duong=None):
        p = duong or filedialog.askopenfilename(title="Chọn gói giọng", filetypes=[("Gói giọng Đọc Voice", "*.dgvoice")])
        if not p:
            return None
        try:
            tt = gc.nhap_goi_giong(p)
        except Exception as e:
            if not self._tu_dong:
                messagebox.showerror("Đọc Voice", f"Không nhập được gói giọng:\n{e}")
            self.trang_thai.set(f"Không nhập được gói giọng: {e}")
            return None
        self.cd["giong"] = gc.TIEN_TO_CLONE + tt["slug"]
        self.cd["ngon_ngu"] = MA_CLONE
        self.cd["da_gioi_thieu_clone"] = True      # người dùng đã tự có giọng: lần mở sau không tự ép giọng nào nữa
        if getattr(self, "_locales", None) and MA_CLONE in self._locales:
            self.ngon_ngu.current(self._locales.index(MA_CLONE))
            self._nap_giong()
        self._luu_cai_dat()
        self.trang_thai.set(f"Đã nhập giọng {tt['ten']}. Quyền dùng: {tt.get('quyen_dung') or 'không ghi'}")
        return tt

    def _xuat_giong(self):
        if not self._la_clone() or not gc.ds_giong():
            messagebox.showinfo("Đọc Voice", "Chọn một giọng clone trước khi xuất.")
            return
        slug = self._tham_so()[0][len(gc.TIEN_TO_CLONE):]
        tt = gc.ds_giong()[slug]
        p = filedialog.asksaveasfilename(initialfile=f"{tt['ten'].split(' (')[0]}.dgvoice", defaultextension=".dgvoice",
                                         filetypes=[("Gói giọng Đọc Voice", "*.dgvoice")])
        if p:
            try:
                gc.xuat_goi_giong(slug, p)
            except gc.LoiClone as e:
                messagebox.showinfo("Đọc Voice", str(e))
                return
            self.trang_thai.set(f"Đã xuất {p}. Lưu ý quyền dùng: {tt.get('quyen_dung') or 'không ghi'}")

    def _doc_kich_ban_youtube(self, duong_ra=None):
        """Một nút: lọc kịch bản, đọc bằng giọng clone (Arthur), xuất mp3 -14 LUFS + srt + chapters."""
        t = self.o_chu.get("1.0", "end").strip()
        if not t:
            messagebox.showinfo("Đọc Voice", "Dán kịch bản YouTube vào bảng đọc trước.")
            return
        if not self._san_sang():
            return
        if not self._la_clone():
            if MA_CLONE in self._locales:
                self.ngon_ngu.current(self._locales.index(MA_CLONE))
                self._nap_giong()
        van_ban, chuong = tach_chuong(t)
        if not van_ban:
            messagebox.showinfo("Đọc Voice", "Kịch bản không còn chữ nào để đọc sau khi lọc.")
            return
        self._chuong_luot = chuong
        slug = self._tham_so()[0][len(gc.TIEN_TO_CLONE):] or "clone"
        ten = f"{time.strftime('%Y%m%d-%H%M%S')}-{slug.title()}-YouTube.mp3"
        self._chay(van_ban, duong_ra or os.path.join(self.thu_muc_ra, ten), luong=True)

    def _lam(self, van_ban, duong_ra, giong, toc, cao, nghe_thu):
        doan = [d for d in chia_doan(van_ban) if re.search(r"\w", d)]
        xong = [0]
        if not doan:
            self.after(0, self._bao, "Không có chữ nào để đọc", 0)
            self.after(0, lambda: messagebox.showwarning("Đọc Voice", "Văn bản không có chữ nào để đọc."))
            self.after(0, self._xong)
            return

        async def tat_ca():
            so_ss = self.cd.get("song_song", SONG_SONG)
            khoa = asyncio.Semaphore(so_ss)

            async def mot(d):
                async with khoa:
                    self.after(0, lambda d=d: self._highlight_doan_dang_chuyen(d))
                    b = await tao_doan(d, giong, toc, cao)
                xong[0] += 1
                self.after(0, self._bao, f"Đã chuyển {xong[0]}/{len(doan)} đoạn...", xong[0] / len(doan) * 100)
                return b
            return await asyncio.gather(*(mot(d) for d in doan))

        try:
            self.after(0, self._bao, f"Đang chuyển {len(doan)} đoạn bằng giọng Edge {giong}...", 0)
            ket_qua = asyncio.run(tat_ca())
            tam = duong_ra + ".part"
            with open(tam, "wb") as f:
                for b, _ in ket_qua:
                    f.write(b)
            os.replace(tam, duong_ra)
            if not nghe_thu:
                kieu_shorts = self.cd.get("kieu_shorts", False)
                with open(os.path.splitext(duong_ra)[0] + ".srt", "w", encoding="utf-8") as f:
                    f.write(tao_srt(ket_qua, self._tu_dien_luot, kieu_shorts=kieu_shorts))
            self.file_cuoi = duong_ra
            cau_list = lay_danh_sach_cau(ket_qua, self._tu_dien_luot)
            self._danh_sach_cau = cau_list
            self.after(0, self._bao, "Đang phát thử..." if nghe_thu else f"Xong: {duong_ra}", 100)
            self._ket_qua_luot = {"ok": True, "mp3": duong_ra, "so_cau": len(cau_list)}
            if not self._tu_dong:
                self.after(0, lambda: self._bat_dau_phat(duong_ra, cau_list))
        except Exception as e:
            self._ket_qua_luot = {"ok": False, "loi": str(e)}
            self.after(0, self._bao, f"Lỗi: {e}", 0)
            self.after(0, lambda e=e: messagebox.showerror("Đọc Voice", f"Không chuyển được:\n{e}"))
        finally:
            self.after(0, self._xong)

    def _tu_dien(self):
        cua = tk.Toplevel(self)
        cua.title("Từ điển cách đọc")
        cua.geometry("560x460")
        ttk.Label(cua, padding=(12, 10, 12, 4), wraplength=520, text=(
            "Mỗi dòng một từ, dạng:  từ = cách đọc\n"
            "Ví dụ:  AI = ây ai   ·   TP.HCM = thành phố Hồ Chí Minh\n"
            "App thay trước khi đọc, phân biệt hoa thường (AI khác Ai).")).pack(anchor="w")
        o = tk.Text(cua, font=("Consolas", 11), relief="solid", borderwidth=1, padx=8, pady=6, undo=True)
        o.pack(fill="both", expand=True, padx=12)
        try:
            with open(FILE_TU_DIEN, encoding="utf-8") as f:
                o.insert("1.0", f.read())
        except FileNotFoundError:
            pass

        def luu():
            with open(FILE_TU_DIEN, "w", encoding="utf-8") as f:
                f.write(o.get("1.0", "end").rstrip() + "\n")
            self.trang_thai.set(f"Đã lưu từ điển: {len(doc_tu_dien())} từ")
            cua.destroy()
        ttk.Button(cua, text="Lưu", style="Xanh.TButton", command=luu).pack(anchor="e", padx=12, pady=10)

    def _kiem_cap_nhat(self, chu_dong):
        try:
            moi = kiem_ban_moi()
        except Exception as e:
            if chu_dong:
                self.after(0, lambda e=e: messagebox.showwarning("Đọc Voice", f"Không kiểm được bản mới:\n{e}"))
            return
        if not moi:
            if chu_dong:
                self.after(0, lambda: messagebox.showinfo("Đọc Voice", f"Đang dùng bản mới nhất (v{PHIEN_BAN})."))
            return
        self.after(0, self._hoi_cap_nhat, *moi)

    def _hoi_cap_nhat(self, ban, asset):
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Đọc Voice", f"Có bản v{ban}. Bản chạy bằng Python thì cập nhật bằng git pull.")
            return
        with self._khoa_trang_thai:
            if self.dang_chay:
                self._ban_moi_cho = (ban, asset)
                self.trang_thai.set(f"Đã có bản v{ban} (sẽ hỏi sau khi chạy xong voice).")
                return
        if not messagebox.askyesno("Đọc Voice", f"Có bản mới v{ban} (đang dùng v{PHIEN_BAN}).\nCập nhật ngay?"):
            return
        self.trang_thai.set(f"Đang tải bản v{ban}...")
        with self._khoa_trang_thai:
            self.dang_chay = True
        self.nut_chuyen.state(["disabled"])
        self.nut_thu.state(["disabled"])

        def lam():
            try:
                thay_exe(asset)
                self.after(0, self._thoat)
            except Exception as e:
                self.after(0, self._xong)
                self.after(0, self._bao, f"Cập nhật lỗi: {e}", 0)
                self.after(0, lambda e=e: messagebox.showerror(
                    "Đọc Voice", f"Không tự cập nhật được, app cũ vẫn dùng bình thường:\n{e}"
                    f"\n\nTải tay tại github.com/{KHO_GITHUB}/releases"))
        threading.Thread(target=lam, daemon=True).start()

    def _bao(self, chu, phan_tram):
        self.trang_thai.set(chu)
        self.tien_do["value"] = phan_tram
        nk = (self._tu_dong or {}).get("nhat_ky")
        if nk and chu != getattr(self, "_bao_cuoi", None):     # chế độ tự thử: ghi nhật ký từng bước
            self._bao_cuoi = chu
            try:
                with open(nk, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {phan_tram:5.1f}% {chu}\n")
            except OSError:
                pass

    def _xong(self):
        cho_xuly = None
        with self._khoa_trang_thai:
            self.dang_chay = False
            if self._ban_moi_cho:
                cho_xuly = self._ban_moi_cho
                self._ban_moi_cho = None
        self.nut_chuyen.state(["!disabled"])
        self.nut_thu.state(["!disabled"])
        if self._tu_dong:
            kb = self._tu_dong or {}
            if kb.get("phat_lai_giay") and self.file_cuoi and not getattr(self, "_da_phat_lai", False):
                # tự thử: phát lại file vừa xuất qua MCI để đo độ trễ tô câu Anh, Việt so với vị trí phát thật
                self._da_phat_lai = True
                self._bat_dau_phat(self.file_cuoi, self._danh_sach_cau)
                if os.environ.get("DOCVOICE_IM"):
                    mci("setaudio docvoice volume to 0")
                self.after(int(kb["phat_lai_giay"] * 1000), self._xong)
                return
            if kb.get("nghe_giay") and getattr(self, "_phat_luong", None) is not None:
                self.after(300, self._cho_nghe_xong)
            else:
                self.after(300, self._ket_thuc_tu_dong)
            return
        if cho_xuly:
            ban, asset = cho_xuly
            self.after(500, lambda: self._hoi_cap_nhat(ban, asset))

    def _cai_dat(self):
        cua = tk.Toplevel(self)
        cua.title("⚙ Cài đặt Đọc Voice")
        cua.geometry("580x640")
        cua.resizable(False, False)
        cua.transient(self)
        cua.grab_set()

        khung = ttk.Frame(cua, padding=16)
        khung.pack(fill="both", expand=True)

        ttk.Label(khung, text="Cài đặt hệ thống", font=("Segoe UI Semibold", 13)).pack(anchor="w", pady=(0, 12))

        # 1. Thư mục xuất file
        f_tm = ttk.LabelFrame(khung, text=" Thư mục lưu file xuất (MP3, SRT) ", padding=10)
        f_tm.pack(fill="x", pady=(0, 10))
        var_tm = tk.StringVar(value=self.thu_muc_ra)
        hang_tm = ttk.Frame(f_tm)
        hang_tm.pack(fill="x")
        o_tm = ttk.Entry(hang_tm, textvariable=var_tm)
        o_tm.pack(side="left", fill="x", expand=True, padx=(0, 8))
        def chon_tm():
            p = filedialog.askdirectory(initialdir=var_tm.get())
            if p:
                var_tm.set(os.path.abspath(p))
        ttk.Button(hang_tm, text="Duyệt...", command=chon_tm).pack(side="right")

        # 2. YouTube & Phụ đề
        f_yt = ttk.LabelFrame(khung, text=" Hỗ trợ kịch bản & Phụ đề YouTube ", padding=10)
        f_yt.pack(fill="x", pady=(0, 10))
        var_shorts = tk.BooleanVar(value=self.cd.get("kieu_shorts", False))
        ttk.Checkbutton(f_yt, text="Xuất phụ đề SRT kiểu Shorts / TikTok (cắt ngắn 6:7 từ mỗi dòng)",
                        variable=var_shorts).pack(anchor="w", pady=(0, 4))
        var_cuon = tk.BooleanVar(value=self.cd.get("tu_cuon", True))
        ttk.Checkbutton(f_yt, text="Tự động cuộn văn bản theo câu đang đọc (Karaoke Auto-scroll)",
                        variable=var_cuon).pack(anchor="w")

        # 3. Giọng clone
        f_cl = ttk.LabelFrame(khung, text=" Giọng clone: chế độ Chuẩn cao (GPU) dùng DG Voice Studio ở thư mục này ", padding=10)
        f_cl.pack(fill="x", pady=(0, 10))
        var_studio = tk.StringVar(value=self.cd.get("thu_muc_studio", r"E:\DG Voice Studio"))
        hang_st = ttk.Frame(f_cl)
        hang_st.pack(fill="x", pady=(0, 4))
        ttk.Entry(hang_st, textvariable=var_studio).pack(side="left", fill="x", expand=True, padx=(0, 8))
        ttk.Button(hang_st, text="Duyệt...", command=lambda: var_studio.set(
            os.path.abspath(filedialog.askdirectory(initialdir=var_studio.get()) or var_studio.get()))).pack(side="right")
        var_kiem = tk.BooleanVar(value=self.cd.get("kiem_whisper", True))
        ttk.Checkbutton(f_cl, text="Kiểm lại từng câu bằng whisper, đọc lệch thì tự đọc lại câu đó",
                        variable=var_kiem).pack(anchor="w")
        var_wav = tk.BooleanVar(value=self.cd.get("xuat_wav", False))
        ttk.Checkbutton(f_cl, text="Xuất thêm file WAV 48 kHz (bên cạnh MP3 320 kbps)",
                        variable=var_wav).pack(anchor="w")

        # 4. Khởi động & Cập nhật
        f_kd = ttk.LabelFrame(khung, text=" Cập nhật & Hiệu năng ", padding=10)
        f_kd.pack(fill="x", pady=(0, 14))
        var_upd = tk.BooleanVar(value=self.cd.get("tu_kiem_cap_nhat", True))
        ttk.Checkbutton(f_kd, text="Tự động kiểm tra bản cập nhật mới khi mở app",
                        variable=var_upd).pack(anchor="w", pady=(0, 6))

        hang_ss = ttk.Frame(f_kd)
        hang_ss.pack(fill="x")
        ttk.Label(hang_ss, text="Số đoạn gửi máy chủ cùng lúc:").pack(side="left")
        var_ss = tk.IntVar(value=self.cd.get("song_song", SONG_SONG))
        cb_ss = ttk.Combobox(hang_ss, values=[2, 3, 4, 5, 6], textvariable=var_ss, state="readonly", width=6)
        cb_ss.pack(side="left", padx=8)
        ttk.Label(hang_ss, text="(Mặc định 3 đoạn)").pack(side="left")

        # Nút lưu & hủy
        hang_nut = ttk.Frame(khung)
        hang_nut.pack(fill="x", side="bottom")

        def luu():
            tm_moi = var_tm.get().strip()
            if tm_moi:
                try:
                    os.makedirs(tm_moi, exist_ok=True)
                    te_tmp = os.path.join(tm_moi, f".test_ghi_{os.getpid()}.tmp")
                    with open(te_tmp, "w", encoding="utf-8") as f:
                        f.write("ok")
                    if os.path.exists(te_tmp):
                        os.remove(te_tmp)
                    self.thu_muc_ra = tm_moi
                except Exception as e:
                    messagebox.showerror("Cài đặt", f"Thư mục không có quyền ghi dữ liệu:\n{e}", parent=cua)
                    return
            self.cd["thu_muc_ra"] = self.thu_muc_ra
            self.cd["kieu_shorts"] = var_shorts.get()
            self.cd["tu_cuon"] = var_cuon.get()
            self.cd["tu_kiem_cap_nhat"] = var_upd.get()
            self.cd["thu_muc_studio"] = var_studio.get().strip() or r"E:\DG Voice Studio"
            self.cd["kiem_whisper"] = var_kiem.get()
            self.cd["xuat_wav"] = var_wav.get()
            try:
                self.cd["song_song"] = int(var_ss.get())
            except Exception:
                self.cd["song_song"] = SONG_SONG
            self._luu_cai_dat()
            self.trang_thai.set("Đã lưu cấu hình cài đặt.")
            cua.destroy()

        ttk.Button(hang_nut, text="Lưu cài đặt", style="Xanh.TButton", command=luu).pack(side="right")
        ttk.Button(hang_nut, text="Đóng", command=cua.destroy).pack(side="right", padx=(0, 8))

    def _luu_cai_dat(self):
        """Ghi cai_dat.json. Gọi ngay khi người dùng đổi hoặc nhập giọng (không chỉ lúc thoát) để tắt đột ngột không mất lựa chọn."""
        try:
            self.cd.update({
                "ngon_ngu": self._locales[self.ngon_ngu.current()],
                "giong": self._ds_giong[self.chon_giong.current()],
                "cao_do": self.cao_do.get(), "toc_do": self.toc_do.get(),
                "van_ban": self.o_chu.get("1.0", "end").strip()[:50000],
                "ban_dich": self.o_dich.get("1.0", "end").strip()[:50000],
                "thu_muc_ra": self.thu_muc_ra,
                "kieu_shorts": self.cd.get("kieu_shorts", False),
                "tu_cuon": self.cd.get("tu_cuon", True),
                "tu_kiem_cap_nhat": self.cd.get("tu_kiem_cap_nhat", True),
                "song_song": self.cd.get("song_song", SONG_SONG),
                "cam_xuc": self.cam_xuc.get() or gc.MUC_MAC_DINH,
                "may_doc": self.may_doc.get() or mn.MayDocNhe.ten,
            })
        except Exception:
            pass        # giao diện chưa dựng xong (chưa có danh sách giọng): giữ nguyên các khoá đã có, vẫn ghi phần đã đặt
        try:
            ghi_json(FILE_CAI_DAT, self.cd)
        except Exception:
            pass

    # --- chế độ tự thử: đi đúng đường người dùng (chọn giọng, cảm xúc, dán chữ, bấm Chuyển đổi) ---
    def _chay_tu_dong(self):
        kb = self._tu_dong
        if not getattr(self, "_locales", None):
            self.after(1000, self._chay_tu_dong)
            return
        if kb.get("nhap_giong") and not getattr(self, "_da_nhap_tu_dong", False):
            self._da_nhap_tu_dong = True
            self._nhap_giong(kb["nhap_giong"])
        if kb.get("may_doc"):
            self.may_doc.set(kb["may_doc"])
        loc = kb.get("ngon_ngu", MA_CLONE)
        self.ngon_ngu.current(self._locales.index(loc))
        self._nap_giong()
        if kb.get("giong") in self._ds_giong:
            self.chon_giong.current(self._ds_giong.index(kb["giong"]))
        self._cap_nhat_cam_xuc()
        if kb.get("cam_xuc"):
            self.cam_xuc.set(kb["cam_xuc"])
        if kb.get("van_ban_file"):
            with open(kb["van_ban_file"], encoding="utf-8") as f:
                self.o_chu.delete("1.0", "end")
                self.o_chu.insert("1.0", f.read())
        self.update()
        if kb.get("youtube"):
            bam = lambda: self._doc_kich_ban_youtube(duong_ra=kb.get("ra"))
        elif kb.get("nghe"):
            bam = self._nghe_thu
        else:
            bam = lambda: self._chuyen(duong_ra=kb.get("ra"))
        if kb.get("anh_dang_nghe"):      # chụp lúc đang nghe (tô câu, tiến độ)
            self.after(int(kb.get("giay_anh_dang_nghe", 20) * 1000) + int(kb.get("cho_nong", 0) * 1000),
                       lambda: chup_cua_so(ctypes.windll.user32.GetParent(self.winfo_id()), kb["anh_dang_nghe"]))
        if kb.get("nhay_cau"):           # giả lập bấm đúp vào câu thứ N (đếm từ 1) sau X giây nghe
            nc = kb["nhay_cau"]

            def nhay():
                vt = (getattr(self, "_vi_tri_phan", None) or [])
                k = nc["cau"] - 1
                if k < len(vt) and vt[k]:
                    self._nhay_toi(vt[k][0])
            self.after(int((nc.get("sau_giay", 30) + kb.get("cho_nong", 0)) * 1000), nhay)
        if kb.get("cho_nong"):           # như người dùng mở app vài giây rồi mới bấm
            self._lam_nong()
            self.after(int(kb["cho_nong"] * 1000), bam)
        elif kb.get("anh"):
            self.after(800, lambda: (chup_cua_so(ctypes.windll.user32.GetParent(self.winfo_id()), kb["anh"]), bam()))
        else:
            bam()

    def _ket_thuc_tu_dong(self):
        kb = self._tu_dong or {}
        ket = dict(getattr(self, "_ket_qua_luot", {}) or {})
        if getattr(self, "_phat_luong", None) is not None:
            self._thong_ke_luong = self._so_luong(self._phat_luong)
        if getattr(self, "_thong_ke_luong", None):
            ket["nghe_luong"] = self._thong_ke_luong
        if getattr(self, "_lech_to", None):
            lt = self._lech_to
            ket["lech_to_phat_lai_giay"] = {"so": len(lt), "tb": round(sum(lt) / len(lt), 3), "max": max(lt),
                                            "min": min(lt), "co_cap_dich": bool(getattr(self, "_cap_dich", None))}
        if getattr(self, "_do_nhay", None):
            ket["nhay_cau"] = {k: v for k, v in self._do_nhay.items() if k != "luc"}
        ket.update({"giong": self._tham_so()[0], "cam_xuc": self.cam_xuc.get(), "phien_ban": PHIEN_BAN,
                    "trang_thai_cuoi": self.trang_thai.get()})
        if kb.get("anh_xong"):
            try:
                chup_cua_so(ctypes.windll.user32.GetParent(self.winfo_id()), kb["anh_xong"])
            except Exception:
                pass
        if kb.get("ket_qua"):
            ghi_json(kb["ket_qua"], ket)
        self.destroy()

    def _thoat(self):
        if hasattr(self, "_ds_giong"):
            self._luu_cai_dat()
        self._dung_phat_app()
        self.destroy()


def tu_thu(nhat_ky):
    """Chạy không giao diện để kiểm bản exe: tạo giọng, kiểm và cài bản mới nếu có."""
    with open(nhat_ky, "a", encoding="utf-8") as f:
        f.write(f"v{PHIEN_BAN} exe={sys.executable}\n")
        try:
            mp3, cau = asyncio.run(tao_doan("Xin chào, đây là bản thử.", "vi-VN-NamMinhNeural", "+0%", "+0Hz"))
            f.write(f"giong ok {len(mp3)} byte, {len(cau)} cau\n")
        except Exception as e:
            f.write(f"giong LOI {e}\n")
        try:
            moi = kiem_ban_moi()
            f.write(f"ban moi: {moi}\n")
            if moi:
                mo_lai = bool(os.environ.get("DOCVOICE_KIEM_MO") or os.environ.get("DOCGIONG_KIEM_MO"))
                thay_exe(moi[1], mo_lai=mo_lai)
                f.write(f"da thay exe, mo lai={mo_lai}\n")
        except Exception as e:
            f.write(f"cap nhat LOI {e}\n")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--tu-thu":
        don_exe_cu()
        tu_thu(sys.argv[2])
    elif (os.environ.get("DOCVOICE_KIEM_MO") or os.environ.get("DOCGIONG_KIEM_MO")) and che_do_thu():
        # kiểm đường mở lại sau cập nhật: dựng app ẩn, ghi nhật ký rồi thoát
        app = App()
        app.withdraw()
        kiem_mo = os.environ.get("DOCVOICE_KIEM_MO") or os.environ.get("DOCGIONG_KIEM_MO")
        with open(kiem_mo, "a", encoding="utf-8") as f:
            f.write(f"mo lai ok: v{PHIEN_BAN} giao dien dung xong, exe={sys.executable}\n")
        app.destroy()
    else:
        App().mainloop()
