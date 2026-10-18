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
import bisect
import ctypes
import difflib
import html
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import sys
import threading
import time
import unicodedata
from collections import Counter
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import edge_tts
import numpy as np

import giong_clone as gc
import may_nhe as mn
import phat_luong as pl
try:
    import kiem_chat_luong as kcl
except ImportError:      # thiếu module thì ẩn nút kiểm, app đọc bình thường
    kcl = None
try:
    import kich_ban_kiem as kbk
except ImportError:      # thiếu module thì ẩn nút Kiểm kịch bản
    kbk = None


BIEN_PROXY = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")


def bo_proxy_chet(env, cho=0.4):
    """Bỏ biến proxy trỏ về máy này (127.0.0.1, localhost) mà không còn gì nghe ở cổng đó.

    App mở từ một tiến trình có proxy tạm (công cụ khác đặt HTTPS_PROXY=http://127.0.0.1:<cổng>) sẽ thừa hưởng
    biến đó; công cụ kia tắt thì cổng chết, mọi lời gọi Edge (danh sách giọng, đọc) báo "Cannot connect to host
    127.0.0.1:<cổng>" và ô Ngôn ngữ, ô Giọng trống. Proxy thật ở máy khác thì giữ nguyên. Luôn thêm localhost vào
    NO_PROXY để lời gọi tới máy đọc trên máy này không đi qua proxy. env là dict tên biến proxy -> giá trị,
    hàm sửa env tại chỗ và trả về danh sách tên biến đã bỏ."""
    import socket
    from urllib.parse import urlsplit
    da_bo = []
    for ten in BIEN_PROXY:
        gt = (env.get(ten) or "").strip()
        if not gt:
            continue
        try:
            u = urlsplit(gt if "://" in gt else "http://" + gt)
            host, cong = (u.hostname or "").lower(), u.port or 80
        except ValueError:
            continue
        if host not in ("127.0.0.1", "localhost", "::1"):
            continue
        try:
            with socket.create_connection((host, cong), timeout=cho):
                continue                    # proxy trên máy này còn sống: giữ
        except OSError:
            env.pop(ten, None)
            da_bo.append(ten)
    for ten in ("NO_PROXY", "no_proxy"):
        cu = [x.strip() for x in (env.get(ten) or "").split(",") if x.strip()]
        for h in ("localhost", "127.0.0.1", "::1"):
            if h not in cu:
                cu.append(h)
        env[ten] = ",".join(cu)
    return da_bo


def ap_proxy_cho_app():
    """Áp bo_proxy_chet vào môi trường của app. Chỉ đọc đúng các biến proxy theo tên cố định (Windows không phân
    biệt hoa thường nên chỉ cần tên viết hoa)."""
    env = {"HTTPS_PROXY": os.environ.get("HTTPS_PROXY", ""), "HTTP_PROXY": os.environ.get("HTTP_PROXY", ""),
           "ALL_PROXY": os.environ.get("ALL_PROXY", ""), "NO_PROXY": os.environ.get("NO_PROXY", "")}
    bo = bo_proxy_chet(env)
    if "HTTPS_PROXY" in bo and os.environ.get("HTTPS_PROXY") is not None:
        del os.environ["HTTPS_PROXY"]
    if "HTTP_PROXY" in bo and os.environ.get("HTTP_PROXY") is not None:
        del os.environ["HTTP_PROXY"]
    if "ALL_PROXY" in bo and os.environ.get("ALL_PROXY") is not None:
        del os.environ["ALL_PROXY"]
    os.environ["NO_PROXY"] = env["NO_PROXY"]
    return bo


PROXY_DA_BO = ap_proxy_cho_app()


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
PHIEN_BAN = "1.6.0"
KHO_GITHUB = "ducdg88/doc-voice"
TEN_EXE = "DocVoice.exe"
LINK_TAI_HOP_LE = f"https://github.com/{KHO_GITHUB}/releases/download/"
FILE_TU_DIEN = os.path.join(GOC, "tu_dien.txt")
FILE_BO_DEM_DICH = os.path.join(GOC, "bo_dem_dich.json")
BYTE_MOI_GIAY = 6000  # edge-tts trả mp3 48 kbps: 6000 byte = 1 giây
# Một frame MPEG-2 Layer III 24 kHz, 48 kbps, mono do LAME mã hóa.
# Frame có side information hợp lệ, khác với header + byte 0 không giải mã được.
FRAME_IM_LANG = bytes.fromhex(
    "fff364c40000000348000000004c414d455555554c414d45332e313031202862657461203329"
) + b"\x55" * 106
GIAY_MOI_FRAME = 0.024  # 576 samples / 24000 Hz = 24 ms mỗi frame
TOI_DA_PAUSE = 10.0  # giây, trần cho mỗi chỉ dẫn [nghỉ ...] để không sinh file mp3 khổng lồ

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
        # giọng clone chưa có khoảng nghỉ theo giây: chỉ dẫn [nghỉ Xs], (pause) thành ngắt đoạn (lặng dài hơn giữa hai câu)
        sach = re.sub(r"[ \t]*(?:" + MAU_PAUSE + r"|\((?:nghỉ|pause|tạm dừng)\))[ \t]*", "\n", sach, flags=re.IGNORECASE)
        sach = re.sub(r"\n{3,}", "\n\n", sach).strip()
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


def mci_hoi(lenh):
    """Lệnh MCI có trả lời (status ...): trả chuỗi, lỗi thì chuỗi rỗng."""
    buf = ctypes.create_unicode_buffer(128)
    if _mci(lenh, buf, 127, None) != 0:
        return ""
    return buf.value


def phat(duong_dan, am_luong=None):
    mci("close docvoice")
    mci(f'open "{duong_dan}" type mpegvideo alias docvoice')
    mci("set docvoice time format milliseconds")
    if am_luong is not None:                  # tự thử: phát câm để đo vị trí mà không làm phiền ai
        mci(f"setaudio docvoice volume to {int(am_luong)}")
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


def ten_clone(slug):
    v = gc.ds_giong().get(slug) or gc.GIONG_CLONE.get(slug) or {"ten": slug, "gioi": ""}
    return f"★ {v['ten']} · {v.get('gioi', '')}"


def danh_sach_giong(loc, giong_edge):
    """[(mã, tên hiện)] cho ô Giọng. Ngôn ngữ "clone" chỉ có giọng clone; ngôn ngữ khác thì giọng
    clone cùng tiếng (Arthur en-US hợp mọi en-*) đứng đầu, rồi tới giọng Edge của locale đó.
    Giọng clone lấy từ gc.ds_giong(): giọng đóng sẵn (mẫu hay dấu giọng) và giọng người dùng đã nhập."""
    kho = gc.ds_giong()
    if loc == MA_CLONE:
        return [(gc.TIEN_TO_CLONE + s, ten_clone(s)) for s in kho]
    tieng = loc.split("-")[0].lower()
    ds = [(gc.TIEN_TO_CLONE + s, ten_clone(s)) for s, v in kho.items()
          if str(v.get("locale", "en-US")).split("-")[0].lower() == tieng]
    return ds + [(v["ShortName"], ten_hien_thi(v)) for v in giong_edge if v["Locale"] == loc]


KHOA_DA_CHUYEN_ARTHUR = "da_chuyen_arthur"


def _da_qua_lan_dau(cd):
    return bool(cd.get(KHOA_DA_CHUYEN_ARTHUR) or cd.get("da_gioi_thieu_clone"))


def chuyen_arthur_lan_dau(cd):
    """Lần đầu mở bản có Arthur (cai_dat.json chưa có khoá đánh dấu, thường đang nhớ giọng Edge cũ như
    en-AU William): chuyển sang Arthur MỘT lần rồi đánh dấu. Bản chưa có giọng nào (bản team) thì không
    ép. Trả True nếu vừa chuyển (cần ghi file)."""
    if _da_qua_lan_dau(cd) or "arthur" not in gc.ds_giong():
        return False
    cd.update({"ngon_ngu": MA_CLONE, "giong": gc.TIEN_TO_CLONE + "arthur", KHOA_DA_CHUYEN_ARTHUR: True,
               "da_gioi_thieu_clone": True})
    cd.pop("mo_app_chon_arthur", None)
    return True


def lua_chon_khi_mo(cd):
    """(ngôn ngữ, giọng) lúc mở app: đúng giọng người dùng chọn lần cuối (Yêu cầu: "lần sau
    đọc thì cũng đọc đúng như cái giọng voice đấy"). Chưa có lựa chọn nào mà máy có Arthur thì là Arthur;
    chưa có giọng clone nào thì giữ lựa chọn cũ hay mặc định của app."""
    if cd.get("giong") and (_da_qua_lan_dau(cd) or "arthur" not in gc.ds_giong()):
        return cd.get("ngon_ngu", MA_CLONE), cd.get("giong")
    if "arthur" in gc.ds_giong():
        return MA_CLONE, gc.TIEN_TO_CLONE + "arthur"
    return cd.get("ngon_ngu", "en-AU"), cd.get("giong")


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
    """Độ dài đoạn audio theo đúng byte mp3 thật (48 kbps cố định, 6000 byte một giây), kể cả
    phần im lặng cuối đoạn. Mốc câu của Microsoft Neural chỉ dùng cho vị trí câu trong đoạn.
    Chỉ khi mp3 rỗng mới dự phòng theo mốc câu."""
    if mp3:
        return len(mp3) / BYTE_MOI_GIAY
    if cau:
        return max(bd + dai for bd, dai, _ in cau)
    return 0.0


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


MAU_TIEU_DE_MD = re.compile(r"^[ \t]*#{1,6}[ \t]+(.*)$")
TU_KHOA_CHUONG = r"(?:Phần|Chương|Chapter|Part|Scene|Mục)"
# "Phần 2", "Part 3: Tên", "Chương 1 - Tên" (số rồi hết dòng hoặc dấu ngăn), hoặc "Phần: Tên".
MAU_TIEU_DE_PHAN = re.compile(
    rf"^{TU_KHOA_CHUONG}[ \t]*(?:\d+[ \t]*(?:$|[:.\-\u2013\u2014][ \t]*(.*)$)|:[ \t]*(.+)$)",
    re.IGNORECASE)


def chuan_khop(chu):
    """Chuẩn hoá để so khớp chữ: bỏ hoa thường, dấu câu, khoảng trắng thừa."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", chu.casefold())).strip()


def nhan_dien_tieu_de(dong):
    """Trả (tên tiêu đề, chữ đọc của dòng) nếu dòng là tiêu đề phân cảnh, không thì None.
    Chỉ nhận: dòng Markdown bắt đầu bằng #, dòng "Phần/Chương/Chapter/Part/Scene/Mục" có số
    (hết dòng hoặc theo sau dấu : . -) hoặc có dấu hai chấm. Mọi dòng khác là chữ thường."""
    d = dong.strip()
    if not d:
        return None
    m = MAU_TIEU_DE_MD.match(d)
    if m:
        ten = re.sub(r"[*_`]", "", m.group(1)).strip()
        return (ten, ten) if ten else None
    m = MAU_TIEU_DE_PHAN.match(d)
    if m:
        ten = re.sub(r"[*_`]", "", (m.group(1) or m.group(2) or "")).strip()
        return (ten or d.rstrip(" :.-"), d)
    return None


DAU_CUOI_CAU = ".!?。！？…"
MAU_DONG_DANH_SACH = re.compile(r"^\s*(?:\d+[.)]|[-*\u2022])\s+")


def chuan_bi_van_ban_doc(van_ban):
    """Văn bản đưa cho giọng đọc: bỏ ký tự # đầu dòng tiêu đề Markdown (Edge sẽ đọc thành tiếng),
    thêm dấu chấm cuối dòng tiêu đề (dấu hai chấm cuối được đổi thành dấu chấm) để tiêu đề là một
    câu riêng có mốc thời gian riêng. Dòng danh sách không có dấu câu cuối, hoặc dòng đứng ngay
    trước một tiêu đề, cũng được thêm dấu chấm để không dính sang câu kế tiếp."""
    dong_goc = van_ban.splitlines()
    nhan = [nhan_dien_tieu_de(d) for d in dong_goc]
    dong_moi = []
    for i, dong in enumerate(dong_goc):
        td = nhan[i]
        chu = td[1] if td else dong
        ket_noi = bool(td)
        if not td and chu.strip() and MAU_DONG_DANH_SACH.match(chu):
            ket_noi = True
        if not td and chu.strip():
            ke = next((nhan[j] for j in range(i + 1, len(dong_goc)) if dong_goc[j].strip()), None)
            if ke:
                ket_noi = True
        if ket_noi and chu.strip():
            chu = chu.rstrip()
            if chu[-1] in ":;":
                chu = chu[:-1].rstrip()
            if chu and chu[-1] not in DAU_CUOI_CAU:
                chu += "."
        dong_moi.append(chu)
    return "\n".join(dong_moi)


def loc_kich_ban_youtube(van_ban):
    """Lọc bỏ các ghi chú đạo diễn, phân cảnh, âm nhạc và markdown thường gặp trong kịch bản video (giữ lại chỉ dẫn [nghỉ ...] và [pause ...]).
    Dòng tiêu đề Markdown chỉ còn một dấu # đầu dòng để Chapters nhận ra tiêu đề; dấu # này KHÔNG bao giờ
    được đưa cho giọng đọc hay phụ đề (xem chuan_bi_van_ban_doc)."""
    if not van_ban:
        return "", 0
    dem = 0

    def thay_ngoac_vuong(m):
        nonlocal dem
        noi_dung = m.group(0)
        if re.fullmatch(MAU_PAUSE, noi_dung, re.IGNORECASE):
            return noi_dung
        if gc.la_the_sac_thai(noi_dung):      # thẻ sắc thái [hồi hộp], [hào hứng]... giữ cho giọng clone
            return noi_dung
        dem += 1
        return ""
    vb = re.sub(r"\[[^\]\n]*\]", thay_ngoac_vuong, van_ban)

    tu_khoa_dao_dien = r"cười|nhạc|music|b-roll|camera|voice|intro|outro|cut|quay|cảnh|âm thanh|sfx|sound|tiếng|hiệu ứng|vỗ tay|hét|thì thầm"
    def thay_ngoac_tron(m):
        nonlocal dem
        noi_dung = m.group(0)
        if re.fullmatch(MAU_PAUSE, noi_dung, re.IGNORECASE):
            return noi_dung
        if re.search(tu_khoa_dao_dien, noi_dung, re.IGNORECASE):
            dem += 1
            return ""
        return noi_dung
    vb = re.sub(r"\([^\)\n]*\)", thay_ngoac_tron, vb)

    vb = re.sub(r"[*_`]", "", vb)
    vb = re.sub(r"^[ \t]*#{1,6}[ \t]+", "# ", vb, flags=re.M)
    vb = re.sub(r"[ \t]+", " ", vb)
    vb = re.sub(r"[ \t]*\n[ \t]*", "\n", vb)
    vb = re.sub(r"\n{3,}", "\n\n", vb)
    return vb.strip(), dem


def tao_khoang_lang_mp3(so_giay):
    """Tạo dữ liệu MP3 im lặng chuẩn MPEG-2 Layer 3 24kHz 48kbps mono (khớp định dạng của Edge TTS)."""
    if so_giay <= 0:
        return b""
    so_frame = max(1, int(round(so_giay / GIAY_MOI_FRAME)))
    return FRAME_IM_LANG * so_frame


# Chỉ dẫn khoảng nghỉ: [nghỉ 1s], [pause 2s], [tạm dừng 2s], (nghỉ 1.5 giây)...
MAU_PAUSE = (r"\[(?:nghỉ|pause|tạm dừng)\s*([\d\.]+)\s*(?:s|giây)?\s*\]"
             r"|\((?:nghỉ|pause|tạm dừng)\s*([\d\.]+)\s*(?:s|giây)?\s*\)")


def tach_doan_va_pause(van_ban, canh_bao=None):
    """Tách văn bản thành danh sách các khối: ('text', doan_chu) hoặc ('pause', so_giay).
    Mỗi khoảng nghỉ tối đa TOI_DA_PAUSE giây; khoảng nghỉ dài hơn bị rút lại và ghi vào canh_bao (nếu có)."""
    mau = MAU_PAUSE
    cac_phan = []
    vi_tri = 0
    for m in re.finditer(mau, van_ban, re.IGNORECASE):
        dau, cuoi = m.span()
        if dau > vi_tri:
            doan_chu = van_ban[vi_tri:dau].strip()
            if doan_chu:
                cac_phan.append(("text", doan_chu))
        giay_str = m.group(1) or m.group(2)
        try:
            giay = float(giay_str)
            if giay > TOI_DA_PAUSE:
                if canh_bao is not None:
                    canh_bao.append(f"Khoảng nghỉ {giay_str} giây đã rút còn {TOI_DA_PAUSE:g} giây")
                giay = TOI_DA_PAUSE
            if giay > 0:
                cac_phan.append(("pause", giay))
        except ValueError:
            pass
        vi_tri = cuoi
    if vi_tri < len(van_ban):
        doan_chu = van_ban[vi_tri:].strip()
        if doan_chu:
            cac_phan.append(("text", doan_chu))
    return cac_phan


def dinh_dang_tg_yt(giay):
    """Định dạng số giây thành chuỗi thời gian mm:ss hoặc hh:mm:ss theo chuẩn YouTube."""
    s = int(round(giay))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def trich_xuat_chapters(van_ban, cau_list=None, toc_do=0):
    """Trích xuất mốc thời gian phân cảnh YouTube (Chapters) từ tiêu đề kịch bản và timeline audio.

    Tiêu đề: dòng Markdown (#), dòng Phần/Chương/Chapter/Part/Scene/Mục có số hoặc dấu hai chấm,
    Mọi dòng khác (kể cả "Phần lớn...", "1. Mua gỗ") là chữ thường, không ra mốc.
    Mốc thời gian lấy từ chính câu audio của tiêu đề trong cau_list (cùng mốc với file SRT);
    chưa có cau_list (chưa chuyển giọng) thì ước tính 2,5 từ mỗi giây."""
    if not van_ban.strip():
        return ""
    dong_list = [d.strip() for d in van_ban.splitlines() if d.strip()]
    nhan = [nhan_dien_tieu_de(d) for d in dong_list]
    he_so = max(0.5, (100 + toc_do) / 100.0)

    muc = []  # (tên, số từ đứng trước, chữ chuẩn hoá của tiêu đề, đầu dòng nội dung kế tiếp)
    tu_tich_luy = 0
    for i, (d, td) in enumerate(zip(dong_list, nhan)):
        if td:
            ke_tiep = ""
            for j in range(i + 1, len(dong_list)):
                if not nhan[j]:
                    ke_tiep = " ".join(chuan_khop(dong_list[j]).split()[:6])
                    break
            muc.append((td[0], tu_tich_luy, chuan_khop(td[1]), ke_tiep))
        tu_tich_luy += len((td[1] if td else d).split())
    if not muc:
        return ""
    tong_tu_kich_ban = max(1, tu_tich_luy)

    if cau_list:
        cum_tu, tong_tu_audio = [], 0
        for _, _, chu in cau_list:
            cum_tu.append(tong_tu_audio)
            tong_tu_audio += len(chu.split())
        chu_chuan = [chuan_khop(c[2]) for c in cau_list]
        so_cau = len(cau_list)
        con_tro = 0
        cac_muc = []
        for ten, tu_truoc, tieu_de_k, ke_tiep in muc:
            # Vị trí dự đoán theo tỉ lệ số từ, chỉ để chọn câu đúng khi nhiều câu giống nhau.
            muc_tieu = tu_truoc / tong_tu_kich_ban * tong_tu_audio + 1e-6
            du_doan = min(so_cau - 1, max(0, bisect.bisect_right(cum_tu, muc_tieu) - 1))
            ung_vien = [k for k in range(con_tro, so_cau) if tieu_de_k and chu_chuan[k] == tieu_de_k]
            if not ung_vien and ke_tiep:
                ung_vien = [k for k in range(con_tro, so_cau) if chu_chuan[k].startswith(ke_tiep)]
            vi_tri = min(ung_vien, key=lambda k: abs(k - du_doan)) if ung_vien else du_doan
            vi_tri = min(so_cau - 1, max(vi_tri, con_tro))
            con_tro = min(so_cau - 1, vi_tri + 1)
            cac_muc.append((cau_list[vi_tri][0], ten))
    else:
        cac_muc = [(tu_truoc / (2.5 * he_so), ten) for ten, tu_truoc, _, _ in muc]

    # Chuẩn hóa YouTube: mốc đầu tiên bắt đầu từ 00:00
    ket_qua = []
    if not cac_muc or cac_muc[0][0] > 5.0:
        ket_qua.append((0.0, "Giới thiệu"))
    else:
        cac_muc[0] = (0.0, cac_muc[0][1])

    for tg, ten in cac_muc:
        # YouTube yêu cầu các mốc Chapters cách nhau ít nhất 10 giây.
        if ket_qua and tg < ket_qua[-1][0] + 10.0:
            tg = ket_qua[-1][0] + 10.0
        ket_qua.append((tg, ten))

    lines = [f"{dinh_dang_tg_yt(tg)} {ten}" for tg, ten in ket_qua]
    return "\n".join(lines)


def tao_srt_song_ngu(cau_list, van_ban_dich, kieu_shorts=False, van_ban_nguon=None, canh_bao=None):
    """Tạo phụ đề tiếng Việt (_vi.srt): mốc lấy từ câu Edge thật, chữ là bản dịch của đúng các câu nguồn
    nằm trong câu Edge đó (ghép theo nội dung, xem nhom_phu_de_dich). Không # và không chỉ dẫn khoảng nghỉ."""
    dong, so = [], 0
    for bd, kt, chu_vi, _, _ in nhom_phu_de_dich(cau_list, van_ban_dich, van_ban_nguon, canh_bao):
        if kieu_shorts and len(chu_vi.split()) > 7:
            cum = chia_cau_shorts(chu_vi, max_tu=7)
            tong_len = sum(len(c) for c in cum) or 1
            t_hien_tai = bd
            for c in cum:
                so += 1
                t_doan = (kt - bd) * (len(c) / tong_len)
                dong.append(f"{so}\n{gio_srt(t_hien_tai)} --> {gio_srt(t_hien_tai + t_doan)}\n{c}\n")
                t_hien_tai += t_doan
        else:
            so += 1
            dong.append(f"{so}\n{gio_srt(bd)} --> {gio_srt(kt)}\n{chu_vi}\n")
    return "\n".join(dong)


STOPWORDS_SEO = {
    "và", "là", "của", "các", "những", "có", "trong", "với", "cho", "được", "này", "đó", "đã", "sẽ",
    "đang", "để", "khi", "về", "như", "từ", "người", "ra", "vào", "lại", "thì", "ở", "một", "hai",
    "ba", "bạn", "chúng", "ta", "tôi", "rất", "cũng", "nhiều", "hơn", "không", "nhưng", "bởi", "vì",
    "the", "and", "is", "in", "to", "of", "for", "with", "on", "at", "from", "by", "about", "as",
    "this", "that", "it", "an", "be", "are", "was", "were", "will", "have", "has", "had", "you", "we"
}


def trich_xuat_seo(van_ban):
    """Trích xuất tiêu đề gợi ý, đoạn mô tả mở đầu (hook), tags chuẩn YouTube (< 500 ký tự) và hashtags."""
    if not van_ban.strip():
        return {"tieu_de": [], "mo_ta_hook": "", "tags": "", "hashtags": ""}
    dong_list = [d.strip() for d in van_ban.splitlines() if d.strip()]
    cac_tieu_de = []
    for d in dong_list:
        m = re.match(r"^#{1,3}\s+(.+)$", d)
        if m:
            clean_t = re.sub(r"[*_`]", "", m.group(1)).strip()
            if clean_t and clean_t not in cac_tieu_de:
                cac_tieu_de.append(clean_t)
    cau_dau = (re.split(r"(?<=[.!?。！？…])\s+|\n+", van_ban)[0] if van_ban else "").strip()
    cau_dau_sach = re.sub(r"[*_#`\[\]\(\)]", "", cau_dau).strip()
    if cau_dau_sach and cau_dau_sach not in cac_tieu_de:
        cac_tieu_de.append(cau_dau_sach[:70])
    if cac_tieu_de:
        goc = cac_tieu_de[0]
        if len(goc) < 50:
            cac_tieu_de.append(f"Sự thật về {goc}")
            cac_tieu_de.append(f"{goc}: Những điều bạn chưa biết")
    cac_cau = [c.strip() for c in re.split(r"(?<=[.!?。！？…])\s+|\n+", van_ban) if c.strip()]
    mo_ta = []
    tong_len = 0
    for c in cac_cau:
        c_sach = re.sub(r"[*_#`\[\]]", "", c).strip()
        if not c_sach:
            continue
        mo_ta.append(c_sach)
        tong_len += len(c_sach)
        if tong_len >= 180:
            break
    mo_ta_hook = " ".join(mo_ta)
    if len(mo_ta_hook) > 250:
        mo_ta_hook = mo_ta_hook[:247] + "..."
    chu_sach = re.sub(r"[^\w\s]", " ", van_ban.lower())
    tu_list = [t for t in chu_sach.split() if len(t) >= 2 and t not in STOPWORDS_SEO]
    dem_tu = Counter(tu_list)
    top_tu = [t for t, _ in dem_tu.most_common(25)]
    tags = []
    tong_tag_len = 0
    if cac_tieu_de:
        tag_tieu_de = re.sub(r"[^\w\s]", "", cac_tieu_de[0].lower()).strip()
        if tag_tieu_de:
            tags.append(tag_tieu_de)
            tong_tag_len += len(tag_tieu_de) + 2
    for t in top_tu:
        if tong_tag_len + len(t) + 2 < 480:
            tags.append(t)
            tong_tag_len += len(t) + 2
    str_tags = ", ".join(tags)
    hashtags = ["#" + t.capitalize() for t in top_tu[:5]]
    str_hashtags = " ".join(hashtags)
    return {
        "tieu_de": cac_tieu_de[:4],
        "mo_ta_hook": mo_ta_hook,
        "tags": str_tags,
        "hashtags": str_hashtags
    }


def tao_srt(ket_qua, tu_dien=({}, ()), kieu_shorts=False):
    """ket_qua: [(mp3, [(bắt_đầu_giây, dài_giây, câu)])] theo thứ tự đoạn.
    tu_dien: (bản đồ câu, từ điển dự phòng) để trả phụ đề về chữ gốc."""
    ban_do, du_phong = tu_dien
    dong, lech, so = [], 0.0, 0
    for mp3, cau in ket_qua:
        for bd, dai, chu in cau:
            if not chu.strip():
                continue
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
            if not chu.strip():
                continue
            chu_that = ban_do.get(chuan_cau(chu)) or tra_chu_goc(chu, du_phong)
            cau_list.append((lech + bd, lech + bd + dai, chu_that.strip()))
        lech += tinh_do_dai_doan(mp3, cau)
    return cau_list


# ---------- dịch và phụ đề tiếng Việt (_vi.srt) ----------
# Thiết kế: đơn vị dịch là "câu nguồn" do app tách từ chữ ĐỌC đã sạch, mỗi câu
# một dòng dịch (mã định danh của câu = số thứ tự dòng). Phụ đề lấy mốc từ câu Edge thật
# (SentenceBoundary), rồi ghép mỗi câu Edge với dãy câu nguồn theo NỘI DUNG (so khớp ký tự đã
# bỏ dấu câu, khoảng trắng, dấu thanh), không theo số lượng, nên không cần bộ tách câu của app
# trùng với Edge.
MAU_TACH_CAU = r"(?<=[.!?。！？…])\s+|\n+"
MAU_HET_CAU = re.compile(r"[.!?。！？…]+[\"'”’»)\]]*(?=\s)")
# Chữ viết tắt hay gặp: dấu chấm ngay sau chúng không kết thúc câu.
VIET_TAT = {"mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "e.g", "i.e", "u.s", "u.k",
            "u.s.a", "a.m", "p.m", "fig", "approx", "inc", "ltd", "mt", "ths", "ts", "pgs", "gs", "tp"}
MAU_DAU_THANG = re.compile(r"(?:^|(?<=[.!?。！？…:] ))[ \t]*#{1,6}(?![#\d])[ \t]*", re.M)
MAU_THE_NGOAC_VUONG = re.compile(r"\[[^\]\n]*\]")
MAU_PAUSE_DICH = re.compile(
    r"\[\s*(?:nghỉ|pause|tạm dừng|tạm nghỉ|dừng|ngừng)[^\]\n]*\]"
    r"|\(\s*(?:nghỉ|pause|tạm dừng|tạm nghỉ)\s*[\d.,]+\s*(?:s|giây)?\s*\)",
    re.IGNORECASE)
LO_DICH = 1800  # ký tự mỗi lượt gửi máy dịch
KHOANG_NGHI_TOI_THIEU = 0.3  # giây lặng giữa hai câu Edge được coi là khoảng nghỉ, phụ đề không phủ qua
_NGUON_CUA_BAN_DICH = {}  # khoá bản dịch -> danh sách câu nguồn, để _vi.srt biết dòng dịch nào của câu nào


def bo_dau_thang(chu):
    """Bỏ # tiêu đề Markdown ở ĐẦU DÒNG (và ngay sau dấu kết câu, khi bản dịch cũ nối nhiều dòng thành
    một đoạn). Dấu # giữa câu như "Số # 5" hay "#5" được giữ. Gộp khoảng trắng trong từng dòng."""
    return "\n".join(re.sub(r"[ \t]+", " ", MAU_DAU_THANG.sub("", d)).strip()
                     for d in chu.split("\n")).strip()


def bo_the_pause(chu):
    """Bỏ chỉ dẫn khoảng nghỉ ([nghỉ 1s], [pause 2s], (pause 2s), [tạm dừng 2 giây]) khỏi chữ hiển thị."""
    return re.sub(r"[ \t]+", " ", MAU_PAUSE_DICH.sub(" ", chu)).strip()


def chu_nguon_dich(van_ban):
    """Chữ ĐỌC đã sạch đưa cho máy dịch, mỗi dòng một dòng nguồn: bỏ # đầu dòng, bỏ chỉ dẫn khoảng nghỉ
    (tại đó ngắt dòng, giống tach_doan_va_pause tách lượt đọc), bỏ thẻ trong ngoặc vuông ([cười], [B-roll]),
    gộp khoảng trắng. Truyền vào chữ đã qua chuan_bi_van_ban_doc thì giống chữ gửi Edge."""
    dong_ra = []
    for dong in re.sub(MAU_PAUSE, "\n", van_ban, flags=re.IGNORECASE).split("\n"):
        dong = bo_dau_thang(MAU_THE_NGOAC_VUONG.sub(" ", dong))
        if re.search(r"\w", dong):
            dong_ra.append(dong)
    return "\n".join(dong_ra)


def _tach_dong_thanh_cau(dong):
    """Tách một dòng thành câu tại dấu kết câu, trừ sau số thứ tự đầu dòng ("1."), chữ viết tắt (Mr., e.g.)
    và chữ cái viết tắt tên (J. K.)."""
    cau, dau = [], 0
    for m in MAU_HET_CAU.finditer(dong):
        if m.group(0) == ".":
            truoc = dong[dau:m.start()]
            tu_goc = (truoc.split() or [""])[-1].lstrip("(\"'“‘")
            if (re.fullmatch(r"\s*(?:\d{1,3}|[A-Za-z])", truoc) or tu_goc.casefold() in VIET_TAT
                    or re.fullmatch(r"[A-Z]", tu_goc)):
                continue
        cau.append(dong[dau:m.end()].strip())
        dau = m.end()
    cau.append(dong[dau:].strip())
    return [c for c in cau if re.search(r"\w", c)]


def tach_cau_nguon(van_ban):
    """Danh sách câu nguồn (đơn vị dịch) của văn bản; số thứ tự trong danh sách là mã của câu."""
    cau = []
    for dong in chu_nguon_dich(van_ban).split("\n"):
        cau.extend(_tach_dong_thanh_cau(dong))
    return cau


def _khoa_ban_dich(chu):
    chuan = "\n".join(d.strip() for d in chu.strip().split("\n"))
    return hashlib.sha1(chuan.encode("utf-8")).hexdigest()


def _dich_mot(doan):
    params = urllib.parse.urlencode({"client": "gtx", "sl": "auto", "tl": "vi", "dt": "t", "q": doan})
    url = f"https://translate.googleapis.com/translate_a/single?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8"))
    if data and isinstance(data, list) and data[0]:
        return "".join(item[0] for item in data[0] if item and isinstance(item, list) and item[0])
    return ""


def tran_luot_dich(so_lo):
    """Trần số lượt gọi máy dịch cho một lần bấm Dịch: mỗi lô một lượt, cộng dự phòng bằng số lô + 8."""
    return 2 * so_lo + 8


def _phan_bo(do_dai_muc, trong_so):
    """Xếp k mục liên tiếp (độ dài do_dai_muc) vào m ngăn liên tiếp theo tỉ lệ trọng số, không tách mục nào.
    Đủ mục (k >= m) thì ngăn nào cũng có ít nhất một mục. Mục đầu vào ngăn đầu, mục cuối vào ngăn cuối.
    Trả chỉ số ngăn của từng mục."""
    k, m = len(do_dai_muc), len(trong_so)
    if not k or not m:
        return []
    tong_l = sum(do_dai_muc) or k
    tong_w = sum(trong_so) or m
    moc, t = [], 0.0
    for w in trong_so[:-1]:
        t += w
        moc.append(t / tong_w)
    ngan, truoc, j_truoc = [], 0.0, -1
    for i, l in enumerate(do_dai_muc):
        j = bisect.bisect_right(moc, (truoc + l / 2) / tong_l)
        truoc += l
        if k >= m:
            j = min(max(j, j_truoc, m - k + i, 0), j_truoc + 1, m - 1)
        else:
            j = min(max(j, j_truoc, 0), m - 1)
        if i == 0:
            j = 0
        if i == k - 1 and k > 1:
            j = m - 1
        ngan.append(j)
        j_truoc = j
    return ngan


def dich_cau_nguon(cau_nguon, ham_dich=None, ngu=None, canh_bao=None, thong_ke=None):
    """Dịch danh sách câu nguồn, trả đúng một chuỗi dịch cho mỗi câu (cùng mã, cùng thứ tự).
    Gom câu thành lô tối đa LO_DICH ký tự, mỗi câu một dòng. Lô trả về lệch số dòng thì chia đôi lô rồi dịch
    lại (tới từng câu), trong trần tran_luot_dich. Hết trần thì không gọi thêm: chia các câu dịch của lô lệch
    cho các câu nguồn theo tỉ lệ độ dài, không cắt giữa câu dịch, và ghi canh_bao.
    Máy dịch báo 429 thì chờ 2 rồi 4 giây, thử lại; vẫn lỗi thì ném RuntimeError (bản dịch cũ giữ nguyên)."""
    ham_dich = ham_dich or _dich_mot
    ngu = ngu or time.sleep
    n = len(cau_nguon)
    lo, dau, dai = [], 0, 0
    for i, c in enumerate(cau_nguon):
        if i > dau and dai + len(c) + 1 > LO_DICH:
            lo.append((dau, i))
            dau, dai = i, 0
        dai += len(c) + 1
    if dau < n:
        lo.append((dau, n))
    tran = tran_luot_dich(len(lo))
    dem = [0]

    def goi(doan):
        for lan in range(3):
            if dem[0] >= tran:
                raise RuntimeError(f"Máy dịch đang quá tải, đã dùng hết {tran} lượt cho văn bản này. "
                                   "Bản dịch cũ được giữ nguyên, thử lại sau ít phút.")
            dem[0] += 1
            try:
                return ham_dich(doan)
            except urllib.error.HTTPError as e:
                if e.code != 429 or lan == 2:
                    raise RuntimeError(f"Máy dịch trả lỗi {e.code}. Bản dịch cũ được giữ nguyên.") from e
                ngu(2 * (lan + 1))

    ket_qua = [None] * n

    def dich_lo(a, b):
        ra = [d.strip() for d in goi("\n".join(cau_nguon[a:b])).split("\n")]
        ra = [d for d in ra if d]
        if b - a == 1:
            ket_qua[a] = " ".join(ra)
            return None
        if len(ra) == b - a:
            ket_qua[a:b] = ra
            return None
        return ra

    cho = []
    for a, b in lo:
        ra = dich_lo(a, b)
        if ra is not None:
            cho.append((a, b, ra))
    da_ha_cap = 0
    while cho:
        a, b, ra = cho.pop(0)
        if tran - dem[0] >= 2:
            giua = (a + b) // 2
            for x, y in ((a, giua), (giua, b)):
                ra_con = dich_lo(x, y)
                if ra_con is not None:
                    cho.append((x, y, ra_con))
            continue
        # Hết trần lượt: chia các câu dịch của lô cho câu nguồn theo tỉ lệ độ dài, không cắt giữa câu.
        manh = [c for c in (bo_dau_thang(x) for x in re.split(MAU_TACH_CAU, " ".join(ra))) if c]
        ngan = _phan_bo([len(x) for x in manh], [len(c) for c in cau_nguon[a:b]])
        gom = [[] for _ in range(b - a)]
        for x, j in zip(manh, ngan):
            gom[j].append(x)
        for j in range(b - a):
            ket_qua[a + j] = " ".join(gom[j])
        da_ha_cap += b - a
    if da_ha_cap and canh_bao is not None:
        canh_bao.append(f"Máy dịch gộp dòng ở {da_ha_cap} câu, đã chia bản dịch theo tỉ lệ độ dài "
                        "(không cắt giữa câu); nên bấm Dịch lại sau.")
    if thong_ke is not None:
        thong_ke.update(so_luot=dem[0], tran=tran, so_lo=len(lo), ha_cap=da_ha_cap)
    return [bo_the_pause(bo_dau_thang(d or "")) for d in ket_qua]


def dich_tieng_viet(van_ban, ham_dich=None, ngu=None, canh_bao=None, thong_ke=None):
    """Dịch văn bản sang tiếng Việt bằng endpoint miễn phí không cần key.
    Kết quả: mỗi câu nguồn (tach_cau_nguon) đúng một dòng, không #, không chỉ dẫn khoảng nghỉ.
    App ghi nhớ bản dịch thuộc những câu nguồn nào để _vi.srt ghép đúng."""
    cau = tach_cau_nguon(van_ban) if van_ban.strip() else []
    if not cau:
        return ""
    ban_dich = "\n".join(dich_cau_nguon(cau, ham_dich, ngu, canh_bao, thong_ke))
    if len(_NGUON_CUA_BAN_DICH) > 32:
        _NGUON_CUA_BAN_DICH.clear()
    _NGUON_CUA_BAN_DICH[_khoa_ban_dich(ban_dich)] = cau
    return ban_dich


def _chuan_ghep(chu):
    """Chuỗi để so khớp nội dung: chữ thường, bỏ dấu thanh, bỏ dấu câu và khoảng trắng."""
    chu = unicodedata.normalize("NFD", chu.casefold()).replace("đ", "d")
    return "".join(ch for ch in chu if ch.isalnum())


def ghep_cue_voi_cau_nguon(cau_list, cau_nguon):
    """Ghép câu Edge (cau_list) với câu nguồn theo NỘI DUNG.
    Trả [(cue đầu, cue cuối, [mã câu nguồn])] theo thứ tự thời gian: một nhóm là dãy cue liên tiếp cùng các
    câu nguồn nằm trong đó. Một cue chứa nhiều câu nguồn thì nhóm có nhiều câu; một câu nguồn trải nhiều cue
    thì các cue đó gộp một nhóm (bản dịch đặt trên toàn khoảng thời gian). Ghép được dưới một nửa nội dung
    thì trả None."""
    if not cau_list or not cau_nguon:
        return None
    nguon_chuan = [_chuan_ghep(c) for c in cau_nguon]
    bat_dau, t = [], 0
    for c in nguon_chuan:
        bat_dau.append(t)
        t += len(c)
    src = "".join(nguon_chuan)
    if not src:
        return None

    # 1. Con trỏ tuần tự: tìm chữ của từng cue Edge trong chuỗi nguồn, ngay sau cue trước.
    pham_vi, p, du, da_khop = [], 0, 0, 0
    for _, _, chu in cau_list:
        c = _chuan_ghep(chu)
        if not c:
            pham_vi.append(None)
            continue
        het = min(len(src), p + len(c) + 40 + len(c) // 2 + du)
        q = src.find(c, p, het)
        if q < 0 and len(c) >= 12:
            q = src.find(c, p)
        if q >= 0:
            pham_vi.append((q, q + len(c)))
            p, du, da_khop = q + len(c), 0, da_khop + len(c)
            continue
        sm = difflib.SequenceMatcher(None, c, src[p:het], autojunk=False)
        khoi = [k for k in sm.get_matching_blocks() if k.size]
        while len(khoi) > 1 and khoi[0].size < 3:
            khoi.pop(0)
        while len(khoi) > 1 and khoi[-1].size < 3:
            khoi.pop()
        trung = sum(k.size for k in khoi)
        if khoi and trung * 2 >= len(c):
            a, b = p + khoi[0].b, p + khoi[-1].b + khoi[-1].size
            pham_vi.append((a, b))
            p, du, da_khop = b, 0, da_khop + trung
        else:
            pham_vi.append(None)
            du += len(c)
    if da_khop * 2 < len(src):
        return None

    # 2. Mỗi câu nguồn thuộc các cue phủ phần đáng kể của nó (ít nhất nửa câu, hoặc nửa cue).
    cue_cua_cau = [[] for _ in cau_nguon]
    tot_nhat = [(0, None)] * len(cau_nguon)
    for j, pv in enumerate(pham_vi):
        if not pv:
            continue
        s, e = pv
        i = max(0, bisect.bisect_right(bat_dau, s) - 1)
        while i < len(cau_nguon) and bat_dau[i] < e:
            a, b = bat_dau[i], bat_dau[i] + len(nguon_chuan[i])
            ov = min(e, b) - max(s, a)
            if ov > 0:
                if ov * 2 >= b - a or ov * 2 >= e - s:
                    cue_cua_cau[i].append(j)
                if ov > tot_nhat[i][0]:
                    tot_nhat[i] = (ov, j)
            i += 1
    co_pv = [j for j, pv in enumerate(pham_vi) if pv]
    for i in range(len(cau_nguon)):
        if cue_cua_cau[i]:
            continue
        if tot_nhat[i][1] is not None:
            cue_cua_cau[i] = [tot_nhat[i][1]]
            continue
        # Câu không có chữ nào khớp (Edge không đọc hoặc đọc khác hẳn): gắn vào cue gần nhất phía trước.
        truoc = [j for j in co_pv if pham_vi[j][0] <= bat_dau[i]]
        cue_cua_cau[i] = [truoc[-1] if truoc else co_pv[0]]

    # 3. Gộp cue liên tiếp có chung câu nguồn thành nhóm.
    m = len(cau_list)
    noi = [False] * m
    cau_cua_cue = [[] for _ in range(m)]
    for i, ds in enumerate(cue_cua_cau):
        for j in range(min(ds), max(ds)):
            noi[j] = True
        cau_cua_cue[min(ds)].append(i)
    nhom, j = [], 0
    while j < m:
        k = j
        while k < m - 1 and noi[k]:
            k += 1
        cau = sorted(i for x in range(j, k + 1) for i in cau_cua_cue[x])
        if cau:
            nhom.append((j, k, cau))
        j = k + 1
    return nhom


def _cac_doan_lien(cau_list, ds_cue):
    """Chia dãy cue thành các đoạn liền, không vắt qua khoảng nghỉ (Smart Pause hay khoảng lặng giữa đoạn)."""
    doan = [[ds_cue[0]]]
    for a, b in zip(ds_cue, ds_cue[1:]):
        if cau_list[b][0] - cau_list[a][1] > KHOANG_NGHI_TOI_THIEU:
            doan.append([b])
        else:
            doan[-1].append(b)
    return doan


def nhom_phu_de_dich(cau_list, van_ban_dich, van_ban_nguon=None, canh_bao=None):
    """Các dòng phụ đề tiếng Việt [(bắt đầu, kết thúc, chữ, [chỉ số dòng dịch], [chỉ số cue Edge])].
    van_ban_nguon: văn bản (hoặc danh sách văn bản thử lần lượt) đã dùng để dịch. Câu nguồn tách lại từ đó
    phải có đúng số dòng của bản dịch; không có thì tra bản dịch app vừa làm. Mốc luôn là mốc câu Edge,
    chữ luôn là câu dịch trọn vẹn. Chỉ khi không ghép được theo nội dung mới xếp câu dịch theo tỉ lệ thời
    gian (vẫn không cắt giữa câu), và ghi canh_bao."""
    canh_bao = canh_bao if canh_bao is not None else []
    if not cau_list or not van_ban_dich.strip():
        return []
    dong_dich = [bo_the_pause(bo_dau_thang(d)) for d in van_ban_dich.strip().split("\n")]
    if not any(dong_dich):
        return []

    ung_vien = van_ban_nguon if isinstance(van_ban_nguon, (list, tuple)) else [van_ban_nguon]
    cau_nguon = None
    for vb in ung_vien:
        if vb and vb.strip():
            ds = tach_cau_nguon(vb)
            if len(ds) == len(dong_dich):
                cau_nguon = ds
                break
    if cau_nguon is None:
        cau_nguon = _NGUON_CUA_BAN_DICH.get(_khoa_ban_dich(van_ban_dich))
        if cau_nguon is not None and len(cau_nguon) != len(dong_dich):
            cau_nguon = None

    ra = []
    if cau_nguon is not None:
        nhom = ghep_cue_voi_cau_nguon(cau_list, cau_nguon)
        if nhom:
            for j, k, cau in nhom:
                chu = " ".join(dong_dich[i] for i in cau if dong_dich[i])
                if chu:
                    for doan in _cac_doan_lien(cau_list, list(range(j, k + 1))):
                        ra.append((cau_list[doan[0]][0], cau_list[doan[-1]][1], chu, cau, doan))
            return ra
        canh_bao.append("Phụ đề tiếng Việt: không ghép được bản dịch với giọng đọc theo nội dung "
                        "(văn bản đã đổi sau khi dịch?). Bấm Dịch lại rồi Chuyển để phụ đề khớp từng câu.")

    cue = [j for j, (bd, kt, chu) in enumerate(cau_list) if kt > bd and chu.strip()]
    if not cue:
        return []
    dong_co_chu = [i for i, d in enumerate(dong_dich) if d]
    if cau_nguon is None and len(dong_co_chu) == len(cue):
        # Không biết bản dịch từ văn bản nào (dán tay, dữ liệu cũ) nhưng mỗi dòng ứng đúng một câu đọc.
        return [(cau_list[j][0], cau_list[j][1], dong_dich[i], [i], [j]) for j, i in zip(cue, dong_co_chu)]

    # Dự phòng: xếp từng câu dịch trọn vẹn vào câu Edge theo tỉ lệ thời gian; câu dịch phủ tới câu Edge
    # kế tiếp có chữ, tách ra ở khoảng nghỉ. Không bao giờ cắt giữa câu dịch.
    manh = []  # (chữ câu dịch, dòng dịch)
    for i in dong_co_chu:
        manh += [(x.strip(), i) for x in re.split(MAU_TACH_CAU, dong_dich[i]) if x.strip()]
    if not manh:
        return []
    if cau_nguon is None:
        canh_bao.append("Phụ đề tiếng Việt: chưa biết bản dịch thuộc câu nào nên câu dịch được xếp theo tỉ lệ "
                        "thời gian (không cắt giữa câu). Bấm Dịch rồi Chuyển để khớp từng câu.")
    ngan = _phan_bo([len(x) for x, _ in manh], [cau_list[j][1] - cau_list[j][0] for j in cue])
    co = sorted(set(ngan))
    for t, x in enumerate(co):
        het = co[t + 1] if t + 1 < len(co) else len(cue)
        nhom_manh = [mm for mm, n in zip(manh, ngan) if n == x]
        chu = " ".join(c for c, _ in nhom_manh)
        dong = sorted({i for _, i in nhom_manh})
        for doan in _cac_doan_lien(cau_list, cue[x:het]):
            ra.append((cau_list[doan[0]][0], cau_list[doan[-1]][1], chu, dong, doan))
    return ra


# ---------- tiến độ khi đang đọc: phần trăm, x/y, thời gian chạy, ước tính còn lại ----------
GIAY_NHIP_TIEN_DO = 1.0        # làm mới dòng tiến độ mỗi giây, kể cả khi một câu đọc lâu
GIAY_BUOC_DUNG_IM = 3.0        # bước đứng yên quá ngần này thì hiện thêm "bước này đã chạy N giây"
MAU_X_Y = re.compile(r"(\d+)\s*/\s*(\d+)\s*(câu|phần|đoạn)")


def dinh_dang_gio(giay):
    """65 -> '1:05', 3725 -> '1:02:05'."""
    g = max(0, int(round(giay)))
    if g >= 3600:
        return f"{g // 3600}:{g % 3600 // 60:02d}:{g % 60:02d}"
    return f"{g // 60}:{g % 60:02d}"


def uoc_con_lai(da_chay, phan_tram, t_bat_dau_cong_viec=0.0):
    """Giây còn lại, tính từ tốc độ thật: thời gian đã chạy (trừ t_bat_dau_cong_viec nếu có) chia cho phần
    trăm đã xong. Chưa đủ dữ liệu (dưới 2% hoặc dưới 3 giây) thì trả None."""
    chay = da_chay - t_bat_dau_cong_viec
    if phan_tram is None or phan_tram < 2.0 or phan_tram >= 100.0 or chay < 3.0:
        return None
    return chay * (100.0 - phan_tram) / phan_tram


def uoc_theo_nhip_gan(lich_su, bay_gio, so_diem=4):
    """Giây còn lại theo nhịp của vài lần tiến triển gần nhất (lich_su = [(giây, phần trăm)] tăng dần). Khớp hơn
    đo cả quãng khi các phần xong không đều (đọc song song, phần lấy từ kho đi nhanh rồi phần mới đi chậm).
    Đã trừ thời gian trôi từ lần tiến triển cuối. Chưa đủ 3 điểm hoặc không tiến thì trả None."""
    if len(lich_su) < 3:
        return None
    ds = lich_su[-so_diem:]
    dt, dp = ds[-1][0] - ds[0][0], ds[-1][1] - ds[0][1]
    if dt <= 0 or dp <= 0 or ds[-1][1] >= 100.0:
        return None
    con = (100.0 - ds[-1][1]) * dt / dp - (bay_gio - ds[-1][0])
    return max(1.0, con)


def uoc_ket_hop(lich_su, bay_gio, phan_tram):
    """Giây còn lại, chọn số LỚN hơn của hai cách: theo nhịp vài lần tiến triển gần nhất và theo tốc độ cả quãng đã
    chạy (đã chạy chia phần trăm xong, nhân phần còn lại). Lấy số lớn để không báo "sắp xong" khi các phần xong
    không đều (đọc song song, phần cuối chậm): không bao giờ dưới phần còn lại nhân thời gian trung bình mỗi phần."""
    tong = uoc_con_lai(bay_gio, phan_tram)
    gan = uoc_theo_nhip_gan(lich_su, bay_gio)
    if gan is None:
        return tong
    return gan if tong is None else max(gan, tong)


def ten_buoc(chu):
    """Tên bước hiện cho người dùng, suy từ câu trạng thái của từng đường đọc."""
    c = (chu or "").lower()
    if c.startswith("lỗi") or c.startswith("không "):
        return "Lỗi"
    if c.startswith("xong"):
        return "Xong"
    if c.startswith("đã dừng") or "sẽ dừng" in c:
        return "Dừng"
    if "chuẩn âm lượng" in c or "ghép" in c:
        return "Ghép và chuẩn âm lượng"
    if "srt" in c or "xuất" in c:
        return "Xuất mp3 và srt"
    if "bộ kiểm" in c or "whisper" in c and "nghe lệch" not in c:
        return "Bật bộ kiểm đọc"
    if "đọc lại" in c:
        return "Đọc lại câu lệch"
    if "phát thử" in c:
        return "Phát thử"
    if "dịch" in c:
        return "Dịch"
    if "giọng clone" in c or "clone" in c:
        return "Đọc (giọng clone)"
    if "chuyển" in c or "đọc" in c:
        return "Đọc (giọng Edge)"
    return "Đang chạy"


def dong_tien_do(chu, phan_tram, da_chay, giay_buoc=0.0, t_bat_dau_cong_viec=0.0, con_giay=None):
    """Dòng trạng thái đầy đủ: bước, phần trăm, x/y, đã chạy, còn lại, và số giây của bước đang đứng yên."""
    buoc = ten_buoc(chu)
    if buoc in ("Lỗi", "Dừng"):
        return chu
    if buoc == "Xong":
        return f"{chu} (mất {dinh_dang_gio(da_chay)})"
    phan = [f"{buoc}", f"{phan_tram:.0f}%"]
    m = MAU_X_Y.search(chu or "")
    if m:
        phan.append(f"{m.group(3)} {m.group(1)}/{m.group(2)}")
    phan.append(f"đã chạy {dinh_dang_gio(da_chay)}")
    con = con_giay if con_giay is not None else uoc_con_lai(da_chay, phan_tram, t_bat_dau_cong_viec)
    if con is None:
        phan.append("đang ước tính thời gian còn lại")
    elif con <= 0:
        phan.append("lâu hơn dự kiến, vẫn đang chạy")      # không hiện số nhỏ giả khi bước đứng lâu hơn ước tính
    else:
        phan.append(f"còn khoảng {dinh_dang_gio(con)}")
    dong = " | ".join(phan)
    if giay_buoc >= GIAY_BUOC_DUNG_IM:
        dong += f" | bước này đã chạy {int(giay_buoc)} giây"
    return dong


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


def ban_day_du():
    """True khi exe là bản đầy đủ (dong-goi.ps1 không cờ, dựng cho máy chủ dự án): kèm âm thanh mẫu Arthur
    giong_mau/arthur/ref.wav nên chạy được cả Nhẹ lẫn Chuẩn cao (GPU). Bản trên GitHub chỉ nhúng dấu giọng,
    nên bản đầy đủ không tự cập nhật sang đó (sẽ mất mẫu cho Chuẩn cao); lên bản mới bằng cách dựng lại."""
    return bool(getattr(sys, "frozen", False)) and os.path.isfile(
        os.path.join(gc.thu_muc_goc_app(), "giong_mau", "arthur", "ref.wav"))


def kiem_ban_moi():
    """Trả (phiên bản, asset exe) nếu GitHub có bản mới hơn, không thì None. Bản đầy đủ luôn trả None."""
    if ban_day_du():
        return None
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
        self.title(f"Đọc Voice · Text to Speech · v{PHIEN_BAN}" + (" (bản đầy đủ)" if ban_day_du() else ""))
        self.geometry("980x720")
        self.minsize(800, 600)
        self.configure(bg="#f6f8fa")
        self.giong = doc_json(FILE_GIONG, [])
        self.cd = doc_json(FILE_CAI_DAT, {})
        if chuyen_arthur_lan_dau(self.cd):
            try:
                ghi_json(FILE_CAI_DAT, self.cd)
            except OSError:
                pass
        self._khoa_trang_thai = threading.Lock()
        self.dang_chay = False
        self.file_cuoi = None
        self._ban_moi_cho = None
        self._danh_sach_cau = []
        self._canh_bao_pause = []
        self._dang_theo_doi_phat = False
        self._cau_highlight_cu = -1
        self._dung_clone = threading.Event()
        self._tu_dong = None
        # highlight theo vị trí phát thật (MCI) + nghe ngay khi đang sinh
        self._cau_nguon = []               # câu của ô văn bản kèm vị trí ký tự
        self._dong_thoi_gian = []          # [(bắt đầu, kết thúc, câu đầu, câu cuối)]
        self._da_choi = False
        self._song_truc = None             # đang nghe từng câu clone lúc máy còn sinh
        self._phan_san = {}
        self._am_luong_phat = None
        self._nhat_ky_hl = None
        self._tien = {}                    # tiến độ lượt đang đọc: chu, pt, t_doi, t_cong_viec
        self._nhat_ky_tien_do = None
        self._hen_nhip = None
        self.thu_muc_ra = (self.cd.get("thu_muc_ra") or "").strip() or THU_MUC_RA      # rỗng hoặc thiếu thì dùng mặc định
        try:
            os.makedirs(self.thu_muc_ra, exist_ok=True)
        except OSError:
            self.thu_muc_ra = THU_MUC_RA
            os.makedirs(self.thu_muc_ra, exist_ok=True)
        self._nguoi_dung_da_chon = False   # đã tự đổi ngôn ngữ hoặc giọng trong phiên này
        self._dung_giao_dien()
        self._nap_ngon_ngu()               # giọng clone không cần danh sách Edge, chọn được ngay
        if not self.giong:
            self.trang_thai.set("Đang tải danh sách giọng Edge...")
        threading.Thread(target=self._tai_giong, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._thoat)
        threading.Thread(target=don_exe_cu, daemon=True).start()
        if getattr(sys, "frozen", False) and self.cd.get("tu_kiem_cap_nhat", True):
            threading.Thread(target=self._kiem_cap_nhat, args=(False,), daemon=True).start()
        kb = os.environ.get("DOCVOICE_TU_DONG")
        if kb and che_do_thu():  # chế độ tự thử: bấm như người dùng rồi ghi kết quả
            self._tu_dong = doc_json(kb, {})
            self.geometry(self._tu_dong.get("vi_tri", "1180x760+40+40"))
            self._am_luong_phat = self._tu_dong.get("am_luong")      # 0: phát câm để đo highlight
            if self._tu_dong.get("phat_sau") or self._tu_dong.get("nghe_ngay"):
                self._nhat_ky_hl = []
            if self._tu_dong.get("nhat_ky_tien_do"):
                self._nhat_ky_tien_do = []
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
        if kcl:
            ttk.Button(hang1, text="Kiểm chất lượng", command=self._kiem_chat_luong).pack(side="left", padx=(6, 0))
            ttk.Button(hang1, text="Chạy nhiều kịch bản", command=self._chay_nhieu_kich_ban).pack(side="left", padx=(6, 0))
        ttk.Button(hang1, text="Cập nhật", command=lambda: threading.Thread(
            target=self._kiem_cap_nhat, args=(True,), daemon=True).start()).pack(side="right")
        ttk.Label(hang1, text="  Ngôn ngữ").pack(side="left")
        self.ngon_ngu = ttk.Combobox(hang1, state="readonly", width=22)
        self.ngon_ngu.pack(side="left", padx=6)
        self.ngon_ngu.bind("<<ComboboxSelected>>", lambda e: self._nguoi_dung_doi(self._nap_giong))
        ttk.Label(hang1, text="Giọng").pack(side="left")
        self.chon_giong = ttk.Combobox(hang1, state="readonly", width=28)
        self.chon_giong.pack(side="left", padx=6)
        self.chon_giong.bind("<<ComboboxSelected>>", lambda e: self._nguoi_dung_doi(self._cap_nhat_cam_xuc))
        ttk.Label(hang1, text="Cảm xúc").pack(side="left")
        self.cam_xuc = ttk.Combobox(hang1, state="disabled", width=20,
                                    values=[gc.MUC_TU_DONG] + [k for k in gc.MUC_CAM_XUC if k != gc.MUC_TU_DONG])
        self.cam_xuc.set(self.cd.get("cam_xuc", gc.MUC_MAC_DINH))
        self.cam_xuc.pack(side="left", padx=6)
        self.cam_xuc.bind("<<ComboboxSelected>>", lambda e: self._nguoi_dung_doi(self._cap_nhat_nhan_giong))

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
        self.nhan_giong = ttk.Label(hang3, font=("Segoe UI Semibold", 10), foreground="#15803d")
        self.nhan_giong.pack(side="right")

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
        if kbk:
            ttk.Button(tieu_trai, text="✔ Kiểm kịch bản", command=self._kiem_kich_ban).pack(side="left", padx=(4, 0))
        ttk.Button(tieu_trai, text="📋 Chapters YT", command=self._xem_chapters).pack(side="left", padx=(4, 0))
        ttk.Button(tieu_trai, text="🎯 Trợ lý SEO", command=self._xem_seo).pack(side="left", padx=(4, 0))
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

        # Nạp dữ liệu cũ. Bản dịch đã lưu chỉ hiện lại khi khớp đúng văn bản đã lưu; lệch thì bỏ, tự dịch lại.
        self.o_chu.insert("1.0", self.cd.get("van_ban", ""))
        self._bo_dem_dich = doc_json(FILE_BO_DEM_DICH, {})
        txt0 = self.o_chu.get("1.0", "end-1c")
        if self.cd.get("ban_dich_cho") == hashlib.sha1(txt0.encode("utf-8")).hexdigest():
            self.o_dich.insert("1.0", self.cd.get("ban_dich", ""))     # bản dịch cũ không khớp chữ thì bỏ
        self._nguon_ban_dich = self.cd.get("nguon_ban_dich", "")  # văn bản đã dùng để dịch, cho _vi.srt
        self._cue_dong_dich = {}  # câu Edge -> các dòng dịch tương ứng, để tô sáng bảng dịch
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

    def _nguoi_dung_doi(self, ham):
        self._nguoi_dung_da_chon = True
        ham()
        self._luu_cai_dat()           # nhớ ngay giọng vừa chọn: mở lại app vẫn là giọng này

    def _nap_ngon_ngu(self):
        co_san = {v["Locale"] for v in self.giong}
        uu_tien = ["vi-VN", "en-US", "en-AU", "en-GB"]
        ds = [MA_CLONE] + [loc for loc in uu_tien if loc in co_san] + sorted(co_san - set(uu_tien))
        if self._nguoi_dung_da_chon and getattr(self, "_ds_giong", None):
            # nạp lại khi danh sách Edge vừa tải xong: giữ đúng thứ người dùng đang chọn
            chon, giong = self._locales[self.ngon_ngu.current()], self._ds_giong[self.chon_giong.current()]
        else:
            chon, giong = lua_chon_khi_mo(self.cd)
        self._locales = ds
        self.ngon_ngu["values"] = [f"{TEN_NGON_NGU.get(loc, loc)} ({loc})" for loc in ds]
        idx = ds.index(chon) if chon in ds else (ds.index("vi-VN") if "vi-VN" in ds else 0)
        self.ngon_ngu.current(idx)
        self._nap_giong(giong)
        if not self.cd.get("da_gioi_thieu_tu_dong"):   # bản có cảm xúc tự động: đặt làm mặc định một lần
            self.cd["da_gioi_thieu_tu_dong"] = True
            self.cam_xuc.set(gc.MUC_TU_DONG)
            self._cap_nhat_nhan_giong()
        if getattr(self, "_gioi_thieu_clone", False):
            self.trang_thai.set("Bản này có giọng Arthur (clone): đã chọn sẵn. Muốn giọng Edge thì đổi ô Ngôn ngữ, "
                                "muốn quay lại Arthur bấm ★ Giọng Arthur.")

    def _nap_giong(self, giong_chon=None):
        loc = self._locales[self.ngon_ngu.current()]
        if loc == MA_CLONE:
            ds_cl = gc.ds_giong()
            self._ds_giong = [gc.TIEN_TO_CLONE + slug for slug in ds_cl] or [gc.TIEN_TO_CLONE]
            self.chon_giong["values"] = ([f"{v['ten']} · {v.get('gioi', '')}" for v in ds_cl.values()]
                                         or ["(chưa có, bấm Nhập giọng)"])
            luu = giong_chon or self.cd.get("giong")
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
        luu = giong_chon or self.cd.get("giong")
        if luu in self._ds_giong:
            self.chon_giong.current(self._ds_giong.index(luu))
        elif self._ds_giong and self._ds_giong[0].startswith(gc.TIEN_TO_CLONE):
            self.chon_giong.current(0)       # tiếng Anh: Arthur đứng đầu và là giọng mặc định
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

    def mo_ta_giong(self):
        """Chữ cho nhãn "Giọng đang chọn" và thanh trạng thái: tên giọng (+ mức cảm xúc nếu là clone)."""
        if not getattr(self, "_ds_giong", None):
            return ""
        ma = self._ds_giong[self.chon_giong.current()]
        if ma.startswith(gc.TIEN_TO_CLONE):
            v = gc.ds_giong().get(ma[len(gc.TIEN_TO_CLONE):])
            if not v:
                return "chưa có giọng clone (bấm Nhập giọng)"
            return f"{v['ten']} · {self.cam_xuc.get() or gc.MUC_MAC_DINH}"
        v = next((x for x in self.giong if x["ShortName"] == ma), None)
        return f"{ten_hien_thi(v) if v else ma} (Edge)"

    def _cap_nhat_nhan_giong(self):
        try:
            ten = self.mo_ta_giong()
            self.nhan_giong.config(text=f"Giọng đang chọn: {ten}" if ten else "",
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

    def _kiem_kich_ban(self):
        """Kiểm kịch bản trước khi đọc: liệt kê chỗ dễ làm bản đọc không đạt, bấm đúp để nhảy tới chỗ đó."""
        t = self.o_chu.get("1.0", "end-1c")
        if not t.strip():
            messagebox.showinfo("Đọc Voice", "Chưa có nội dung bên bảng đọc để kiểm.")
            return
        ds = kbk.kiem(t)
        tt = kbk.tom_tat(ds)
        if not ds:
            messagebox.showinfo("Kiểm kịch bản", "Kịch bản sạch: không thấy chỗ nào dễ làm bản đọc không đạt.\n"
                                                 "Bấm Chuyển đổi, rồi bấm Kiểm chất lượng sau khi đọc xong.")
            self.trang_thai.set("Kiểm kịch bản: không thấy chỗ nào cần sửa.")
            return
        cua = tk.Toplevel(self)
        cua.title(f"Kiểm kịch bản: {tt['sua']} chỗ nên sửa, {tt['luu_y']} chỗ lưu ý")
        cua.geometry("860x460")
        cua.transient(self)
        ttk.Label(cua, text="Nên sửa trước khi đọc (đỏ) và lưu ý (vàng). Bấm đúp một dòng để nhảy tới chỗ đó trong bảng đọc.",
                  foreground="#374151").pack(anchor="w", padx=12, pady=(10, 4))
        cay = ttk.Treeview(cua, columns=("muc", "dong", "trich", "goi_y"), show="headings", height=14)
        for cot, ten, rong in (("muc", "Mức", 70), ("dong", "Dòng", 50), ("trich", "Chỗ đó", 200), ("goi_y", "Cách sửa", 480)):
            cay.heading(cot, text=ten)
            cay.column(cot, width=rong, anchor="w", stretch=(cot == "goi_y"))
        cay.tag_configure("sua", background="#fee2e2")
        cay.tag_configure("luu_y", background="#fef9c3")
        for i, e in enumerate(ds):
            cay.insert("", "end", iid=str(i), tags=(e["muc"],),
                       values=("Nên sửa" if e["muc"] == "sua" else "Lưu ý", e["dong"], e["trich"], e["goi_y"]))
        cay.pack(fill="both", expand=True, padx=12, pady=4)
        self.o_chu.tag_config("canh_bao", background="#fecaca")

        def nhay(_=None):
            sel = cay.selection()
            if not sel:
                return
            e = ds[int(sel[0])]
            self.o_chu.tag_remove("canh_bao", "1.0", "end")
            a, b = f"1.0+{e['bd']}c", f"1.0+{e['kt']}c"
            self.o_chu.tag_add("canh_bao", a, b)
            self.o_chu.see(a)
            self.o_chu.mark_set("insert", a)

        def sua_an_toan():
            moi, doi = kbk.tu_sua_an_toan(self.o_chu.get("1.0", "end-1c"))
            if not doi:
                messagebox.showinfo("Kiểm kịch bản", "Không có gì sửa tự động được. Làm theo cột Cách sửa.", parent=cua)
                return
            self.o_chu.delete("1.0", "end")
            self.o_chu.insert("1.0", moi)
            self._dem_chu()
            self.trang_thai.set("Đã sửa tự động: bỏ emoji, dấu lặp, từ gõ lặp, gạch dài. Bấm Kiểm kịch bản lại để xem còn gì.")
            cua.destroy()
        cay.bind("<Double-1>", nhay)
        cay.bind("<<TreeviewSelect>>", nhay)
        hang = ttk.Frame(cua)
        hang.pack(fill="x", padx=12, pady=(4, 10))
        ttk.Button(hang, text="Sửa tự động phần an toàn", command=sua_an_toan).pack(side="left")
        ttk.Button(hang, text="Đóng", command=cua.destroy).pack(side="right")
        self.trang_thai.set(f"Kiểm kịch bản: {tt['sua']} chỗ nên sửa, {tt['luu_y']} chỗ lưu ý.")

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

    def _xem_chapters(self):
        van_ban = self.o_chu.get("1.0", "end").strip()
        if not van_ban:
            messagebox.showinfo("Phân cảnh YouTube", "Chưa có nội dung bên bảng đọc để trích xuất Chapters.")
            return
        cau_list = getattr(self, "_danh_sach_cau", None)
        toc = self.toc_do.get()
        noi_dung = trich_xuat_chapters(van_ban, cau_list, toc)
        if not noi_dung.strip():
            messagebox.showinfo(
                "Phân cảnh YouTube",
                "Chưa phát hiện tiêu đề phân cảnh nào trong kịch bản.\n\n"
                "Mẹo: Hãy thêm các dòng tiêu đề phân cảnh như:\n"
                "# Giới thiệu\n"
                "Phần 1: Khởi đầu mới\n"
                "Chương 2 - Nguyên nhân cốt lõi\n"
                "Chapter 3: Giải pháp công nghệ\n\n"
                "Dòng thường như \"Phần lớn...\" hay \"1. Mua gỗ\" không bị coi là phân cảnh."
            )
            return

        cua = tk.Toplevel(self)
        cua.title("📋 Phân cảnh YouTube (Chapters Description)")
        cua.geometry("540x430")
        cua.transient(self)

        khung = ttk.Frame(cua, padding=14)
        khung.pack(fill="both", expand=True)

        ttk.Label(khung, text="Danh sách Chapters cho phần Mô tả YouTube", font=("Segoe UI Semibold", 12)).pack(anchor="w")
        ttk.Label(khung, text="Định dạng chuẩn YouTube (tự động bắt đầu từ 00:00 và dãn cách trên 10 giây).",
                  foreground="#6b7280").pack(anchor="w", pady=(2, 8))

        o_text = tk.Text(khung, font=("Consolas", 11), wrap="word", relief="solid", borderwidth=1, padx=8, pady=8)
        o_text.pack(fill="both", expand=True, pady=(0, 10))
        o_text.insert("1.0", noi_dung)

        hang_nut = ttk.Frame(khung)
        hang_nut.pack(fill="x")

        def sao_chep():
            self.clipboard_clear()
            self.clipboard_append(o_text.get("1.0", "end").strip())
            self.trang_thai.set("Đã sao chép Chapters vào Clipboard.")
            messagebox.showinfo("Chapters YouTube", "Đã sao chép toàn bộ Chapters vào Clipboard. Dán thẳng vào ô Mô tả video YouTube.", parent=cua)

        def luu_file():
            p = filedialog.asksaveasfilename(
                initialdir=self.thu_muc_ra,
                initialfile="chapters.txt",
                defaultextension=".txt",
                filetypes=[("Text file", "*.txt")],
                parent=cua
            )
            if p:
                with open(p, "w", encoding="utf-8") as f:
                    f.write(o_text.get("1.0", "end").strip())
                self.trang_thai.set(f"Đã lưu Chapters: {os.path.basename(p)}")

        ttk.Button(hang_nut, text="📋 Sao chép vào Clipboard", style="Xanh.TButton", command=sao_chep).pack(side="left")
        ttk.Button(hang_nut, text="💾 Lưu file .txt", command=luu_file).pack(side="left", padx=8)
        ttk.Button(hang_nut, text="Đóng", command=cua.destroy).pack(side="right")

    def _xem_seo(self):
        van_ban = self.o_chu.get("1.0", "end").strip()
        if not van_ban:
            messagebox.showinfo("Trợ lý SEO YouTube", "Chưa có nội dung bên bảng đọc để phân tích SEO.")
            return
        dulieu = trich_xuat_seo(van_ban)

        cua = tk.Toplevel(self)
        cua.title("🎯 Trợ lý SEO YouTube (Tiêu đề, Hook, Tags và Hashtags)")
        cua.geometry("640x570")
        cua.transient(self)

        khung = ttk.Frame(cua, padding=14)
        khung.pack(fill="both", expand=True)

        ttk.Label(khung, text="Gợi ý tối ưu hóa SEO YouTube từ kịch bản", font=("Segoe UI Semibold", 12)).pack(anchor="w")

        # 1. Tiêu đề gợi ý
        f_td = ttk.LabelFrame(khung, text=" Gợi ý Tiêu đề video ", padding=8)
        f_td.pack(fill="x", pady=(6, 8))
        if dulieu["tieu_de"]:
            for td in dulieu["tieu_de"]:
                h = ttk.Frame(f_td)
                h.pack(fill="x", pady=2)
                e = ttk.Entry(h)
                e.insert(0, td)
                e.pack(side="left", fill="x", expand=True, padx=(0, 6))
                def copy_td(t=td):
                    self.clipboard_clear()
                    self.clipboard_append(t)
                    self.trang_thai.set(f"Đã sao chép tiêu đề: {t}")
                ttk.Button(h, text="Chép", width=6, command=copy_td).pack(side="right")
        else:
            ttk.Label(f_td, text="Chưa có gợi ý tiêu đề.").pack(anchor="w")

        # 2. Đoạn mô tả Hook (2 dòng đầu)
        f_hook = ttk.LabelFrame(khung, text=" Đoạn mô tả mở đầu (Hook 2 dòng đầu kết quả tìm kiếm) ", padding=8)
        f_hook.pack(fill="x", pady=(0, 8))
        o_hook = tk.Text(f_hook, height=3, font=("Segoe UI", 10), wrap="word", relief="solid", borderwidth=1, padx=6, pady=4)
        o_hook.pack(fill="x", pady=(0, 4))
        o_hook.insert("1.0", dulieu["mo_ta_hook"])
        def copy_hook():
            self.clipboard_clear()
            self.clipboard_append(o_hook.get("1.0", "end").strip())
            self.trang_thai.set("Đã sao chép mô tả Hook.")
        ttk.Button(f_hook, text="Chép Hook", command=copy_hook).pack(anchor="e")

        # 3. Tags chuẩn YouTube (< 500 ký tự)
        tag_len = len(dulieu["tags"])
        f_tags = ttk.LabelFrame(khung, text=f" Bộ từ khóa Tags ({tag_len}/500 ký tự chuẩn YouTube) ", padding=8)
        f_tags.pack(fill="x", pady=(0, 8))
        o_tags = tk.Text(f_tags, height=3, font=("Segoe UI", 10), wrap="word", relief="solid", borderwidth=1, padx=6, pady=4)
        o_tags.pack(fill="x", pady=(0, 4))
        o_tags.insert("1.0", dulieu["tags"])
        def copy_tags():
            self.clipboard_clear()
            self.clipboard_append(o_tags.get("1.0", "end").strip())
            self.trang_thai.set("Đã sao chép Tags.")
        ttk.Button(f_tags, text="Chép Tags (Dán vào mục Thẻ)", command=copy_tags).pack(anchor="e")

        # 4. Hashtags
        f_hash = ttk.LabelFrame(khung, text=" Gợi ý Hashtags ", padding=8)
        f_hash.pack(fill="x", pady=(0, 10))
        h_box = ttk.Frame(f_hash)
        h_box.pack(fill="x")
        e_hash = ttk.Entry(h_box)
        e_hash.insert(0, dulieu["hashtags"])
        e_hash.pack(side="left", fill="x", expand=True, padx=(0, 6))
        def copy_hash():
            self.clipboard_clear()
            self.clipboard_append(e_hash.get().strip())
            self.trang_thai.set("Đã sao chép Hashtags.")
        ttk.Button(h_box, text="Chép", width=6, command=copy_hash).pack(side="right")

        ttk.Button(khung, text="Đóng", command=cua.destroy).pack(side="right")

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
                canh_bao_dich = []
                ban_dich = dich_tieng_viet(chuan_bi_van_ban_doc(van_ban), canh_bao=canh_bao_dich)
                def cap_nhat():
                    self.o_dich.delete("1.0", "end")
                    self.o_dich.insert("1.0", ban_dich)
                    self._nguon_ban_dich = van_ban
                    luu_y = f" Lưu ý: {canh_bao_dich[0]}" if canh_bao_dich else ""
                    self.trang_thai.set(f"Đã dịch xong ({len(ban_dich.split())} từ tiếng Việt).{luu_y}")
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
        """Mỗi 80 ms hỏi MCI vị trí đang phát rồi tô câu có mốc bắt đầu vừa qua."""
        if self._song_truc is not None:
            self._theo_doi_song_truc()
            return
        if not self._dang_theo_doi_phat:
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
        """cau_list: [(bắt đầu, kết thúc, chữ)] theo thời gian của file. Ghép vào câu của ô văn bản."""
        if cau_list:
            self._danh_sach_cau = cau_list
        self._dong_thoi_gian = gc.gan_moc_vao_cau(self._danh_sach_cau or [], self._chup_cau_nguon())
        self._song_truc = None
        self._cau_highlight_cu = -1
        self._vt_tim = 0
        self._lech_to = []
        self._thoi_gian_phat = time.time()
        self._dang_theo_doi_phat = True
        phat(duong_dan, self._am_luong_phat)
        self._theo_doi_phat()

    # --- nghe ngay khi máy còn đang sinh (giọng clone): phát từng câu xong, hết câu phát câu kế ---
    def _co_phan_moi(self, i, thong_tin):
        self.after(0, self._nhan_phan, i, thong_tin)

    def _nhan_phan(self, i, tt):
        if not getattr(self, "_nghe_ngay", False):
            return
        self._phan_san[i] = tt
        st = self._song_truc
        if st is None and i == 0 and not self._dang_theo_doi_phat:
            self._chup_cau_nguon()
            self._song_truc = {"i": 0, "tong": tt.get("tong", 0), "cho_tu": None, "da_choi": False,
                               "t_phat": time.time()}
            self._t_nghe_dau = time.time()
            self._phat_phan(0)
            self.after(80, self._theo_doi_phat)
        elif st is not None and st.get("dem") and i == st["i"]:
            self._phat_phan(i)

    def _phat_phan(self, i):
        st = self._song_truc
        tt = self._phan_san[i]
        nghe = os.path.join(THU_MUC_CLONE_TAM, "nghe_ngay", f"{i % 4}.wav")   # bản sao: app không giữ file gốc
        try:
            os.makedirs(os.path.dirname(nghe), exist_ok=True)
            mci("close docvoice")
            shutil.copyfile(tt["wav"], nghe)
        except OSError:
            nghe = tt["wav"]
        st.update({"i": i, "dem": False, "da_choi": False, "cho_tu": None, "t_phat": time.time()})
        phat(nghe, self._am_luong_phat)
        cau = tt.get("cau")
        if cau is not None and (cau, cau) != self._cau_highlight_cu:
            self._highlight_cau(cau)
            self._ghi_hl(0.0, (cau, cau), 0.0, "nghe_ngay")
        if self.dang_chay:
            self.trang_thai.set(f"Đang nghe câu {i + 1}/{st['tong']} trong lúc máy đọc sinh tiếp...")

    def _theo_doi_song_truc(self):
        st = self._song_truc
        if st is None:
            return
        if st.get("dem"):                      # đang chờ máy sinh xong câu kế
            if st["i"] in self._phan_san:
                self._phat_phan(st["i"])
        else:
            _, dang = vi_tri_phat()
            if dang:
                st["da_choi"] = True
            elif st["da_choi"] or time.time() - st["t_phat"] > 3:     # hết câu (hoặc MCI không mở được)
                if st["cho_tu"] is None:
                    st["cho_tu"] = time.time()
                ke = st["i"] + 1
                if ke >= st["tong"]:
                    self._song_truc = None
                    self._xoa_highlight()
                    if not self.dang_chay:
                        self.trang_thai.set("Đã nghe hết. Bấm Phát lại để nghe bản mp3 đã hậu kỳ.")
                    return
                if time.time() - st["cho_tu"] >= gc.lang_sau(self._phan_san.get(st["i"], {}).get("ngat", "cau")):
                    if ke in self._phan_san:
                        self._phat_phan(ke)
                    else:
                        st.update({"i": ke, "dem": True})
                        if self.dang_chay:
                            self.trang_thai.set(f"Đang đệm câu {ke + 1}: máy đọc đang sinh...")
        if self._song_truc is st and not st.get("dem") and self._cau_highlight_cu is None:
            cau = self._phan_san.get(st["i"], {}).get("cau")      # bảng dịch vừa dựng lại: tô lại câu đang nghe
            if cau is not None:
                self._highlight_cau(cau)
        self.after(80, self._theo_doi_phat)

    def _dung_phat_app(self):
        self._dang_theo_doi_phat = False
        self._song_truc = None
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
        self.trang_thai.set(f"Đang đọc bằng {self.mo_ta_giong()}...")
        td = doc_tu_dien()
        van_ban_goc = van_ban  # bản người dùng viết (còn #) cho Chapters
        van_ban = chuan_bi_van_ban_doc(van_ban)  # chữ đọc và phụ đề không có #
        self._tu_dien_luot = (ban_do_cau(van_ban, td), tu_dien_cho_srt(van_ban, td))
        self._t_bat_dau_luot = time.time()
        self._t_nghe_dau = None
        self._tien = {"chu": "Đang chuẩn bị", "pt": 0.0, "t_doi": time.time(), "lich_su": []}
        if self._hen_nhip is None:
            self._hen_nhip = self.after(int(GIAY_NHIP_TIEN_DO * 1000), self._nhip_tien_do)
        if giong.startswith(gc.TIEN_TO_CLONE):
            self._dung_clone.clear()
            threading.Thread(target=self._lam_clone, args=(van_ban, td, duong_ra, giong, nghe_thu, luong, bat_dau),
                             daemon=True).start()
            return
        van_ban_dich = self.o_dich.get("1.0", "end").strip() if hasattr(self, "o_dich") else ""
        self._canh_bao_pause = []
        khoi_list = tach_doan_va_pause(van_ban, self._canh_bao_pause)
        if self._canh_bao_pause:
            self.trang_thai.set(f"Lưu ý: {len(self._canh_bao_pause)} khoảng nghỉ vượt {TOI_DA_PAUSE:g} giây "
                                f"đã rút còn {TOI_DA_PAUSE:g} giây.")
        threading.Thread(target=self._lam,
                         args=(khoi_list, van_ban_goc, van_ban_dich, duong_ra, giong, toc, cao, nghe_thu, td),
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

            def chay_doc(bd=bat_dau, ph=phat_cb):
                return doc.chay(van_ban, slug, muc, duong_ra, wav_ra,
                                "" if nghe_thu else os.path.splitext(duong_ra)[0] + ".srt",
                                kieu_shorts=self.cd.get("kieu_shorts", False),
                                chia_shorts=lambda c: chia_cau_shorts(c, max_tu=7),
                                ap_cach_doc=lambda c: ap_tu_dien(c, td), toc=max(0.5, min(2.0, toc)),
                                bat_dau=bd, phat=ph)
            kq = chay_doc()
            if kq.get("dung") is False and getattr(self, "_chuong_luot", None) and not nghe_thu:
                with open(os.path.splitext(duong_ra)[0] + ".chapters.txt", "w", encoding="utf-8") as f:
                    f.write(chapters_youtube(self._chuong_luot, kq["muc_tg"]) + "\n")
            if kq.get("dung"):
                self.after(0, self._bao, f"Đã dừng ở {kq['xong']}/{kq['tong']} câu. Bấm Chuyển đổi lại "
                                         "cùng văn bản và cùng mức cảm xúc để chạy tiếp.", kq["xong"] / kq["tong"] * 95)
                ket = {"ok": False, "dung": True, **kq}
                return
            self.file_cuoi = duong_ra
            self._viec_cuoi = {"viec": kq["thu_muc"], "mp3": duong_ra, "ref": gc.duong_mau(slug),
                               "kho": os.path.join(THU_MUC_CLONE_TAM, "cau", slug), "doc_lai": lambda: chay_doc(0, None),
                               "ten": os.path.basename(duong_ra)}
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

    # ---- kiểm chất lượng: module kiem_chat_luong.py, ở đây chỉ nối nút
    def _kiem_chat_luong(self):
        v = getattr(self, "_viec_cuoi", None)
        if self.dang_chay or not v:
            messagebox.showinfo("Đọc Voice", "Chưa có bản đọc giọng clone nào xong trong phiên này để kiểm. "
                                             "Chuyển đổi một bản bằng giọng clone rồi bấm lại.")
            return
        with self._khoa_trang_thai:
            self.dang_chay = True
        self.nut_chuyen.state(["disabled"])
        self.trang_thai.set("Đang kiểm chất lượng bản vừa đọc...")
        threading.Thread(target=self._lam_kiem, args=(v,), daemon=True).start()

    def _lam_kiem(self, v):
        try:
            py = self.cd.get("python_kiem", "")
            bc = kcl.kiem_mot_viec(v["viec"], v["mp3"], v["ref"], v["ten"], True, 2, v["doc_lai"], v["kho"],
                                   python=py, thu_muc_tam=os.path.join(THU_MUC_CLONE_TAM, "kiem"))
            chu = "ĐẠT" if bc["dat"] else "KHÔNG ĐẠT: " + "; ".join(bc["ly_do"][:3])
            chua = ("\n" + bc["chua_kiem"][0]) if bc.get("chua_kiem") else ""
            html_bc = bc["bao_cao"][1]
            self.after(0, self._bao, f"Kiểm chất lượng: {chu}", 100)
            self.after(0, lambda: messagebox.askyesno(
                "Kiểm chất lượng", f"{chu}{chua}\n\nBáo cáo: {html_bc}\n\nMở báo cáo?") and os.startfile(html_bc))
        except Exception as e:
            self.after(0, self._bao, f"Không kiểm được: {e}", 0)
        finally:
            self.after(0, self._xong)

    def _chay_nhieu_kich_ban(self):
        if self.dang_chay:
            return
        ds = filedialog.askopenfilenames(title="Chọn các file kịch bản (.txt)", filetypes=[("Văn bản", "*.txt")])
        if not ds:
            return
        giong, _, _ = self._tham_so()
        if not giong.startswith(gc.TIEN_TO_CLONE):
            messagebox.showinfo("Đọc Voice", "Chạy nhiều kịch bản dùng giọng clone. Chọn giọng clone rồi bấm lại.")
            return
        slug, muc = giong[len(gc.TIEN_TO_CLONE):], self.cam_xuc.get() or gc.MUC_MAC_DINH
        td = doc_tu_dien()
        kb = [(os.path.splitext(os.path.basename(f))[0], open(f, encoding="utf-8-sig").read()) for f in ds]
        with self._khoa_trang_thai:
            self.dang_chay = True
        self.nut_chuyen.state(["disabled"])
        threading.Thread(target=self._lam_nhieu, args=(kb, slug, muc, td), daemon=True).start()

    def _lam_nhieu(self, kb, slug, muc, td):
        try:
            may = self._chon_may_doc()
            doc = gc.DocDaiClone(THU_MUC_CLONE_TAM, may, None, bao=lambda c, pt: self.after(0, self._bao, c, pt),
                                 dung=self._dung_clone)

            def doc_mot(ten, vb):
                ra = os.path.join(self.thu_muc_ra, "kiem-" + re.sub(r"[^\w.-]+", "_", ten) + ".mp3")
                toc = gc.MUC_CAM_XUC.get(muc, {}).get("toc", 1.0)

                def chay():
                    return doc.chay(vb, slug, muc, ra, "", "", ap_cach_doc=lambda c: ap_tu_dien(c, td), toc=toc)
                kq = chay()
                return {"mp3": ra, "viec": kq["thu_muc"], "ref": gc.duong_mau(slug), "doc_lai": chay,
                        "kho": os.path.join(THU_MUC_CLONE_TAM, "cau", slug)}
            dong, _, duong = kcl.chay_nhieu(kb, doc_mot, thu_muc_ra=self.thu_muc_ra,
                                            bao=lambda c: self.after(0, self._bao, c, 0))
            dat = sum(1 for d in dong if d["dat"])
            self.after(0, self._bao, f"Chạy nhiều kịch bản xong: {dat}/{len(dong)} ĐẠT. Bảng tổng: {duong}", 100)
        except Exception as e:
            self.after(0, self._bao, f"Lỗi chạy nhiều kịch bản: {e}", 0)
        finally:
            self.after(0, self._xong)

    def _lam(self, khoi_list, van_ban_goc, van_ban_dich, duong_ra, giong, toc, cao, nghe_thu, td):
        self._cue_dong_dich = {}
        canh_bao_vi = []
        danh_sach_muc = []
        for loai, val in khoi_list:
            if loai == "pause":
                danh_sach_muc.append(("pause", val))
            else:
                chu_ap_td = ap_tu_dien(gc.bo_the(val), td)   # thẻ [hồi hộp]... không đọc thành chữ
                for d in chia_doan(chu_ap_td):
                    if re.search(r"\w", d):
                        danh_sach_muc.append(("text", d))

        if not danh_sach_muc:
            self.after(0, self._bao, "Không có chữ nào để đọc", 0)
            self.after(0, lambda: messagebox.showwarning("Đọc Voice", "Văn bản không có chữ nào để đọc."))
            self.after(0, self._xong)
            return

        xong = [0]
        so_text = sum(1 for m in danh_sach_muc if m[0] == "text") or 1

        async def tat_ca():
            so_ss = self.cd.get("song_song", SONG_SONG)
            khoa = asyncio.Semaphore(so_ss)

            async def mot(muc):
                loai, val = muc
                if loai == "pause":
                    mp3_data = tao_khoang_lang_mp3(val)
                    return (mp3_data, [(0.0, val, "")])
                else:
                    async with khoa:
                        self.after(0, lambda d=val: self._highlight_doan_dang_chuyen(d))
                        b = await tao_doan(val, giong, toc, cao)
                    xong[0] += 1
                    self.after(0, self._bao, f"Đã chuyển {xong[0]}/{so_text} đoạn...", xong[0] / so_text * 90)
                    return b

            return await asyncio.gather(*(mot(m) for m in danh_sach_muc))

        try:
            self.after(0, self._bao, f"Đang chuyển {so_text} đoạn kịch bản...", 0)
            ket_qua = asyncio.run(tat_ca())
            self.after(0, self._bao, "Đang xuất mp3 và srt...", 96)
            tam = duong_ra + ".part"
            with open(tam, "wb") as f:
                for b, _ in ket_qua:
                    f.write(b)
            os.replace(tam, duong_ra)

            cau_list = lay_danh_sach_cau(ket_qua, self._tu_dien_luot)
            self._danh_sach_cau = cau_list

            if not nghe_thu:
                kieu_shorts = self.cd.get("kieu_shorts", False)
                ten_goc = os.path.splitext(duong_ra)[0]
                with open(ten_goc + ".srt", "w", encoding="utf-8") as f:
                    f.write(tao_srt(ket_qua, self._tu_dien_luot, kieu_shorts=kieu_shorts))

                if self.cd.get("xuat_song_ngu", True) and van_ban_dich:
                    # Câu nguồn tách lại từ văn bản đang đọc; văn bản đã đổi sau khi dịch thì thử bản đã dịch.
                    nguon = [chuan_bi_van_ban_doc(van_ban_goc),
                             chuan_bi_van_ban_doc(getattr(self, "_nguon_ban_dich", "") or "")]
                    nhom_vi = nhom_phu_de_dich(cau_list, van_ban_dich, nguon, canh_bao_vi)
                    self._cue_dong_dich = {j: g[3] for g in nhom_vi for j in g[4]}
                    srt_vi = tao_srt_song_ngu(cau_list, van_ban_dich, kieu_shorts=kieu_shorts, van_ban_nguon=nguon)
                    if srt_vi.strip():
                        with open(ten_goc + "_vi.srt", "w", encoding="utf-8") as f:
                            f.write(srt_vi)

                toc_so = self.toc_do.get() if hasattr(self, "toc_do") else 0
                chapters = trich_xuat_chapters(van_ban_goc, cau_list, toc_so)
                if chapters.strip():
                    self._chapters_hien_tai = chapters
                    if self.cd.get("xuat_chapters", True):
                        with open(ten_goc + "_chapters.txt", "w", encoding="utf-8") as f:
                            f.write(chapters)

                self._seo_hien_tai = trich_xuat_seo(van_ban_goc)

            self.file_cuoi = duong_ra
            ghi_chu = (f" (có {len(self._canh_bao_pause)} khoảng nghỉ rút còn {TOI_DA_PAUSE:g} giây)"
                       if self._canh_bao_pause else "")
            if canh_bao_vi:
                ghi_chu += f". {canh_bao_vi[0]}"
            self.after(0, self._bao, "Đang phát thử..." if nghe_thu else f"Xong: {duong_ra}{ghi_chu}", 100)
            self._ket_qua_luot = {"ok": True, "mp3": duong_ra, "so_cau": len(cau_list)}
            if not self._tu_dong or self._tu_dong.get("phat_sau"):
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
        if ban_day_du():
            if chu_dong:
                self.after(0, lambda: messagebox.showinfo(
                    "Đọc Voice", f"Đây là bản đầy đủ v{PHIEN_BAN} (có mẫu Arthur cho Chuẩn cao). Bản trên GitHub là bản "
                    "công khai không kèm mẫu, nên bản này không tự cập nhật. Lên bản mới: dựng lại bằng dong-goi.ps1."))
            return
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
        """Mọi đường đọc (Edge, clone, nghe thử) báo tiến độ qua đây: thanh tiến độ và dòng trạng thái đầy đủ."""
        now = time.time()
        t0 = getattr(self, "_t_bat_dau_luot", None)
        if not self.dang_chay or t0 is None:       # ngoài lúc đọc: hiện nguyên câu
            self.trang_thai.set(chu)
            self.tien_do["value"] = phan_tram
            return
        tt = self._tien
        if tt.get("pt") != phan_tram or tt.get("chu") != chu:
            tt["t_doi"] = now                      # có tiến triển mới: đếm lại giây của bước
        if tt.get("pt") != phan_tram:
            tt.setdefault("lich_su", []).append((now - t0, phan_tram))
            tt["con0"] = uoc_ket_hop(tt["lich_su"], now - t0, phan_tram)    # chỉ cập nhật khi có tiến triển
            tt["t_con0"] = now
        tt["chu"], tt["pt"] = chu, phan_tram
        self.tien_do["value"] = phan_tram
        nk = (self._tu_dong or {}).get("nhat_ky")
        if nk and chu != getattr(self, "_bao_cuoi", None):     # chế độ tự thử: ghi nhật ký từng bước
            self._bao_cuoi = chu
            try:
                with open(nk, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {phan_tram:5.1f}% {chu}\n")
            except OSError:
                pass
        self._ve_tien_do()

    def _ve_tien_do(self):
        tt = self._tien
        now = time.time()
        t0 = self._t_bat_dau_luot
        con0 = tt.get("con0")
        con = None if con0 is None else con0 - (now - tt.get("t_con0", now))    # đếm ngược giữa hai lần tiến triển
        dong = dong_tien_do(tt.get("chu", ""), tt.get("pt", 0.0), now - t0, now - tt.get("t_doi", now), 0.0, con)
        self.trang_thai.set(dong)
        if self._nhat_ky_tien_do is not None:
            self._nhat_ky_tien_do.append({"t": round(now - t0, 1), "pt": round(tt.get("pt", 0.0), 1), "dong": dong})

    def _nhip_tien_do(self):
        """Làm mới mỗi giây khi đang đọc, để thời gian chạy vẫn nhảy ngay cả lúc một câu đọc lâu."""
        if not self.dang_chay:
            self._hen_nhip = None
            return
        if self._tien.get("chu"):
            self._ve_tien_do()
        self._hen_nhip = self.after(int(GIAY_NHIP_TIEN_DO * 1000), self._nhip_tien_do)

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
        cua.geometry("600x740")
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
        var_song_ngu = tk.BooleanVar(value=self.cd.get("xuat_song_ngu", True))
        ttk.Checkbutton(f_yt, text="Tự động xuất phụ đề tiếng Việt (_vi.srt) khi có bản dịch",
                        variable=var_song_ngu).pack(anchor="w", pady=(0, 4))
        var_chapters = tk.BooleanVar(value=self.cd.get("xuat_chapters", True))
        ttk.Checkbutton(f_yt, text="Tự động xuất mốc phân cảnh YouTube (_chapters.txt) từ kịch bản",
                        variable=var_chapters).pack(anchor="w", pady=(0, 4))
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
        var_nghe = tk.BooleanVar(value=self.cd.get("nghe_ngay", True))
        ttk.Checkbutton(f_cl, text="Nghe ngay từ câu đầu trong lúc máy còn đọc các câu sau",
                        variable=var_nghe).pack(anchor="w")

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
            self.cd["xuat_song_ngu"] = var_song_ngu.get()
            self.cd["xuat_chapters"] = var_chapters.get()
            self.cd["tu_cuon"] = var_cuon.get()
            self.cd["tu_kiem_cap_nhat"] = var_upd.get()
            self.cd["thu_muc_studio"] = var_studio.get().strip() or r"E:\DG Voice Studio"
            self.cd["kiem_whisper"] = var_kiem.get()
            self.cd["xuat_wav"] = var_wav.get()
            self.cd["nghe_ngay"] = var_nghe.get()
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
                "nguon_ban_dich": (getattr(self, "_nguon_ban_dich", "") or "")[:50000],
                "thu_muc_ra": self.thu_muc_ra,
                "kieu_shorts": self.cd.get("kieu_shorts", False),
                "xuat_song_ngu": self.cd.get("xuat_song_ngu", True),
                "xuat_chapters": self.cd.get("xuat_chapters", True),
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
        if kb.get("ngon_ngu"):          # không ghi ngôn ngữ thì giữ đúng giọng app tự chọn lúc mở
            self.ngon_ngu.current(self._locales.index(kb["ngon_ngu"]))
            self._nap_giong()
        if kb.get("giong") in self._ds_giong:
            self.chon_giong.current(self._ds_giong.index(kb["giong"]))
        self._cap_nhat_cam_xuc()
        if kb.get("cam_xuc"):
            self.cam_xuc.set(kb["cam_xuc"])
        self._cap_nhat_nhan_giong()
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
        if kb.get("anh_giua"):          # ảnh chụp giữa lúc đang đọc, để thấy dòng tiến độ
            def chup_giua():
                try:
                    chup_cua_so(ctypes.windll.user32.GetParent(self.winfo_id()), kb["anh_giua"])
                except Exception:
                    pass
            self.after(int(float(kb.get("anh_giua_s", 12)) * 1000), chup_giua)
        if kb.get("cho_nong"):           # như người dùng mở app vài giây rồi mới bấm
            self._lam_nong()
            self.after(int(kb["cho_nong"] * 1000), bam)
        elif kb.get("anh"):
            self.after(800, lambda: (chup_cua_so(ctypes.windll.user32.GetParent(self.winfo_id()), kb["anh"]), bam()))
        else:
            bam()

    def _ket_thuc_tu_dong(self):
        kb = self._tu_dong or {}
        dang_nghe = self._dang_theo_doi_phat or self._song_truc is not None
        con_cho = time.time() - getattr(self, "_t_cho_ket", time.time()) < float(kb.get("cho_phat_toi_da", 900))
        if (kb.get("phat_sau") or kb.get("nghe_ngay")) and dang_nghe and con_cho:
            if not hasattr(self, "_t_cho_ket"):
                self._t_cho_ket = time.time()
            self.after(500, self._ket_thuc_tu_dong)     # nghe hết rồi mới ghi kết quả
            return
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
        if getattr(self, "_nhat_ky_hl", None) is not None:
            lech = [h["vi_tri"] - h["bd"] for h in self._nhat_ky_hl
                    if h["che_do"] == "mp3" and h["vi_tri"] is not None and h["bd"] is not None]
            ket["highlight"] = {"so_lan_to": len(self._nhat_ky_hl), "so_lan_do_lech": len(lech),
                                "lech_max_giay": round(max(lech), 3) if lech else None,
                                "lech_tb_giay": round(sum(lech) / len(lech), 3) if lech else None,
                                "nghe_ngay_so_cau": sum(1 for h in self._nhat_ky_hl if h["che_do"] == "nghe_ngay"),
                                "giay_den_tieng_dau": round(self._t_nghe_dau - self._t_bat_dau_luot, 1)
                                if getattr(self, "_t_nghe_dau", None) and getattr(self, "_t_bat_dau_luot", None) else None,
                                "nhat_ky": self._nhat_ky_hl[:400]}
        if getattr(self, "_nhat_ky_tien_do", None) is not None:
            ket["tien_do"] = self._nhat_ky_tien_do[:600]
        ket.update({"giong": self._tham_so()[0], "cam_xuc": self.cam_xuc.get(), "phien_ban": PHIEN_BAN,
                    "ngon_ngu": self._locales[self.ngon_ngu.current()],
                    "giong_hien_thi": self.nhan_giong.cget("text"),
                    "trang_thai_cuoi": self.trang_thai.get()})
        if kb.get("anh_xong"):
            try:
                chup_cua_so(ctypes.windll.user32.GetParent(self.winfo_id()), kb["anh_xong"])
            except Exception:
                pass
        if kb.get("ket_qua"):
            ghi_json(kb["ket_qua"], ket)
        self.destroy()

    def destroy(self):
        if getattr(self, "_hen_nhip", None):
            try:
                self.after_cancel(self._hen_nhip)
            except Exception:
                pass
            self._hen_nhip = None
        super().destroy()

    def _thoat(self):
        if hasattr(self, "_ds_giong"):
            self._luu_cai_dat()
        self._dung_phat_app()
        self._dung_clone.set()
        gc.dong_tat_ca_may()          # nhả VRAM ngay, không chờ máy đọc tự thoát
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
