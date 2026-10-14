"""Đọc Voice: app đọc văn bản bằng giọng Edge (Microsoft Neural), chạy trên máy.

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
PHIEN_BAN = "1.1.0"
KHO_GITHUB = "ducdg88/doc-voice"
TEN_EXE = "DocVoice.exe"
LINK_TAI_HOP_LE = f"https://github.com/{KHO_GITHUB}/releases/download/"
FILE_TU_DIEN = os.path.join(GOC, "tu_dien.txt")
BYTE_MOI_GIAY = 6000  # edge-tts trả mp3 48 kbps: 6000 byte = 1 giây

TEN_NGON_NGU = {
    "vi-VN": "Tiếng Việt", "en-US": "English (US)", "en-GB": "English (UK)",
    "en-AU": "English (Australia)", "en-CA": "English (Canada)", "en-IN": "English (India)",
    "fr-FR": "Français", "de-DE": "Deutsch", "es-ES": "Español", "ja-JP": "日本語",
    "ko-KR": "한국어", "zh-CN": "中文 (简体)", "zh-TW": "中文 (繁體)", "th-TH": "ไทย",
    "pt-BR": "Português (BR)", "it-IT": "Italiano", "ru-RU": "Русский",
}


# ---------- phát âm thanh không mở cửa sổ (Windows MCI) ----------
_mci = ctypes.windll.winmm.mciSendStringW


def mci(lenh):
    _mci(lenh, None, 0, None)


def phat(duong_dan):
    mci("close docvoice")
    mci(f'open "{duong_dan}" type mpegvideo alias docvoice')
    mci("play docvoice")


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


def tao_srt(ket_qua, tu_dien=({}, ())):
    """ket_qua: [(mp3, [(bắt_đầu_giây, dài_giây, câu)])] theo thứ tự đoạn.
    tu_dien: (bản đồ câu, từ điển dự phòng) để trả phụ đề về chữ gốc."""
    ban_do, du_phong = tu_dien
    dong, lech, so = [], 0.0, 0
    for mp3, cau in ket_qua:
        for bd, dai, chu in cau:
            so += 1
            chu = ban_do.get(chuan_cau(chu)) or tra_chu_goc(chu, du_phong)
            dong.append(f"{so}\n{gio_srt(lech + bd)} --> {gio_srt(lech + bd + dai)}\n{chu}\n")
        lech += len(mp3) / BYTE_MOI_GIAY
    return "\n".join(dong)


def lay_danh_sach_cau(ket_qua, tu_dien=({}, ())):
    """Trích xuất danh sách [(bat_dau, ket_thuc, chu_that)] theo timeline audio để karaoke highlight."""
    ban_do, du_phong = tu_dien
    cau_list, lech = [], 0.0
    for mp3, cau in ket_qua:
        for bd, dai, chu in cau:
            chu_that = ban_do.get(chuan_cau(chu)) or tra_chu_goc(chu, du_phong)
            cau_list.append((lech + bd, lech + bd + dai, chu_that.strip()))
        lech += len(mp3) / BYTE_MOI_GIAY
    return cau_list


def dich_tieng_viet(van_ban):
    """Dịch văn bản sang tiếng Việt bằng endpoint miễn phí không cần key."""
    if not van_ban.strip():
        return ""
    cac_doan = [d for d in chia_doan(van_ban, toi_da=1800) if d.strip()]
    ket_qua = []
    for doan in cac_doan:
        params = urllib.parse.urlencode({
            "client": "gtx",
            "sl": "auto",
            "tl": "vi",
            "dt": "t",
            "q": doan
        })
        url = f"https://translate.googleapis.com/translate_a/single?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
            dich_doan = "".join(item[0] for item in data[0] if item and item[0])
            ket_qua.append(dich_doan)
    return "\n\n".join(ket_qua)


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
        self.dang_chay = False
        self.file_cuoi = None
        self._danh_sach_cau = []
        self._dang_theo_doi_phat = False
        self._cau_highlight_cu = -1
        os.makedirs(THU_MUC_RA, exist_ok=True)
        self._dung_giao_dien()
        if self.giong:
            self._nap_ngon_ngu()
        else:
            self.trang_thai.set("Đang tải danh sách giọng...")
        threading.Thread(target=self._tai_giong, daemon=True).start()
        self.protocol("WM_DELETE_WINDOW", self._thoat)
        threading.Thread(target=don_exe_cu, daemon=True).start()
        if getattr(sys, "frozen", False):
            threading.Thread(target=self._kiem_cap_nhat, args=(False,), daemon=True).start()
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
        ttk.Label(khung, text="Giọng Microsoft Edge Neural, 2 bảng song song tiếng Anh và bản dịch, highlight theo tiến trình đọc.",
                  foreground="#6b7280").pack(anchor="w", pady=(0, 10))

        hang1 = ttk.Frame(khung)
        hang1.pack(fill="x", pady=(0, 8))
        ttk.Button(hang1, text="Mở file văn bản", command=self._mo_file).pack(side="left")
        ttk.Button(hang1, text="Từ điển", command=self._tu_dien).pack(side="left", padx=(6, 0))
        ttk.Button(hang1, text="Cập nhật", command=lambda: threading.Thread(
            target=self._kiem_cap_nhat, args=(True,), daemon=True).start()).pack(side="right")
        ttk.Label(hang1, text="  Ngôn ngữ").pack(side="left")
        self.ngon_ngu = ttk.Combobox(hang1, state="readonly", width=22)
        self.ngon_ngu.pack(side="left", padx=6)
        self.ngon_ngu.bind("<<ComboboxSelected>>", lambda e: self._nap_giong())
        ttk.Label(hang1, text="Giọng").pack(side="left")
        self.chon_giong = ttk.Combobox(hang1, state="readonly", width=28)
        self.chon_giong.pack(side="left", padx=6)

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

        self.trang_thai = tk.StringVar(value="Sẵn sàng")
        ttk.Label(khung, textvariable=self.trang_thai, foreground="#374151").pack(side="bottom", anchor="w", pady=(4, 0))
        self.tien_do = ttk.Progressbar(khung, style="green.Horizontal.TProgressbar", maximum=100)
        self.tien_do.pack(side="bottom", fill="x", pady=(8, 0))

        hang3 = ttk.Frame(khung)
        hang3.pack(side="bottom", fill="x", pady=(8, 0))
        self.nut_chuyen = ttk.Button(hang3, text="Chuyển đổi", style="Xanh.TButton", command=self._chuyen)
        self.nut_chuyen.pack(side="left")
        self.nut_thu = ttk.Button(hang3, text="Nghe thử", command=self._nghe_thu)
        self.nut_thu.pack(side="left", padx=6)
        ttk.Button(hang3, text="Dừng phát", command=self._dung_phat_app).pack(side="left")
        ttk.Button(hang3, text="Phát lại", command=self._phat_lai).pack(side="left", padx=6)
        ttk.Button(hang3, text="Đã chuyển đổi", command=lambda: os.startfile(THU_MUC_RA)).pack(side="left")
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
        self.o_dich.insert("1.0", self.cd.get("ban_dich", ""))
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
            self.after(0, lambda: self.trang_thai.set(f"Đã tải {len(ds)} giọng từ máy chủ."))
        except Exception as e:
            self.after(0, lambda e=e: self.trang_thai.set(f"Không tải được danh sách giọng: {e}"))

    def _nap_ngon_ngu(self):
        co_san = {v["Locale"] for v in self.giong}
        uu_tien = ["vi-VN", "en-US", "en-AU", "en-GB"]
        ds = [loc for loc in uu_tien if loc in co_san] + sorted(co_san - set(uu_tien))
        self._locales = ds
        self.ngon_ngu["values"] = [f"{TEN_NGON_NGU.get(loc, loc)} ({loc})" for loc in ds]
        chon = self.cd.get("ngon_ngu", "en-AU")
        idx = ds.index(chon) if chon in ds else (ds.index("vi-VN") if "vi-VN" in ds else 0)
        self.ngon_ngu.current(idx)
        self._nap_giong()

    def _nap_giong(self):
        loc = self._locales[self.ngon_ngu.current()]
        ds = [v for v in self.giong if v["Locale"] == loc]
        self._ds_giong = [v["ShortName"] for v in ds]
        self.chon_giong["values"] = [ten_hien_thi(v) for v in ds]
        luu = self.cd.get("giong")
        if luu in self._ds_giong:
            self.chon_giong.current(self._ds_giong.index(luu))
        else:
            multi = [i for i, v in enumerate(ds) if "Multilingual" in v["ShortName"]]
            self.chon_giong.current(multi[0] if multi else 0)

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

    def _mac_dinh(self):
        self.cao_do.set(0)
        self.toc_do.set(0)
        self._cap_nhat_nhan()

    def _dem_chu(self, _=None):
        t = self.o_chu.get("1.0", "end").strip()
        self.dem_trai.config(text=f"{len(t.split())} từ ({len(t)} ký tự)")
        self.o_chu.edit_modified(False)

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

    def _dich_van_ban(self):
        van_ban = self.o_chu.get("1.0", "end").strip()
        if not van_ban:
            messagebox.showinfo("Đọc Voice", "Chưa có văn bản bên bảng đọc để dịch.")
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
        self.o_chu.tag_remove("dang_doc", "1.0", "end")
        self.o_dich.tag_remove("dang_doc", "1.0", "end")

    def _highlight_cau(self, chu, idx):
        self._xoa_highlight()
        if not chu:
            return
        # 1. Bảng trái: tìm câu trong self.o_chu
        dau = chu[:min(35, len(chu))].strip()
        if dau:
            pos = self.o_chu.search(dau, "1.0", tk.END)
            if pos:
                end_pos = f"{pos}+{len(chu)}c"
                self.o_chu.tag_add("dang_doc", pos, end_pos)
                self.o_chu.see(pos)

        # 2. Bảng phải: tìm câu dịch tương ứng
        dich_toan_bo = self.o_dich.get("1.0", "end").strip()
        if dich_toan_bo:
            cac_cau_dich = [c.strip() for c in re.split(r"(?<=[.!?。！？…])\s+|\n+", dich_toan_bo) if c.strip()]
            if 0 <= idx < len(cac_cau_dich):
                cau_d = cac_cau_dich[idx]
                dau_d = cau_d[:min(35, len(cau_d))].strip()
                if dau_d:
                    pos_d = self.o_dich.search(dau_d, "1.0", tk.END)
                    if pos_d:
                        end_pos_d = f"{pos_d}+{len(cau_d)}c"
                        self.o_dich.tag_add("dang_doc", pos_d, end_pos_d)
                        self.o_dich.see(pos_d)

    def _theo_doi_phat(self):
        if not getattr(self, "_dang_theo_doi_phat", False):
            self._xoa_highlight()
            return
        da_chay = time.time() - getattr(self, "_thoi_gian_phat", 0)

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
            else:
                self._xoa_highlight()

        self.after(50, self._theo_doi_phat)

    def _bat_dau_phat(self, duong_dan, cau_list=None):
        if cau_list:
            self._danh_sach_cau = cau_list
        self._cau_highlight_cu = -1
        self._thoi_gian_phat = time.time()
        self._dang_theo_doi_phat = True
        phat(duong_dan)
        self._theo_doi_phat()

    def _dung_phat_app(self):
        self._dang_theo_doi_phat = False
        dung_phat()
        self._xoa_highlight()
        self.trang_thai.set("Đã dừng phát.")

    def _phat_lai(self):
        if self.file_cuoi and os.path.exists(self.file_cuoi):
            self.trang_thai.set(f"Đang phát lại: {os.path.basename(self.file_cuoi)}")
            self._bat_dau_phat(self.file_cuoi, getattr(self, "_danh_sach_cau", None))

    def _nghe_thu(self):
        t = self.o_chu.get("1.0", "end").strip()
        if not t or not self._san_sang():
            return
        doan = (chia_doan(t, 300) or [""])[0]
        self._chay(doan, os.path.join(THU_MUC_RA, "_nghe_thu.mp3"), nghe_thu=True)

    def _chuyen(self):
        t = self.o_chu.get("1.0", "end").strip()
        if not t:
            messagebox.showinfo("Đọc Voice", "Chưa có văn bản bên bảng đọc.")
            return
        if not self._san_sang():
            return
        giong = self._tham_so()[0].split("-")[2].replace("MultilingualNeural", "").replace("Neural", "")
        mac_dinh = f"{time.strftime('%Y%m%d-%H%M%S')}-{giong}.mp3"
        p = filedialog.asksaveasfilename(initialdir=THU_MUC_RA, initialfile=mac_dinh,
                                         defaultextension=".mp3", filetypes=[("MP3", "*.mp3")])
        if p:
            self._chay(t, p)

    def _highlight_doan_dang_chuyen(self, text_doan):
        self._xoa_highlight()
        dau = text_doan[:min(40, len(text_doan))].strip()
        if dau:
            pos = self.o_chu.search(dau, "1.0", tk.END)
            if pos:
                end_pos = f"{pos}+{len(text_doan)}c"
                self.o_chu.tag_add("dang_doc", pos, end_pos)
                self.o_chu.see(pos)

    def _chay(self, van_ban, duong_ra, nghe_thu=False):
        if self.dang_chay:
            return
        self.dang_chay = True
        self._dung_phat_app()
        self.nut_chuyen.state(["disabled"])
        self.nut_thu.state(["disabled"])
        giong, toc, cao = self._tham_so()
        self._luu_cai_dat()
        td = doc_tu_dien()
        self._tu_dien_luot = (ban_do_cau(van_ban, td), tu_dien_cho_srt(van_ban, td))
        van_ban_doc = ap_tu_dien(van_ban, td)
        threading.Thread(target=self._lam, args=(van_ban_doc, duong_ra, giong, toc, cao, nghe_thu),
                         daemon=True).start()

    def _lam(self, van_ban, duong_ra, giong, toc, cao, nghe_thu):
        doan = [d for d in chia_doan(van_ban) if re.search(r"\w", d)]
        xong = [0]
        if not doan:
            self.after(0, self._bao, "Không có chữ nào để đọc", 0)
            self.after(0, lambda: messagebox.showwarning("Đọc Voice", "Văn bản không có chữ nào để đọc."))
            self.after(0, self._xong)
            return

        async def tat_ca():
            khoa = asyncio.Semaphore(SONG_SONG)

            async def mot(d):
                async with khoa:
                    self.after(0, lambda d=d: self._highlight_doan_dang_chuyen(d))
                    b = await tao_doan(d, giong, toc, cao)
                xong[0] += 1
                self.after(0, self._bao, f"Đã chuyển {xong[0]}/{len(doan)} đoạn...", xong[0] / len(doan) * 100)
                return b
            return await asyncio.gather(*(mot(d) for d in doan))

        try:
            self.after(0, self._bao, f"Đang chuyển {len(doan)} đoạn...", 0)
            ket_qua = asyncio.run(tat_ca())
            tam = duong_ra + ".part"
            with open(tam, "wb") as f:
                for b, _ in ket_qua:
                    f.write(b)
            os.replace(tam, duong_ra)
            if not nghe_thu:
                with open(os.path.splitext(duong_ra)[0] + ".srt", "w", encoding="utf-8") as f:
                    f.write(tao_srt(ket_qua, self._tu_dien_luot))
            self.file_cuoi = duong_ra
            cau_list = lay_danh_sach_cau(ket_qua, self._tu_dien_luot)
            self._danh_sach_cau = cau_list
            self.after(0, self._bao, "Đang phát thử..." if nghe_thu else f"Xong: {duong_ra}", 100)
            self.after(0, lambda: self._bat_dau_phat(duong_ra, cau_list))
        except Exception as e:
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
        if self.dang_chay:
            return  # đang chuyển đổi thì không hỏi, lần mở sau hỏi lại
        if not messagebox.askyesno("Đọc Voice", f"Có bản mới v{ban} (đang dùng v{PHIEN_BAN}).\nCập nhật ngay?"):
            return
        self.trang_thai.set(f"Đang tải bản v{ban}...")
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

    def _xong(self):
        self.dang_chay = False
        self.nut_chuyen.state(["!disabled"])
        self.nut_thu.state(["!disabled"])

    def _luu_cai_dat(self):
        try:
            ghi_json(FILE_CAI_DAT, {
                "ngon_ngu": self._locales[self.ngon_ngu.current()],
                "giong": self._ds_giong[self.chon_giong.current()],
                "cao_do": self.cao_do.get(), "toc_do": self.toc_do.get(),
                "van_ban": self.o_chu.get("1.0", "end").strip()[:50000],
                "ban_dich": self.o_dich.get("1.0", "end").strip()[:50000],
            })
        except Exception:
            pass

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
