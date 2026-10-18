"""Máy đọc NHẸ cho giọng clone: chạy mọi máy, không cần GPU, không cần torch.

Cách làm: Edge TTS đọc câu (giọng nam Mỹ gần Arthur nhất: Christopher) -> bộ đổi chất giọng
OpenVoice V2 (tone color converter, MIT) chạy bằng onnxruntime trên CPU đổi sang chất giọng của
mẫu (ref.wav trong gói giọng). Đồ thị ONNX đi kèm app (vài trăm KB, không chứa trọng số); trọng số
tải một lần từ Hugging Face (myshell-ai/OpenVoiceV2, checkpoint.pth 131 MB) và đọc bằng bộ đọc
không cần torch. Tải tiếp được khi đứt (HTTP Range).
"""
import asyncio
import hashlib
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

import numpy as np

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0

HF = "https://huggingface.co/myshell-ai/OpenVoiceV2/resolve/main/converter/"
TAP_MODEL = [
    {"ten": "checkpoint.pth", "url": HF + "checkpoint.pth", "co": 131320490,
     "sha256": "9652c27e92b6b2a91632590ac9962ef7ae2b712e5c5b7f4c34ec55ee2b37ab9e"},
    {"ten": "config.json", "url": HF + "config.json", "co": 838,
     "sha256": "9dfff60350b8c63f2c664efd92a61b2516efb22671466960f0e5dfebd881fa47"},
]
# ffmpeg chỉ tải khi máy chưa có. Ghim đúng một bản dựng công khai (BtbN/FFmpeg-Builds, FFmpeg n9.0.2, bản LGPL
# liên kết tĩnh, không phải GPL) theo thẻ phát hành cố định autobuild-2026-09-30-13-08, kiểm kích thước và sha256 như model.
# Không dùng thẻ "latest" vì nội dung đổi hằng ngày nên không kiểm mã được.
FFMPEG_ZIP = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-30-13-08/"
              "ffmpeg-n9.0.2-17-g2a571b6068-win64-lgpl-9.0.zip")
FFMPEG_ZIP_CO = 171535354
FFMPEG_ZIP_SHA256 = "6b264b9e6019103f601d98c292bd332fd87acf1c5e941ddff4fb71760fe63432"

GIONG_EDGE_NGUON = "en-US-ChristopherNeural"
SR = 22050
# Mức cảm xúc của máy nhẹ: Edge không nhận lệnh trong ngoặc, nên đổi tốc độ và cao độ
NHAC_DIEU = {
    "Trung tính": {"rate": "-8%", "pitch": "-2Hz"},
    "Kể chuyện": {"rate": "-10%", "pitch": "-3Hz"},
    "Hào hứng": {"rate": "+0%", "pitch": "+3Hz"},
    # sắc thái từng câu ở mức Tự động theo kịch bản (giong_clone.SAC_THAI)
    "hồi hộp": {"rate": "-12%", "pitch": "-2Hz"},
    "căng thẳng": {"rate": "-16%", "pitch": "-4Hz"},
    "hào hứng": {"rate": "-2%", "pitch": "+3Hz"},
    "kể chuyện": {"rate": "-10%", "pitch": "-3Hz"},
    "thân thiện": {"rate": "-12%", "pitch": "+1Hz"},
    "trầm": {"rate": "-14%", "pitch": "-5Hz"},
    "nhấn": {"rate": "-18%", "pitch": "-1Hz"},
}
TAU = 0.3
# Đọc lại một câu lỗi bằng đúng tham số cũ cho ra đúng âm thanh cũ (Edge và OpenVoice đều xác định), nên mỗi lần đọc lại
# đổi nhẹ nhịp và cao độ: (độ lệch nhịp %, độ lệch cao độ Hz). Lần 0 là gốc.
BIEN_THE = [(0, 0), (5, 0), (-5, 0), (9, -1), (-8, 1), (3, 2), (-3, -2), (12, 0), (-12, 0)]


def _cong_nhac(chuoi, delta, don_vi):
    """Cộng delta vào chuỗi như +5% hay -3Hz, trả cùng định dạng."""
    so = int(re.sub(r"[^\d+-]", "", chuoi) or 0) + int(delta)
    return f"{so:+d}{don_vi}"


def nhac_dieu_bien_the(nd, bien_the=0):
    """Nhịp, cao độ Edge cho lần đọc thứ bien_the của một câu (0 = gốc)."""
    dr, dp = BIEN_THE[int(bien_the) % len(BIEN_THE)]
    if not (dr or dp):
        return dict(nd)
    return {"rate": _cong_nhac(nd["rate"], dr, "%"), "pitch": _cong_nhac(nd["pitch"], dp, "Hz")}


def thu_muc_may_nhe():
    goc = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(goc, "DocVoice", "may_nhe")


def thu_muc_khung():
    if getattr(sys, "frozen", False):
        return os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), "may_nhe_onnx")
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "may_nhe_onnx")


# ------------------------------------------------------------- đọc checkpoint.pth không cần torch
class _Kho:
    def __init__(self, kieu):
        self.kieu = kieu


_KIEU = {"FloatStorage": np.float32, "HalfStorage": np.float16, "DoubleStorage": np.float64,
         "LongStorage": np.int64, "IntStorage": np.int32, "BFloat16Storage": "bf16"}


def doc_pth(duong):
    """Đọc file .pth (định dạng zip của torch) thành dict tên -> numpy, chỉ dùng thư viện chuẩn."""
    with zipfile.ZipFile(duong) as z:
        ten_pkl = [n for n in z.namelist() if n.endswith("/data.pkl")][0]
        goc = ten_pkl[: -len("data.pkl")]
        bo_nho = {}

        def lay_kho(khoa, kieu):
            if khoa not in bo_nho:
                raw = z.read(f"{goc}data/{khoa}")
                if kieu == "bf16":
                    u = np.frombuffer(raw, dtype="<u2").astype(np.uint32) << 16
                    bo_nho[khoa] = u.view(np.float32)
                else:
                    bo_nho[khoa] = np.frombuffer(raw, dtype=np.dtype(kieu).newbyteorder("<"))
            return bo_nho[khoa]

        def dung_tensor(kho, offset, size, stride, *a):
            mang = kho
            if not size:
                return mang[offset:offset + 1].reshape(()).copy()
            item = mang.itemsize
            return np.lib.stride_tricks.as_strided(mang[offset:], shape=tuple(size),
                                                   strides=tuple(s * item for s in stride)).copy()

        class Doc(pickle.Unpickler):
            def find_class(self, mod, ten):
                if mod == "torch._utils" and ten == "_rebuild_tensor_v2":
                    return dung_tensor
                if mod == "torch._utils" and ten == "_rebuild_parameter":
                    return lambda data, *a: data
                if mod == "torch" and ten in _KIEU:
                    return _Kho(_KIEU[ten])
                if mod == "collections" and ten == "OrderedDict":
                    import collections
                    return collections.OrderedDict
                raise pickle.UnpicklingError(f"không cho phép {mod}.{ten}")

            def persistent_load(self, pid):
                _, kho, khoa, _, _ = pid
                return lay_kho(khoa, kho.kieu)

        return Doc(z.open(ten_pkl)).load()


def trong_so(sd, ten, ct):
    """Tính trọng số theo công thức ghi lúc xuất ONNX (gộp weight_norm, đổi thứ tự cổng GRU)."""
    if ct["kieu"] == "ten":
        k = ct["khoa"]
        if k in sd:
            return np.ascontiguousarray(sd[k], dtype=np.float32)
        g, v = sd[k + "_g"].astype(np.float32), sd[k + "_v"].astype(np.float32)
        chuan = np.sqrt((v.reshape(v.shape[0], -1) ** 2).sum(axis=1)).reshape((-1,) + (1,) * (v.ndim - 1))
        return np.ascontiguousarray(g * v / chuan, dtype=np.float32)
    if ct["kieu"] == "gru":
        t = sd[ct["khoa"]].astype(np.float32)
        h = t.shape[0] // 3
        return np.ascontiguousarray(np.concatenate([t[h:2 * h], t[:h], t[2 * h:]])[None])
    if ct["kieu"] == "gru_bias":
        bi, bh = sd["ref_enc.gru.bias_ih_l0"], sd["ref_enc.gru.bias_hh_l0"]
        h = bi.shape[0] // 3
        r = lambda b: np.concatenate([b[h:2 * h], b[:h], b[2 * h:]])
        return np.ascontiguousarray(np.concatenate([r(bi), r(bh)])[None], dtype=np.float32)
    raise ValueError(ten)


# ------------------------------------------------------------- phổ (giống spectrogram_torch, center=False)
_CUA = None


def pho(y, n_fft=1024, hop=256):
    global _CUA
    if _CUA is None:
        _CUA = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(n_fft) / n_fft)).astype(np.float32)
    p = (n_fft - hop) // 2
    y = np.pad(np.asarray(y, dtype=np.float32), (p, p), mode="reflect")
    n = 1 + (len(y) - n_fft) // hop
    if n < 1:
        y = np.pad(y, (0, n_fft - len(y)))
        n = 1
    khung = np.lib.stride_tricks.as_strided(y, shape=(n, n_fft), strides=(y.strides[0] * hop, y.strides[0]))
    f = np.fft.rfft(khung * _CUA, axis=1)
    return np.sqrt(f.real ** 2 + f.imag ** 2 + 1e-6).astype(np.float32).T[None]   # [1, 513, T]


# ------------------------------------------------------------- tải tiếp được khi đứt
def tai_file(url, dich, co=None, sha256=None, bao=None, dung=None):
    """Tải về dich (qua dich.part), đứt thì lần sau tải tiếp từ byte đã có (HTTP Range)."""
    if os.path.isfile(dich) and (co is None or os.path.getsize(dich) == co):
        return dich
    os.makedirs(os.path.dirname(dich), exist_ok=True)
    part = dich + ".part"
    for lan in range(6):
        da = os.path.getsize(part) if os.path.isfile(part) else 0
        req = urllib.request.Request(url, headers={"User-Agent": "DocVoice", **({"Range": f"bytes={da}-"} if da else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if da and r.status != 206:      # máy chủ không hỗ trợ Range: tải lại từ đầu
                    da = 0
                tong = co or (da + int(r.headers.get("Content-Length") or 0))
                with open(part, "ab" if da else "wb") as f:
                    while True:
                        if dung is not None and dung.is_set():
                            return None
                        k = r.read(1 << 20)
                        if not k:
                            break
                        f.write(k)
                        da += len(k)
                        if bao:
                            bao(da, tong)
            if co is None or os.path.getsize(part) == co:
                break
        except Exception:
            if lan == 5:
                raise
            time.sleep(2 + lan * 3)
    if sha256:
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for k in iter(lambda: f.read(1 << 20), b""):
                h.update(k)
        if h.hexdigest() != sha256:
            os.remove(part)
            raise RuntimeError(f"File tải về sai mã kiểm (sha256): {os.path.basename(dich)}")
    os.replace(part, dich)
    return dich


def tim_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    rieng = os.path.join(thu_muc_may_nhe(), "ffmpeg", "ffmpeg.exe")
    return rieng if os.path.isfile(rieng) else ""


def da_cai():
    d = thu_muc_may_nhe()
    return all(os.path.isfile(os.path.join(d, t["ten"])) for t in TAP_MODEL) and bool(tim_ffmpeg())


def cai_dat(bao=None, dung=None):
    """Tải model nhẹ (và ffmpeg nếu máy chưa có). Gọi lại được nhiều lần, phần đã có thì bỏ qua."""
    d = thu_muc_may_nhe()
    for t in TAP_MODEL:
        tai_file(t["url"], os.path.join(d, t["ten"]), t["co"], t["sha256"],
                 bao=(lambda a, b, t=t: bao(f"Tải {t['ten']}", a, b)) if bao else None, dung=dung)
    if not tim_ffmpeg():
        z = tai_file(FFMPEG_ZIP, os.path.join(d, "ffmpeg.zip"), FFMPEG_ZIP_CO, FFMPEG_ZIP_SHA256,
                     bao=(lambda a, b: bao("Tải ffmpeg", a, b)) if bao else None, dung=dung)
        if z is None:
            return False
        with zipfile.ZipFile(z) as zz:
            ten = [n for n in zz.namelist() if n.endswith("bin/ffmpeg.exe")][0]
            os.makedirs(os.path.join(d, "ffmpeg"), exist_ok=True)
            with zz.open(ten) as nguon, open(os.path.join(d, "ffmpeg", "ffmpeg.exe"), "wb") as dich:
                shutil.copyfileobj(nguon, dich)
        os.remove(z)
    return da_cai()


def dung_luong_cai():
    d = thu_muc_may_nhe()
    tong = 0
    for goc, _, files in os.walk(d):
        for f in files:
            tong += os.path.getsize(os.path.join(goc, f))
    return tong


# ------------------------------------------------------------- âm thanh
def giai_ma(ffmpeg, du_lieu_hoac_duong, sr=SR):
    """mp3/wav -> mảng float32 mono ở sr (qua ffmpeg, ẩn cửa sổ)."""
    if isinstance(du_lieu_hoac_duong, (bytes, bytearray)):
        r = subprocess.run([ffmpeg, "-v", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                           input=bytes(du_lieu_hoac_duong), capture_output=True, creationflags=NO_WINDOW)
    else:
        r = subprocess.run([ffmpeg, "-v", "error", "-i", du_lieu_hoac_duong, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                           capture_output=True, creationflags=NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg không giải mã được âm thanh")
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


async def _edge(chu, giong, rate, pitch):
    import edge_tts
    loi = None
    for lan in range(4):
        try:
            c = edge_tts.Communicate(chu, giong, rate=rate, pitch=pitch)
            mp3 = bytearray()
            async for k in c.stream():
                if k["type"] == "audio":
                    mp3.extend(k["data"])
            if mp3:
                return bytes(mp3)
        except Exception as e:
            loi = e
        await asyncio.sleep(1 + lan)
    raise RuntimeError(f"Edge TTS không trả âm thanh: {loi}")


TRAN_LUONG_ONNX = 32


def so_luong_onnx(so_nhan=None, moi_truong=None):
    """Số luồng onnxruntime: bằng số nhân logic, tối đa 4.
    Đo 18/10/2026 (RTF = giây sinh / giây tiếng, thấp là nhanh): 2 nhân x 2 luồng 0,82 (1 luồng: 1,97);
    4 nhân x 4 luồng 0,66 (2 luồng: 1,08); máy 8 nhân trở lên giữ 4 luồng. 1 nhân thì 1 luồng.
    Biến môi trường DOCVOICE_THREADS (số nguyên từ 1) đè lên mọi thứ, trần TRAN_LUONG_ONNX (32): gõ 100000
    thì onnxruntime tạo ngần ấy luồng làm cạn RAM và treo máy (P3 chấm vòng 4)."""
    env = {"DOCVOICE_THREADS": os.environ.get("DOCVOICE_THREADS", "")} if moi_truong is None else moi_truong
    try:
        v = int(str(env.get("DOCVOICE_THREADS", "")).strip())
        if v >= 1:
            return min(v, TRAN_LUONG_ONNX)
    except ValueError:
        pass
    n = so_nhan if so_nhan is not None else (os.cpu_count() or 1)
    return max(1, min(4, int(n)))


class MayDocNhe:
    """Giao diện giống MayDocVoxCPM (bat, sinh, trang_thai) để DocDaiClone dùng chung."""

    ten = "Nhẹ (mọi máy)"
    khung = 260                # Edge đọc cả câu dài, không phải cắt nhỏ như VoxCPM
    khung_dau = 80             # phần đầu ngắn: bấm Nghe là có tiếng sau khoảng 2 đến 4 giây trên CPU
    # ngưỡng giống giọng từng câu theo cosine dấu giọng OpenVoice: đo 17/10 trên 67 câu Arthur min 0,735,
    # trung bình 0,861; giọng Edge thô (chưa đổi) 0,729. Dưới 0,74 coi như lệch về giọng nguồn, sinh lại nhẹ hơn.
    nguong_giong = 0.74
    nhan_tien_to = False       # không đọc được lệnh cảm xúc trong ngoặc: dùng NHAC_DIEU
    ho_tro_bien_the = True     # sinh(bien_the=n) đổi nhẹ nhịp, cao độ để đọc lại ra bản khác
    nguong_wer = 0.10          # WER từng câu tối đa: Edge đọc đúng gần như hoàn toàn, lệch hơn thì thử biến thể khác

    def __init__(self, so_luong=None):
        self.ss_ref = self.ss_doi = None
        self.se_nguon = None
        self._se_dich = {}
        self._khoa = threading.Lock()
        self._khoa_bat = threading.Lock()   # nạp trước lúc mở app và bấm Nghe cùng lúc: chỉ nạp một lần
        self.so_luong = so_luong or so_luong_onnx()
        self._truoc = {}             # (chu, muc) -> Future mp3 Edge tải trước
        self._pool = None

    def chuan_bi(self, ds):
        """Tải trước mp3 Edge cho các câu sắp đọc (3 luồng mạng) trong lúc CPU đổi chất giọng câu hiện tại."""
        from concurrent.futures import ThreadPoolExecutor
        if self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=3)
        for chu, muc in ds:
            chu = re.sub(r"^\s*\([^)]*\)", "", chu).strip()
            k = (chu, muc)
            if k not in self._truoc:
                nd = NHAC_DIEU.get(muc, NHAC_DIEU["Kể chuyện"])
                self._truoc[k] = self._pool.submit(lambda c=chu, n=nd: asyncio.run(_edge(c, GIONG_EDGE_NGUON, n["rate"], n["pitch"])))

    def co_san(self):
        return da_cai()

    def trang_thai(self):
        return {"loaded": self.ss_doi is not None, "app": "may-nhe"}

    def bat(self, bao=None, **kw):
        with self._khoa_bat:
            return self._bat(bao)

    def _bat(self, bao=None):
        if self.ss_doi is not None:
            return self.trang_thai()
        if not da_cai():
            raise RuntimeError("Chưa cài máy đọc nhẹ. Bấm Cài máy đọc giọng clone (tải khoảng 131 MB, một lần).")
        import onnxruntime as ort
        d = thu_muc_may_nhe()
        if bao:
            bao("Đang nạp máy đọc nhẹ...")
        sd = doc_pth(os.path.join(d, "checkpoint.pth"))["model"]
        with open(os.path.join(thu_muc_khung(), "ov_cong_thuc.json"), encoding="utf-8") as f:
            ct = json.load(f)

        def mo(ten):
            so = ort.SessionOptions()
            so.intra_op_num_threads = self.so_luong
            so.inter_op_num_threads = 1
            s = ort.InferenceSession(os.path.join(thu_muc_khung(), ten), so, providers=["CPUExecutionProvider"])
            s.trong_so = {k: trong_so(sd, k, c) for k, c in ct[ten].items()}   # đầu vào trọng số, nạp một lần
            return s
        self.ss_ref = mo("ov_ref.onnx")
        self.ss_doi = mo("ov_doi.onnx")
        self.ffmpeg = tim_ffmpeg()
        self.se_nguon = self._se_giong_nguon()
        return self.trang_thai()

    def se(self, am):
        return self.ss_ref.run(None, {**self.ss_ref.trong_so, "spec_t": pho(am).transpose(0, 2, 1)})[0]

    def _se_giong_nguon(self):
        """Dấu giọng của Christopher: tính một lần từ đoạn đọc mẫu ~25 giây, lưu lại."""
        p = os.path.join(thu_muc_may_nhe(), f"se_{GIONG_EDGE_NGUON}.npy")
        if os.path.isfile(p):
            return np.load(p)
        mau = ("Every forest has a story. Some trees grow for centuries, quietly recording each season in "
               "their rings. Others fall early, giving light and space to the young ones below. When we "
               "walk among them, we are walking through a living library, written slowly, one year at a time.")
        mp3 = asyncio.run(_edge(mau, GIONG_EDGE_NGUON, "+0%", "+0Hz"))
        g = self.se(giai_ma(self.ffmpeg, mp3))
        np.save(p, g)
        return g

    def do_giong(self, wav, ref):
        """Giống giọng một câu: cosine dấu giọng OpenVoice của câu đọc ra và của mẫu (thang riêng, xem nguong_giong)."""
        import wave
        with wave.open(wav, "rb") as w:
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        with self._khoa:
            a = np.asarray(self.se(x)).reshape(-1)
            b = np.asarray(self.se_dich(ref)).reshape(-1)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))

    def se_dich(self, ref):
        """Dấu giọng đích: từ âm thanh mẫu (.wav), hoặc đọc thẳng file dấu giọng .npy đóng sẵn (không cần âm thanh)."""
        k = (ref, os.path.getmtime(ref))
        if k not in self._se_dich:
            if str(ref).lower().endswith(".npy"):
                g = np.load(ref, allow_pickle=False).astype(np.float32)
                if g.shape != (1, 256, 1) or not np.isfinite(g).all():
                    raise RuntimeError("File dấu giọng hỏng")
                self._se_dich[k] = g
            else:
                self._se_dich[k] = self.se(giai_ma(self.ffmpeg, ref))
        return self._se_dich[k]

    def doi(self, am, g_dich, tau=TAU):
        """Đổi chất giọng theo khúc ~20 giây (giữ RAM thấp), mỗi chỗ nối có fade 10 ms hai phía để không click."""
        khuc = SR * 20
        if len(am) <= khuc * 1.3:
            spec = pho(am)
            o = self.ss_doi.run(None, {**self.ss_doi.trong_so, "spec": spec, "g_src": self.se_nguon, "g_tgt": g_dich,
                                       "tau": np.array(tau, dtype=np.float32)})[0]
            return o.reshape(-1)
        ra = []
        for i in range(0, len(am), khuc):
            ra.append(self.doi(am[i:i + khuc], g_dich, tau))
        n = int(SR * 0.010)
        for k in range(len(ra)):
            if len(ra[k]) > 2 * n:
                ra[k] = ra[k].astype(np.float32, copy=True)
                if k > 0:
                    ra[k][:n] *= np.linspace(0.0, 1.0, n, dtype=np.float32)
                if k < len(ra) - 1:
                    ra[k][-n:] *= np.linspace(1.0, 0.0, n, dtype=np.float32)
        return np.concatenate(ra)

    def sinh(self, chu, ref, ra, cfg=2.0, buoc=16, muc="Kể chuyện", bien_the=0):
        import wave
        nd = nhac_dieu_bien_the(NHAC_DIEU.get(muc, NHAC_DIEU["Kể chuyện"]), bien_the)
        chu = re.sub(r"^\s*\([^)]*\)", "", chu).strip()     # bỏ lệnh cảm xúc kiểu VoxCPM nếu có
        f = self._truoc.pop((chu, muc), None) if not bien_the else None
        if f is not None and f.cancel():   # còn xếp hàng sau câu khác (vừa nhảy câu): tải thẳng, không chờ
            f = None
        try:
            mp3 = f.result(timeout=30) if f is not None else None
        except Exception:
            mp3 = None
        if not mp3:
            mp3 = asyncio.run(_edge(chu, GIONG_EDGE_NGUON, nd["rate"], nd["pitch"]))
        with self._khoa:
            am = giai_ma(self.ffmpeg, mp3)
            o = self.doi(am, self.se_dich(ref))
        o = np.clip(o, -1.0, 1.0)
        with wave.open(ra, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((o * 32767).astype("<i2").tobytes())
        return {"ok": True}
