"""Đọc Giọng: app đọc văn bản bằng giọng Edge (Microsoft Neural), dùng riêng trên máy.

Giống bản J2TEAM TTS miễn phí trước đây: chọn ngôn ngữ, giọng, cao độ, tốc độ,
đọc thử, xuất mp3. Văn bản dài được chia đoạn theo câu rồi ghép lại, kèm phụ đề .srt.
Bản exe tự kiểm bản mới trên GitHub Releases và tự thay.
"""
import asyncio
import ctypes
import html
import hashlib
import json
import os
import re
import subprocess
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
    GOC = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "DocGiong")
    THU_MUC_RA = os.path.join(thu_muc_documents(), "Doc Giong")
else:
    GOC = os.path.dirname(os.path.abspath(__file__))
    THU_MUC_RA = os.path.join(GOC, "da_chuyen")
os.makedirs(GOC, exist_ok=True)
FILE_GIONG = os.path.join(GOC, "giong.json")
FILE_CAI_DAT = os.path.join(GOC, "cai_dat.json")
DO_DAI_DOAN = 2500  # ký tự mỗi lượt gửi máy chủ
SO_LAN_THU = 4
SONG_SONG = 3  # số đoạn gửi máy chủ cùng lúc
PHIEN_BAN = "1.0.0"
KHO_GITHUB = "ducdg88/doc-giong"
TEN_EXE = "DocGiong.exe"
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
    mci("close docgiong")
    mci(f'open "{duong_dan}" type mpegvideo alias docgiong')
    mci("play docgiong")


def dung_phat():
    mci("stop docgiong")
    mci("close docgiong")


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


def gio_srt(giay):
    ms = int(round(giay * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def tao_srt(ket_qua, tu_dien=()):
    """ket_qua: [(mp3, [(bắt_đầu_giây, dài_giây, câu)])] theo thứ tự đoạn."""
    dong, lech, so = [], 0.0, 0
    for mp3, cau in ket_qua:
        for bd, dai, chu in cau:
            so += 1
            chu = tra_chu_goc(chu, tu_dien)
            dong.append(f"{so}\n{gio_srt(lech + bd)} --> {gio_srt(lech + bd + dai)}\n{chu}\n")
        lech += len(mp3) / BYTE_MOI_GIAY
    return "\n".join(dong)


def so_phien_ban(v):
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


def che_do_thu():
    """Máy chủ giả chỉ dùng khi chạy bằng Python, hoặc exe có file DOCGIONG_THU đặt cạnh (máy dev)."""
    if not getattr(sys, "frozen", False):
        return True
    return os.path.exists(os.path.join(os.path.dirname(sys.executable), "DOCGIONG_THU"))


def kiem_ban_moi():
    """Trả (phiên bản, asset exe) nếu GitHub có bản mới hơn, không thì None."""
    api = (che_do_thu() and os.environ.get("DOCGIONG_API")) or \
        f"https://api.github.com/repos/{KHO_GITHUB}/releases/latest"
    req = urllib.request.Request(api,
                                 headers={"User-Agent": "DocGiong", "Accept": "application/vnd.github+json"})
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
    cu = os.path.join(thu_muc, "DocGiong.old.exe")
    can_size = asset.get("size")
    can_sha = (asset.get("digest") or "").removeprefix("sha256:").lower()
    try:
        h, n = hashlib.sha256(), 0
        req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "DocGiong"})
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
        raise
    env = {k: v for k, v in os.environ.items() if not k.startswith("_PYI") and k != "_MEIPASS2"}
    if not mo_lai:
        return
    subprocess.Popen([exe], cwd=thu_muc, env=env, close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def don_exe_cu():
    if getattr(sys, "frozen", False):
        cu = os.path.join(os.path.dirname(sys.executable), "DocGiong.old.exe")
        for _ in range(20):
            try:
                if os.path.exists(cu):
                    os.remove(cu)
                return
            except OSError:
                time.sleep(0.5)


async def tao_doan(text, giong, toc_do, cao_do):
    """Trả (mp3, [(bắt_đầu_giây, dài_giây, câu)])."""
    loi = None
    for lan in range(SO_LAN_THU):
        try:
            buf, cau = bytearray(), []
            com = edge_tts.Communicate(text, giong, rate=toc_do, pitch=cao_do)
            async for goi in com.stream():
                if goi["type"] == "audio":
                    buf.extend(goi["data"])
                elif goi["type"] in ("SentenceBoundary", "WordBoundary"):
                    cau.append((goi["offset"] / 1e7, goi["duration"] / 1e7, goi["text"]))
            if buf:
                return bytes(buf), cau
            loi = RuntimeError("Máy chủ không trả âm thanh")
        except Exception as e:  # mạng chập chờn, 403 tạm thời...
            loi = e
        if lan < SO_LAN_THU - 1:
            await asyncio.sleep(1.5 * (lan + 1))
    raise loi


# ---------- giao diện ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"Đọc Giọng · Text to Speech · v{PHIEN_BAN}")
        self.geometry("860x680")
        self.minsize(700, 560)
        self.configure(bg="#f6f8fa")
        self.giong = doc_json(FILE_GIONG, [])
        self.cd = doc_json(FILE_CAI_DAT, {})
        self.dang_chay = False
        self.file_cuoi = None
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
        s.configure("Tieu.TLabel", font=("Segoe UI Semibold", 17))
        s.configure("Xanh.TButton", foreground="white", background="#16a34a", padding=(14, 7))
        s.map("Xanh.TButton", background=[("active", "#15803d"), ("disabled", "#9ca3af")])
        s.configure("TButton", padding=(10, 6))
        s.configure("green.Horizontal.TProgressbar", background="#16a34a")

        khung = ttk.Frame(self, padding=18)
        khung.pack(fill="both", expand=True)

        ttk.Label(khung, text="Chuyển văn bản thành giọng nói", style="Tieu.TLabel").pack(anchor="w")
        ttk.Label(khung, text="Giọng Microsoft Edge Neural, chạy trên máy, dùng riêng.",
                  foreground="#6b7280").pack(anchor="w", pady=(0, 12))

        hang1 = ttk.Frame(khung)
        hang1.pack(fill="x", pady=(0, 10))
        ttk.Button(hang1, text="Mở file văn bản", command=self._mo_file).pack(side="left")
        ttk.Button(hang1, text="Từ điển", command=self._tu_dien).pack(side="left", padx=(6, 0))
        ttk.Button(hang1, text="Cập nhật", command=lambda: threading.Thread(
            target=self._kiem_cap_nhat, args=(True,), daemon=True).start()).pack(side="right")
        ttk.Label(hang1, text="  Ngôn ngữ").pack(side="left")
        self.ngon_ngu = ttk.Combobox(hang1, state="readonly", width=24)
        self.ngon_ngu.pack(side="left", padx=6)
        self.ngon_ngu.bind("<<ComboboxSelected>>", lambda e: self._nap_giong())
        ttk.Label(hang1, text="Giọng").pack(side="left")
        self.chon_giong = ttk.Combobox(hang1, state="readonly", width=30)
        self.chon_giong.pack(side="left", padx=6)

        hang2 = ttk.Frame(khung)
        hang2.pack(fill="x", pady=(0, 10))
        self.cao_do = tk.IntVar(value=self.cd.get("cao_do", 0))
        self.toc_do = tk.IntVar(value=self.cd.get("toc_do", 0))
        self.nhan_cao = ttk.Label(hang2, width=34)
        self.nhan_cao.grid(row=0, column=0, sticky="w")
        self.nhan_toc = ttk.Label(hang2, width=34)
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
        self.tien_do.pack(side="bottom", fill="x", pady=(10, 0))
        hang3 = ttk.Frame(khung)
        hang3.pack(side="bottom", fill="x", pady=(10, 0))
        self.nut_chuyen = ttk.Button(hang3, text="Chuyển đổi", style="Xanh.TButton", command=self._chuyen)
        self.nut_chuyen.pack(side="left")
        self.nut_thu = ttk.Button(hang3, text="Nghe thử", command=self._nghe_thu)
        self.nut_thu.pack(side="left", padx=6)
        ttk.Button(hang3, text="Dừng phát", command=dung_phat).pack(side="left")
        ttk.Button(hang3, text="Phát lại", command=self._phat_lai).pack(side="left", padx=6)
        ttk.Button(hang3, text="Đã chuyển đổi", command=lambda: os.startfile(THU_MUC_RA)).pack(side="left")
        ttk.Button(hang3, text="Xoá", command=lambda: self.o_chu.delete("1.0", "end")).pack(side="left", padx=6)
        self.dem = ttk.Label(hang3, foreground="#dc2626")
        self.dem.pack(side="right")

        khung_chu = ttk.Frame(khung)
        khung_chu.pack(fill="both", expand=True)
        self.o_chu = tk.Text(khung_chu, wrap="word", font=("Segoe UI", 11), relief="solid",
                             borderwidth=1, padx=10, pady=8, undo=True)
        cuon = ttk.Scrollbar(khung_chu, command=self.o_chu.yview)
        self.o_chu.configure(yscrollcommand=cuon.set)
        self.o_chu.pack(side="left", fill="both", expand=True)
        cuon.pack(side="right", fill="y")
        self.o_chu.insert("1.0", self.cd.get("van_ban", ""))
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
            self.after(0, lambda: self.trang_thai.set(f"Sẵn sàng · {len(ds)} giọng"))
        except Exception as e:
            if not self.giong:
                self.after(0, lambda e=e: self.trang_thai.set(f"Không tải được danh sách giọng: {e}"))

    def _nap_ngon_ngu(self):
        locales = sorted({v["Locale"] for v in self.giong})
        uu_tien = [l for l in ("vi-VN", "en-US", "en-AU", "en-GB") if l in locales]
        locales = uu_tien + [l for l in locales if l not in uu_tien]
        self._locales = locales
        self.ngon_ngu["values"] = [f"{TEN_NGON_NGU.get(l, l)} ({l})" if l in TEN_NGON_NGU else l for l in locales]
        l = self.cd.get("ngon_ngu", "en-AU")
        self.ngon_ngu.current(locales.index(l) if l in locales else 0)
        self._nap_giong(self.cd.get("giong", "en-AU-WilliamMultilingualNeural"))

    def _nap_giong(self, chon=None):
        loc = self._locales[self.ngon_ngu.current()]
        ds = sorted((v for v in self.giong if v["Locale"] == loc),
                    key=lambda v: ("Multilingual" not in v["ShortName"], v["ShortName"]))
        self._ds_giong = [v["ShortName"] for v in ds]
        self.chon_giong["values"] = [ten_hien_thi(v) for v in ds]
        if ds:
            self.chon_giong.current(self._ds_giong.index(chon) if chon in self._ds_giong else 0)

    # --- điều khiển ---
    def _san_sang(self):
        if getattr(self, "_ds_giong", None):
            return True
        messagebox.showinfo("Đọc Giọng", "Đang tải danh sách giọng, chờ vài giây rồi bấm lại.")
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
        self.dem.config(text=f"{len(t.split())} từ ({len(t)} ký tự)")
        self.o_chu.edit_modified(False)

    def _mo_file(self):
        p = filedialog.askopenfilename(filetypes=[("Văn bản, Word", "*.txt *.docx *.md *.srt"), ("Tất cả", "*.*")])
        if not p:
            return
        if p.lower().endswith(".docx"):
            try:
                noi_dung = doc_docx(p)
            except Exception as e:
                messagebox.showerror("Đọc Giọng", f"Không đọc được file Word:\n{e}")
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
            messagebox.showerror("Đọc Giọng", "Không đọc được file này.")
            return
        self.o_chu.delete("1.0", "end")
        self.o_chu.insert("1.0", noi_dung)
        self._dem_chu()

    def _phat_lai(self):
        if self.file_cuoi and os.path.exists(self.file_cuoi):
            phat(self.file_cuoi)

    def _nghe_thu(self):
        t = self.o_chu.get("1.0", "end").strip()
        if not t or not self._san_sang():
            return
        doan = (chia_doan(t, 300) or [""])[0]
        self._chay(doan, os.path.join(THU_MUC_RA, "_nghe_thu.mp3"), nghe_thu=True)

    def _chuyen(self):
        t = self.o_chu.get("1.0", "end").strip()
        if not t:
            messagebox.showinfo("Đọc Giọng", "Chưa có văn bản.")
            return
        if not self._san_sang():
            return
        giong = self._tham_so()[0].split("-")[2].replace("MultilingualNeural", "").replace("Neural", "")
        mac_dinh = f"{time.strftime('%Y%m%d-%H%M%S')}-{giong}.mp3"
        p = filedialog.asksaveasfilename(initialdir=THU_MUC_RA, initialfile=mac_dinh,
                                         defaultextension=".mp3", filetypes=[("MP3", "*.mp3")])
        if p:
            self._chay(t, p)

    def _chay(self, van_ban, duong_ra, nghe_thu=False):
        if self.dang_chay:
            return
        self.dang_chay = True
        dung_phat()
        self.nut_chuyen.state(["disabled"])
        self.nut_thu.state(["disabled"])
        giong, toc, cao = self._tham_so()
        self._luu_cai_dat()
        self._tu_dien_luot = doc_tu_dien()
        van_ban = ap_tu_dien(van_ban, self._tu_dien_luot)
        threading.Thread(target=self._lam, args=(van_ban, duong_ra, giong, toc, cao, nghe_thu),
                         daemon=True).start()

    def _lam(self, van_ban, duong_ra, giong, toc, cao, nghe_thu):
        doan = [d for d in chia_doan(van_ban) if re.search(r"\w", d)]
        xong = [0]
        if not doan:
            self.after(0, self._bao, "Không có chữ nào để đọc", 0)
            self.after(0, lambda: messagebox.showwarning("Đọc Giọng", "Văn bản không có chữ nào để đọc."))
            self.after(0, self._xong)
            return

        async def tat_ca():
            khoa = asyncio.Semaphore(SONG_SONG)

            async def mot(d):
                async with khoa:
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
            self.after(0, self._bao, "Đang phát thử" if nghe_thu else f"Xong: {duong_ra}", 100)
            self.after(0, phat, duong_ra)
        except Exception as e:
            self.after(0, self._bao, f"Lỗi: {e}", 0)
            self.after(0, lambda e=e: messagebox.showerror("Đọc Giọng", f"Không chuyển được:\n{e}"))
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
                self.after(0, lambda e=e: messagebox.showwarning("Đọc Giọng", f"Không kiểm được bản mới:\n{e}"))
            return
        if not moi:
            if chu_dong:
                self.after(0, lambda: messagebox.showinfo("Đọc Giọng", f"Đang dùng bản mới nhất (v{PHIEN_BAN})."))
            return
        self.after(0, self._hoi_cap_nhat, *moi)

    def _hoi_cap_nhat(self, ban, asset):
        if not getattr(sys, "frozen", False):
            messagebox.showinfo("Đọc Giọng", f"Có bản v{ban}. Bản chạy bằng Python thì cập nhật bằng git pull.")
            return
        if self.dang_chay:
            return  # đang chuyển đổi thì không hỏi, lần mở sau hỏi lại
        if not messagebox.askyesno("Đọc Giọng", f"Có bản mới v{ban} (đang dùng v{PHIEN_BAN}).\nCập nhật ngay?"):
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
                    "Đọc Giọng", f"Không tự cập nhật được, app cũ vẫn dùng bình thường:\n{e}"
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
            })
        except Exception:
            pass

    def _thoat(self):
        if hasattr(self, "_ds_giong"):
            self._luu_cai_dat()
        dung_phat()
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
                mo_lai = bool(os.environ.get("DOCGIONG_KIEM_MO"))
                thay_exe(moi[1], mo_lai=mo_lai)
                f.write(f"da thay exe, mo lai={mo_lai}\n")
        except Exception as e:
            f.write(f"cap nhat LOI {e}\n")


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--tu-thu":
        don_exe_cu()
        tu_thu(sys.argv[2])
    elif os.environ.get("DOCGIONG_KIEM_MO") and che_do_thu():
        # kiểm đường mở lại sau cập nhật: dựng app ẩn, ghi nhật ký rồi thoát
        app = App()
        app.withdraw()
        with open(os.environ["DOCGIONG_KIEM_MO"], "a", encoding="utf-8") as f:
            f.write(f"mo lai ok: v{PHIEN_BAN} giao dien dung xong, exe={sys.executable}\n")
        app.destroy()
    else:
        App().mainloop()
