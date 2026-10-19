# -*- coding: utf-8 -*-
"""Tự kiểm chất lượng bản đọc, tách khỏi doc_voice.py để ít xung đột.

Chạy trên đầu ra (mp3 hoặc wav) cùng mốc ghép moc.json, chữ gốc và mẫu giọng; không phụ thuộc engine
(VoxCPM2, máy Nhẹ hay Edge). Hai tầng:

- Tầng tín hiệu (chỉ cần numpy và ffmpeg, luôn chạy được trong exe): mất tiếng (đoạn câm tuyệt đối giữa
  lời), im bất thường (lặng dài ngoài khe ngắt đã định), lặp đoạn (âm thanh giống hệt đoạn trước), bước
  nhảy sóng (cắt dán, click), tốc độ đọc lạ, lệch độ dài so với mốc, âm lượng và true peak.
- Tầng nặng (nhận dạng giọng faster-whisper, độ giống giọng resemblyzer): chạy qua tiến trình Python riêng
  khi máy có công cụ; không có thì ghi rõ "chưa kiểm giọng" trong báo cáo, không im lặng bỏ qua.

Báo cáo: dict (lưu .json) và bản .html cạnh file mp3; bảng tổng nhiều kịch bản.
"""
import difflib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import wave

import numpy as np

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SR_KIEM = 24000                 # tần số lấy mẫu khi phân tích
KHUNG_MS = 10                   # khung năng lượng 10 ms
DB_CAM = -70.0                  # dưới mức này coi là câm tuyệt đối (bên trong lời không được có)
DB_LANG_TUONG_DOI = 45.0        # lặng = thấp hơn đỉnh 95% bấy nhiêu dB
DB_NHIEU_TUONG_DOI = 32.0       # lặng còn nhiễu nền: thấp hơn đỉnh bấy nhiêu dB, chỉ tính khi dài từ 0,8 s
LANG_TOI_THIEU_S = 0.08         # run lặng ngắn hơn bỏ qua
MAT_TIENG_TOI_THIEU_S = 0.80    # câm tuyệt đối giữa lời từ ngần này: mất tiếng (chỗ ngắt tự nhiên của giọng tới 0,6 s)
IM_BAT_THUONG_S = 1.0           # lặng ngoài khe thiết kế từ ngần này: im bất thường
KHE_TOI_DA_S = 1.15             # khe giữa hai phần dài hơn ngần này là im bất thường (khe thiết kế tối đa 0,78 s)
NGAT_LA_S = 0.45                # lặng giữa lời từ ngần này (chưa tới mất tiếng) ghi cảnh báo nhẹ
KHE_LECH_DO_DAI_S = 0.5         # độ dài file lệch mốc hơn ngần này thì báo
NGUONG_GIONG = 0.90             # độ giống giọng cả bài tối thiểu
NGUONG_GIONG_CAU = 0.45         # câu từ 2,5 s giống 'giọng chung' của CÁC CÂU KHÁC trong chính bài (vector WeSpeaker) dưới mức này là sai giọng.
NGUONG_GIONG_CAU_NGAN = 0.30    # câu 1,5 s (vector câu ngắn dao động mạnh hơn); từ 1,5 đến 2,5 s ngưỡng tăng dần lên NGUONG_GIONG_CAU.
                                # Đo 18/10 trên Arthur thật: câu từ 2,5 s thấp nhất 0,60; câu 2 đến 2,5 s 0,52;
                                # câu 1,57 s "How long will it live?" 0,39. Đổi cao độ 4 đến 8%, tốc độ 8 đến 15% một câu Arthur: thấp nhất 0,47.
                                # Edge nam khác thay 1 câu dài: 0,14 đến 0,42 (riêng Christopher 0,49 đến 0,56 nên lọt); Edge nữ: dưới 0,30.
PHAN_TOI_THIEU_S = 1.5          # câu ngắn hơn không đo giọng (vector giọng không đáng tin)
MO_HINH_GIONG_CAU = "wespeaker_en_voxceleb_resnet34.onnx"   # vector giọng phân biệt người nói (WeSpeaker ResNet34, VoxCeleb), chạy onnxruntime CPU
MO_HINH_GIONG_CAU_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
                         + MO_HINH_GIONG_CAU)                # bản công khai, không cần khoá
MO_HINH_GIONG_CAU_SHA256 = "5ef208a9da1453335308a6b6f4e6dfbd7e183a38b604de0a57664f45d257fe94"
NGUONG_WER = 0.05               # WER cả bài tối đa (tiếng Anh)
NGUONG_WER_NGOAI = 0.20         # ngôn ngữ khác tiếng Anh: whisper small nghe kém hơn, nới ngưỡng
NGUONG_WER_CAU = 0.50           # câu có WER cao hơn thì coi là đọc sai
LUFS_MUC_TIEU = -14.0
LUFS_SAI_SO = 3.0               # lệch hơn ngần này mới KHÔNG ĐẠT (loudnorm tuyến tính bị true peak giữ lại thì ra -15 là bình thường)
LUFS_CANH_BAO = 1.0             # lệch hơn ngần này chỉ ghi cảnh báo nhẹ
TRUE_PEAK_TOI_DA = -1.0

LOAI = {
    "mat_tieng": "Mất tiếng giữa lời",
    "im_bat_thuong": "Im bất thường",
    "lap_cau": "Lặp đoạn âm thanh",
    "click_cho_ghep": "Click ở chỗ ghép",
    "toc_do_la": "Tốc độ đọc lạ",
    "lech_do_dai": "Độ dài lệch so với mốc",
    "thieu_tu": "Thiếu từ so với chữ gốc",
    "lap_tu": "Lặp từ so với chữ gốc",
    "doc_sai": "Đọc sai nhiều so với chữ gốc",
    "giong_thap": "Đoạn không giống giọng mẫu",
    "sai_giong": "Câu sai giọng so với mẫu",
    "ngat_la": "Ngắt nghỉ lạ",
    "am_luong": "Âm lượng ngoài chuẩn",
}
MUC = {"mat_tieng": "cao", "im_bat_thuong": "cao", "lap_cau": "cao", "thieu_tu": "cao", "lap_tu": "cao",
       "doc_sai": "cao", "sai_giong": "cao", "click_cho_ghep": "trung", "lech_do_dai": "trung",
       "giong_thap": "trung", "am_luong": "trung", "toc_do_la": "thap", "ngat_la": "thap"}


# ---------------------------------------------------------------- đọc âm thanh
def tim_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    for thu in (r"C:\ffmpeg\bin\ffmpeg.exe", r"E:\ffmpeg\bin\ffmpeg.exe"):
        if os.path.isfile(thu):
            return thu
    return ""


def _doc_wav(duong):
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


def doc_am(duong, sr=SR_KIEM):
    """Đọc file âm thanh thành mảng float32 một kênh ở tần số sr (wav đọc thẳng, còn lại qua ffmpeg)."""
    if duong.lower().endswith(".wav"):
        try:
            x, sr0 = _doc_wav(duong)
            if sr0 == sr:
                return x
            n = int(round(len(x) * sr / sr0))
            return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
        except Exception:
            pass
    ff = tim_ffmpeg()
    if not ff:
        raise RuntimeError("Không thấy ffmpeg để đọc " + duong)
    r = subprocess.run([ff, "-v", "error", "-nostdin", "-i", duong, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, creationflags=NO_WINDOW)
    return np.frombuffer(r.stdout, dtype=np.float32)


def khung_db(x, sr, ms=KHUNG_MS):
    k = max(1, int(sr * ms / 1000))
    n = len(x) // k
    if n == 0:
        return np.zeros(0)
    return 10 * np.log10(np.mean(x[: n * k].reshape(n, k).astype(np.float64) ** 2, axis=1) + 1e-12)


def _cac_run(mask, toi_thieu):
    """Danh sách (đầu, cuối) khung liên tiếp mask True, dài từ toi_thieu khung."""
    if len(mask) == 0:
        return []
    m = np.concatenate(([False], mask, [False]))
    d = np.diff(m.astype(np.int8))
    dau, cuoi = np.where(d == 1)[0], np.where(d == -1)[0]
    return [(int(a), int(b)) for a, b in zip(dau, cuoi) if b - a >= toi_thieu]


# ---------------------------------------------------------------- dò theo tín hiệu
def _cua_so_lang_dai(mask, toi_thieu):
    """Chỉ giữ các run True dài từ toi_thieu khung (các run ngắn hơn bỏ)."""
    ra = np.zeros(len(mask), dtype=bool)
    for a, b in _cac_run(mask, toi_thieu):
        ra[a:b] = True
    return ra


def do_cho_im(x, sr, moc=None):
    """Mất tiếng, im bất thường, ngắt lạ. Có mốc: soi từng phần (lặng nằm trong phần là lỗi, vì phần đã cắt
    lặng đầu cuối) và độ dài khe giữa hai phần. Không mốc: chỉ dò lặng dài trên cả bài."""
    db = khung_db(x, sr)
    if len(db) < 20:
        return [], {}
    dinh = float(np.percentile(db, 95))
    nguong_lang = max(DB_CAM + 5.0, dinh - DB_LANG_TUONG_DOI)
    mot_khung = KHUNG_MS / 1000
    toi_thieu = int(LANG_TOI_THIEU_S / mot_khung)
    loi = []

    nguong_nhieu = max(DB_CAM + 5.0, dinh - DB_NHIEU_TUONG_DOI)

    def xet(a, b, t_goc, trong_phan):
        # lặng dài từ 0,8 s mà nền còn nhiễu (không dưới ngưỡng lặng thường): vẫn là mất tiếng giữa lời
        ghep = db[a:b] < nguong_lang
        if trong_phan:
            ghep = ghep | _cua_so_lang_dai(db[a:b] < nguong_nhieu, int(MAT_TIENG_TOI_THIEU_S / mot_khung))
        for u, v in _cac_run(ghep, toi_thieu):
            dai = (v - u) * mot_khung
            t0 = t_goc + u * mot_khung
            cam = float(np.mean(db[a + u:a + v] < DB_CAM))
            if dai >= IM_BAT_THUONG_S:
                loi.append({"loai": "im_bat_thuong", "bd": round(t0, 2), "kt": round(t0 + dai, 2),
                            "mo_ta": f"Im {dai:.1f} giây giữa lời"})
            elif dai >= MAT_TIENG_TOI_THIEU_S:
                # lặng tương đối (thấp hơn đỉnh 45 dB) dài từ 0,8 s ngay giữa một phần: mất tiếng, dù nền còn nhiễu
                kieu = "câm tuyệt đối" if cam >= 0.6 else "chỉ còn nhiễu nền"
                loi.append({"loai": "mat_tieng", "bd": round(t0, 2), "kt": round(t0 + dai, 2),
                            "mo_ta": f"Mất tiếng {dai:.2f} giây giữa lời ({kieu})"})
            elif dai >= NGAT_LA_S and trong_phan:
                loi.append({"loai": "ngat_la", "bd": round(t0, 2), "kt": round(t0 + dai, 2),
                            "mo_ta": f"Ngắt {dai:.2f} giây giữa lời"})

    if moc:
        for bd, kt, _ in moc:
            a, b = int((bd + 0.03) / mot_khung), int((kt - 0.03) / mot_khung)
            if b - a > toi_thieu:
                xet(a, min(b, len(db)), a * mot_khung, True)
        for i in range(len(moc) - 1):
            khe = moc[i + 1][0] - moc[i][1]
            if khe > KHE_TOI_DA_S:
                loi.append({"loai": "im_bat_thuong", "bd": round(moc[i][1], 2), "kt": round(moc[i + 1][0], 2),
                            "mo_ta": f"Khe giữa hai phần dài {khe:.1f} giây"})
    else:
        xet(int(0.05 / mot_khung), len(db) - int(0.05 / mot_khung), 0.05, False)
    return loi, {"dinh_db": round(dinh, 1), "nguong_lang_db": round(nguong_lang, 1)}


def do_click_cho_ghep(x, sr, moc, cua_ms=40, buoc_yen=0.09, buoc_cung=0.25, he_so_cung=4.0):
    """Click ở chỗ ghép, chạy được trên mp3 sau hậu kỳ (mp3 lệch mốc tới ~25 ms vì bộ lọc, nên cửa sổ rộng
    cộng trừ cua_ms quanh mỗi đầu và cuối phần). Hai kiểu, đều theo ngữ cảnh cục bộ chứ không theo phân vị cả bài:
    1. Xung nhảy từ yên lặng: bước nhảy mẫu lớn hơn buoc_yen mà 1,5 ms ngay trước đó yên (biên độ dưới 0,03).
       Đầu phần đã fade nên đầu phần thật không thể nhảy từ yên lặng lên cao trong một mẫu.
    2. Nối cứng giữa hai đoạn to: bước nhảy trên buoc_cung và gấp he_so_cung lần phân vị 99 bước nhảy quanh đó
       (loại bỏ âm xát to, vốn bước nhảy lớn nhưng cũng lớn quanh nó).
    Mỗi mốc ghi tối đa một lỗi. Trả danh sách lỗi click_cho_ghep."""
    if not moc or len(x) < sr:
        return []
    xf = x.astype(np.float32)
    d = np.abs(np.diff(xf))
    ax = np.abs(xf)
    w = int(sr * cua_ms / 1000)
    y = max(2, int(sr * 0.0015))
    rong = int(sr * 0.1)
    loi = []
    da = set()
    for m in moc:
        for t in (m[0], m[1]):
            c = int(t * sr)
            a, b = max(y, c - w), min(len(d), c + w)
            if b <= a:
                continue
            tim = None
            for j in np.where(d[a:b] > buoc_yen)[0] + a:
                if ax[j - y:j].max() < 0.03:
                    tim = int(j)
                    break
            if tim is None:
                j = a + int(np.argmax(d[a:b]))
                if d[j] > buoc_cung:
                    gan = np.concatenate([d[max(0, j - rong):max(0, j - 72)], d[j + 72:j + rong]])
                    if len(gan) > 100 and d[j] > he_so_cung * float(np.percentile(gan, 99)):
                        tim = int(j)
            if tim is not None and tim // 240 not in da:
                da.add(tim // 240)
                loi.append({"loai": "click_cho_ghep", "bd": round(max(0.0, tim / sr - 0.01), 3),
                            "kt": round(tim / sr + 0.01, 3), "mo_ta": f"Click ở chỗ ghép gần {t:.2f} giây"})
    return loi


def _dac_trung(x, sr, so_dai=20, hop_ms=20):
    """Ma trận (số khung, so_dai): log năng lượng theo dải mel thô."""
    hop = int(sr * hop_ms / 1000)
    nfft = 512
    if len(x) < nfft + hop:
        return np.zeros((0, so_dai), dtype=np.float32)
    so_k = 1 + (len(x) - nfft) // hop
    chi = np.arange(nfft)[None, :] + hop * np.arange(so_k)[:, None]
    cua = np.hanning(nfft).astype(np.float32)
    pho = np.abs(np.fft.rfft(x[chi] * cua, axis=1)) ** 2
    mel = lambda f: 2595 * np.log10(1 + f / 700.0)
    bien = 700 * (10 ** (np.linspace(mel(100), mel(min(8000, sr / 2 - 1)), so_dai + 1) / 2595) - 1)
    tan = np.fft.rfftfreq(nfft, 1.0 / sr)
    ra = np.empty((so_k, so_dai), dtype=np.float32)
    for b in range(so_dai):
        m = (tan >= bien[b]) & (tan < bien[b + 1])
        ra[:, b] = np.log10(pho[:, m].sum(axis=1) + 1e-9) if m.any() else -9.0
    return ra


def do_lap_doan(x, sr, moc=None, khung_s=1.5, buoc_s=0.06, nguong=0.94, dai_toi_thieu_s=1.6):
    """Đoạn âm thanh lặp lại gần như y hệt đoạn trước đó. Giọng tổng hợp không bao giờ đọc hai lần giống
    hệt nhau, nên khớp gần tuyệt đối là bản sao. Trả danh sách lỗi lap_cau (bd, kt, goc_bd, goc_kt)."""
    gop = 3                                   # gộp 3 khung 20 ms thành 60 ms cho đặc trưng mượt, chịu lệch pha
    hop_ms = 20 * gop
    f = _dac_trung(x, sr, hop_ms=20)
    m = len(f) // gop * gop
    if m < 200:
        return []
    f = f[:m].reshape(-1, gop, f.shape[1]).mean(axis=1)
    L = int(khung_s * 1000 / hop_ms)
    buoc = max(1, int(buoc_s * 1000 / hop_ms))
    dau = np.arange(0, len(f) - L, buoc)
    if len(dau) < 6:
        return []
    nl = f.mean(axis=1)
    co_f = nl > (np.percentile(nl, 95) - 3.0)
    cua = np.stack([f[a:a + L] for a in dau])
    co_tieng = np.array([co_f[a:a + L].mean() >= 0.6 for a in dau])
    v = (cua - cua.mean(axis=2, keepdims=True)).reshape(len(dau), -1)
    v -= v.mean(axis=1, keepdims=True)
    v = (v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)).astype(np.float32)
    n = len(dau)
    cach = int(round((L + 2) / buoc))
    khop = []
    kich = 1024
    for r0 in range(0, n, kich):
        r1 = min(n, r0 + kich)
        s = v[r0:r1] @ v.T
        # đoạn lặp lệch lưới 60 ms: lấy điểm tốt nhất trong ba cột kề nhau
        sm = s.copy()
        sm[:, 1:-1] = np.maximum(s[:, 1:-1], np.maximum(s[:, :-2], s[:, 2:]))
        for ri in range(r1 - r0):
            i = r0 + ri
            if not co_tieng[i] or i - cach < 1:
                continue
            hang = sm[ri, : i - cach]
            ok = np.where((hang >= nguong) & co_tieng[: i - cach])[0]
            for j in ok:
                khop.append((int(j), i))
    if not khop:
        return []
    theo_cheo = {}
    for j, i in khop:
        theo_cheo.setdefault((i - j) // 3, []).append((j, i))
    nhom_ra = []
    for ds in theo_cheo.values():
        ds.sort(key=lambda p: p[1])
        nhom = [ds[0]]
        for p in ds[1:]:
            if p[1] - nhom[-1][1] <= 4:
                nhom.append(p)
            else:
                nhom_ra.append(nhom)
                nhom = [p]
        nhom_ra.append(nhom)
    loi = []
    for nhom in nhom_ra:
        j0, i0 = nhom[0]
        j1, i1 = nhom[-1]
        bd_i = dau[i0] * hop_ms / 1000
        kt_i = dau[i1] * hop_ms / 1000 + khung_s
        bd_j = dau[j0] * hop_ms / 1000
        kt_j = dau[j1] * hop_ms / 1000 + khung_s
        if kt_i - bd_i < dai_toi_thieu_s:
            continue
        loi.append({"loai": "lap_cau", "bd": round(float(bd_i), 2), "kt": round(float(kt_i), 2),
                    "goc_bd": round(float(bd_j), 2), "goc_kt": round(float(kt_j), 2),
                    "mo_ta": f"Đoạn {bd_i:.1f} đến {kt_i:.1f} giây lặp lại y hệt đoạn {bd_j:.1f} đến {kt_j:.1f} giây"})
    return _bo_lap_theo_kich_ban(_bo_trung(loi), moc)


def _bo_trung(loi):
    """Bỏ các đoạn lặp bị chứa trong đoạn lặp khác."""
    loi = sorted(loi, key=lambda e: (e["bd"], -(e["kt"] - e["bd"])))
    ra = []
    for e in loi:
        if any(o["bd"] - 0.2 <= e["bd"] and e["kt"] <= o["kt"] + 0.2 for o in ra):
            continue
        ra.append(e)
    return ra


def _bo_lap_theo_kich_ban(loi, moc):
    """Kịch bản tự lặp một câu ở hai phần khác nhau (cùng chữ) thì bản đọc giống nhau là đúng, không phải lỗi.
    Hai đoạn nằm trong CÙNG một phần thì là lặp thật (phần không tự lặp chính nó)."""
    if not moc:
        return loi
    ra = []
    for e in loi:
        i = phan_chua(moc, (e["bd"] + e["kt"]) / 2)
        j = phan_chua(moc, (e["goc_bd"] + e["goc_kt"]) / 2)
        if i is not None and j is not None and i != j:
            ci = re.sub(r"\W+", " ", moc[i][2].lower()).strip()
            cj = re.sub(r"\W+", " ", moc[j][2].lower()).strip()
            if ci == cj:
                continue
        ra.append(e)
    return ra


# ---------------------------------------------------------------- tốc độ, âm lượng
def do_toc_do(x, sr, moc):
    """Mỗi phần: số từ trên giây có tiếng. Phần lệch xa trung vị là tốc độ lạ (đọc cụt, kéo dài)."""
    if not moc:
        return []
    db = khung_db(x, sr)
    if len(db) == 0:
        return []
    nguong = float(np.percentile(db, 95)) - 35.0
    thong = []
    for bd, kt, chu in moc:
        a, b = int(bd * 100), int(kt * 100)
        so_tu = len(chu.split())
        co = int(np.sum(db[a:b] > nguong)) / 100.0
        if so_tu >= 4 and co > 0.2:
            thong.append((so_tu / co, bd, kt))
    if len(thong) < 4:
        return []
    trung_vi = float(np.median([t[0] for t in thong]))
    loi = []
    for v, bd, kt in thong:
        if v > 1.9 * trung_vi or v < 0.5 * trung_vi:
            loi.append({"loai": "toc_do_la", "bd": round(bd, 2), "kt": round(kt, 2),
                        "mo_ta": f"{v:.1f} từ mỗi giây (trung vị bài {trung_vi:.1f})"})
    return loi


def do_am_luong(duong):
    ff = tim_ffmpeg()
    if not ff:
        return None
    r = subprocess.run([ff, "-hide_banner", "-nostdin", "-i", duong, "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr or "")
    p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr or "")
    return {"lufs": float(i[-1]) if i else None, "true_peak": float(p[-1]) if p else None}


# ---------------------------------------------------------------- so chữ nghe được với chữ gốc
def _tach_so_chu(m):
    """9am -> 9 am, nhưng 2nd, 3rd, 4th, 1st giữ nguyên để chuẩn hoá thành số thứ tự."""
    if m.group(2).lower() in ("st", "nd", "rd", "th", "s", "k", "m", "x"):
        return m.group(0)
    return m.group(1) + " " + m.group(2)


def _ghep_chu_don(ds):
    """Chuỗi chữ cái đứng riêng liền nhau (u s, a m) là một từ viết tắt (us, am): whisper viết liền, chuẩn hoá tách rời."""
    ra, run = [], []
    for t in ds:
        if len(t) == 1 and t.isalpha():
            run.append(t)
            continue
        if run:
            ra.append("".join(run) if len(run) > 1 else run[0])
            run = []
        ra.append(t)
    if run:
        ra.append("".join(run) if len(run) > 1 else run[0])
    return ra


def _tu(chu, ngon_ngu="en"):
    """Tách từ để so lời. Tiếng Anh: chuẩn hoá như lúc đọc (số, năm, viết tắt thành chữ) để chữ gốc và chữ nghe được
    cùng một dạng; ngôn ngữ khác: chỉ hạ chữ thường và bỏ dấu câu."""
    if ngon_ngu == "en":
        try:
            import giong_clone as gc
            return _ghep_chu_don(gc.chu_cho_wer(re.sub(r"([0-9])([A-Za-z]+)", _tach_so_chu, chu or "")))
        except Exception:
            pass
    chu = (chu or "").lower().replace(chr(8217), chr(39))
    return re.findall(r"[\w']+", chu)


def doan_ngon_ngu(moc):
    """vi nếu chữ gốc có nhiều chữ cái riêng của tiếng Việt, ngược lại en."""
    chu = " ".join(m[2] for m in (moc or []) if len(m) > 2)
    cai = [c for c in chu if c.isalpha()]
    if not cai:
        return "en"
    rieng = sum(1 for c in cai if c.lower() in "ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ")
    return "vi" if rieng / len(cai) > 0.12 else "en"


def so_cau(chu_goc, chu_nghe, ngon_ngu="en"):
    """So hai chuỗi từ: thiếu, thừa, lặp từ liền kề, WER. Không phân biệt hoa thường và dấu câu."""
    a, b = _tu(chu_goc, ngon_ngu), _tu(chu_nghe, ngon_ngu)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    thieu, thua = [], []
    sai = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            thieu += a[i1:i2]
            sai += i2 - i1
        elif tag == "insert":
            thua += b[j1:j2]
            sai += j2 - j1
        elif tag == "replace":
            thieu += a[i1:i2]
            thua += b[j1:j2]
            sai += max(i2 - i1, j2 - j1)
    goc_lap = {a[m] for m in range(1, len(a)) if a[m] == a[m - 1]}
    lap = [b[k] for k in range(1, len(b)) if b[k] == b[k - 1] and b[k] not in goc_lap]
    return {"wer": sai / max(1, len(a)), "thieu": thieu, "thua": thua, "lap": lap, "so_tu": len(a)}


def _cum_lap(nghe, goc, n=4):
    """Cụm n từ liền nhau xuất hiện từ hai lần trong chữ nghe được mà chữ gốc không lặp cụm đó (máy đọc đọc lại
    một câu: giọng không giống hệt bit nên tầng tín hiệu không bắt được, lời thì lặp)."""
    dem_goc, dem_nghe = {}, {}
    for k in range(len(goc) - n + 1):
        c = tuple(goc[k:k + n])
        dem_goc[c] = dem_goc.get(c, 0) + 1
    for k in range(len(nghe) - n + 1):
        c = tuple(nghe[k:k + n])
        dem_nghe[c] = dem_nghe.get(c, 0) + 1
    for k in range(len(nghe) - n + 1):
        c = tuple(nghe[k:k + n])
        if dem_nghe[c] >= 2 and dem_nghe[c] > dem_goc.get(c, 0):
            return list(c)
    return []


def loi_tu_nghe(moc, nghe_theo_phan, ngon_ngu="en"):
    """nghe_theo_phan: {chỉ số phần: chữ nhận dạng}. Sinh lỗi thieu_tu, lap_tu, doc_sai theo từng phần."""
    loi = []
    for i, nghe in nghe_theo_phan.items():
        i = int(i)
        if i >= len(moc) or nghe is None:
            continue
        bd, kt, chu = moc[i][0], moc[i][1], moc[i][2]
        r = so_cau(chu, nghe, ngon_ngu)
        cum = _cum_lap(_tu(nghe, ngon_ngu), _tu(chu, ngon_ngu))
        if r["lap"]:
            loi.append({"loai": "lap_tu", "bd": round(bd, 2), "kt": round(kt, 2), "phan": i,
                        "mo_ta": "Lặp từ: " + " ".join(r["lap"][:5])})
        elif cum:
            loi.append({"loai": "lap_tu", "bd": round(bd, 2), "kt": round(kt, 2), "phan": i,
                        "mo_ta": "Đọc lặp cả cụm: " + " ".join(cum[:8])})
        elif len(r["thua"]) >= max(4, 0.4 * r["so_tu"]):
            loi.append({"loai": "lap_tu", "bd": round(bd, 2), "kt": round(kt, 2), "phan": i,
                        "mo_ta": f"Đọc thừa {len(r['thua'])} từ so với chữ gốc: " + " ".join(r["thua"][:6])})
        elif len(r["thieu"]) >= 4 and r["wer"] > 0.12:
            loi.append({"loai": "thieu_tu", "bd": round(bd, 2), "kt": round(kt, 2), "phan": i,
                        "mo_ta": "Thiếu từ: " + " ".join(r["thieu"][:6])})
        elif r["wer"] > NGUONG_WER_CAU and r["so_tu"] >= 5:
            loi.append({"loai": "doc_sai", "bd": round(bd, 2), "kt": round(kt, 2), "phan": i,
                        "mo_ta": f"Nghe lệch {r['wer']:.0%} so với chữ gốc"})
    return loi


def kiem_phan_tin_hieu(x, sr):
    """Kiểm nhanh một phần đã sinh (trước khi ghép): mất tiếng, im lạ, nhảy sóng. Rỗng nếu sạch."""
    x = np.asarray(x, dtype=np.float32)
    db = khung_db(x, sr)
    if len(db) < 30:
        return []
    dinh = float(np.percentile(db, 95))
    nguong = max(DB_CAM + 5.0, dinh - DB_LANG_TUONG_DOI)
    co = np.where(db > nguong)[0]
    if len(co) == 0:
        return [{"loai": "mat_tieng", "bd": 0.0, "kt": round(len(x) / sr, 2), "mo_ta": "Phần không có tiếng"}]
    trong = db[co[0]:co[-1] + 1]
    loi = []
    for a, b in _cac_run(trong < nguong, int(0.2 * 1000 / KHUNG_MS)):
        dai = (b - a) * KHUNG_MS / 1000
        t0 = (co[0] + a) * KHUNG_MS / 1000
        cam = float(np.mean(trong[a:b] < DB_CAM))
        if dai >= IM_BAT_THUONG_S:
            loi.append({"loai": "im_bat_thuong", "bd": round(t0, 2), "kt": round(t0 + dai, 2),
                        "mo_ta": f"Im {dai:.1f} giây giữa phần"})
        elif dai >= MAT_TIENG_TOI_THIEU_S or cam >= 0.6:
            loi.append({"loai": "mat_tieng", "bd": round(t0, 2), "kt": round(t0 + dai, 2),
                        "mo_ta": f"Mất tiếng {dai:.2f} giây giữa phần"})
    return loi


# ---------------------------------------------------------------- tầng tín hiệu trọn bài
def kiem_tin_hieu(duong_am, moc=None, ten="", sr=SR_KIEM):
    """Tầng tín hiệu trên file âm thanh. moc: [(bd, kt, chữ)] hoặc None."""
    t0 = time.time()
    x = doc_am(duong_am, sr)
    giay = len(x) / sr
    loi = []
    thong = {"giay": round(giay, 2)}
    if moc:
        moc = [(float(m[0]), float(m[1]), str(m[2]) if len(m) > 2 else "") for m in moc]
        cuoi_moc = moc[-1][1]
        if abs(giay - cuoi_moc) > KHE_LECH_DO_DAI_S:
            loi.append({"loai": "lech_do_dai", "bd": round(min(giay, cuoi_moc), 2), "kt": round(max(giay, cuoi_moc), 2),
                        "mo_ta": f"File dài {giay:.1f} giây, mốc ghép dài {cuoi_moc:.1f} giây"})
    l1, th = do_cho_im(x, sr, moc)
    thong.update(th)
    loi += l1
    loi += do_click_cho_ghep(x, sr, moc)
    loi += do_lap_doan(x, sr, moc)
    loi += do_toc_do(x, sr, moc)
    thong["giay_kiem"] = round(time.time() - t0, 1)
    loi.sort(key=lambda e: (e["bd"], e["loai"]))
    return {"kich_ban": ten or os.path.basename(duong_am), "am": duong_am, "thong_so": thong, "loi": loi,
            "am_luong": do_am_luong(duong_am)}


def phan_chua(moc, t):
    """Chỉ số phần của mốc chứa thời điểm t (hoặc phần gần nhất), None nếu không có mốc."""
    if not moc:
        return None
    for i, m in enumerate(moc):
        if m[0] - 0.05 <= t <= m[1] + 0.05:
            return i
    return min(range(len(moc)), key=lambda i: min(abs(moc[i][0] - t), abs(moc[i][1] - t)))


def gan_phan(loi, moc):
    for e in loi:
        if "phan" not in e and moc:
            e["phan"] = phan_chua(moc, (e["bd"] + e["kt"]) / 2)
    return loi


# ---------------------------------------------------------------- tầng nặng: nhận dạng giọng, độ giống giọng
# Sai giọng từng câu: vector resemblyzer không tách được giọng nam Edge khác khỏi Arthur (cùng vùng
# 0,70 đến 0,80) nên quyết định theo vector WeSpeaker và SO SÁNH TƯƠNG ĐỐI trong chính bài: mỗi câu so với trung vị
# vector của mọi câu KHÁC (bỏ chính nó ra), không so với một ngưỡng tuyệt đối với mẫu giọng.
_MA_GIONG_CAU = r'''
import numpy as np


def _mel_bank(sr=16000, n_fft=512, n_mels=80, fmin=20.0, fmax=8000.0):
    hz2mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)
    diem = 700.0 * (10 ** (np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2) / 2595.0) - 1.0)
    f = np.linspace(0, sr / 2, n_fft // 2 + 1)
    W = np.zeros((n_mels, len(f)))
    for i in range(n_mels):
        a, b, c = diem[i], diem[i + 1], diem[i + 2]
        W[i] = np.maximum(0, np.minimum((f - a) / (b - a), (c - f) / (c - b)))
    return W


_MEL80 = _mel_bank()


def fbank_kaldi(x):
    """Fbank 80 kiểu Kaldi (cửa sổ 25 ms, bước 10 ms, povey, nhấn 0,97, trừ trung bình) cho WeSpeaker. None nếu quá ngắn."""
    x = np.asarray(x, dtype=np.float64) * 32768.0
    n = 1 + (len(x) - 400) // 160
    if n < 10:
        return None
    f = x[np.arange(400)[None, :] + 160 * np.arange(n)[:, None]]
    f = f - f.mean(1, keepdims=True)
    f = np.concatenate([f[:, :1] * 0.03, f[:, 1:] - 0.97 * f[:, :-1]], 1)
    f = f * (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(400) / 399)) ** 0.85
    fb = np.log(np.maximum((np.abs(np.fft.rfft(f, 512)) ** 2) @ _MEL80.T, 1.1920929e-07))
    return (fb - fb.mean(0)).astype(np.float32)


def giong_so_voi_bai(E, them=None):
    """E: vector giọng (đã chuẩn hoá) của các câu. Trả độ giống của từng câu với trung vị vector các câu KHÁC.
    Bài dưới 3 câu đo được thì thêm vector mẫu giọng (them) vào nhóm so sánh."""
    E = np.asarray(E, dtype=np.float64)
    ra = []
    for j in range(len(E)):
        o = np.delete(E, j, 0)
        if them is not None and len(o) < 2:
            o = np.concatenate([o, np.asarray(them, dtype=np.float64)[None, :]])
        if not len(o):
            ra.append(None)
            continue
        c = np.median(o, 0)
        c /= np.linalg.norm(c) + 1e-12
        ra.append(float(E[j] @ c))
    return ra
'''
exec(_MA_GIONG_CAU)

# Giữ giống hệt giong_clone._MA_DOC_TRUOC (test KiemTraKiemDocKhongTreo so hai bản).
_MA_DOC_TRUOC = r'''
def doc_truoc_model(ten):
    """Đọc trước model.bin bằng khối 16 MB cho file vào bộ đệm hệ điều hành, rồi mới nạp. ctranslate2 tự đọc theo
    khối nhỏ: trên ổ HDD đang bận, mỗi khối phải xếp hàng nên chỉ còn khoảng 0,1 MB/s (model small 483 MB mất
    cả giờ, NV-1299); khối lớn trên cùng ổ vẫn khoảng 44 MB/s. Chưa có model trong bộ nhớ đệm thì bỏ qua (sẽ tải)."""
    try:
        import os
        if os.path.isdir(ten):
            thu_muc = ten
        else:
            from faster_whisper.utils import download_model
            thu_muc = download_model(ten, local_files_only=True)
        buf = bytearray(16 << 20)
        with open(os.path.join(thu_muc, "model.bin"), "rb", buffering=0) as f:
            while f.readinto(buf):
                pass
    except Exception:
        pass
'''

_MA_NANG = _MA_GIONG_CAU + _MA_DOC_TRUOC + r'''
import json, subprocess, sys, os, hashlib
cfg = json.load(open(sys.argv[1], encoding="utf-8"))
NW = 0x08000000

def doc(duong, sr=16000):
    r = subprocess.run([cfg["ffmpeg"], "-v", "error", "-nostdin", "-i", duong, "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True, creationflags=NW)
    return np.frombuffer(r.stdout, dtype=np.float32)

kq = {"nghe": {}, "giong": None, "loi": []}
am = doc(cfg["am"])
if cfg.get("whisper"):
    try:
        from faster_whisper import WhisperModel
        doc_truoc_model(cfg.get("model", "small"))
        try:   # bộ nhớ đệm trước, không gọi mạng (máy không mạng hay proxy chết vẫn nạp được)
            m = WhisperModel(cfg.get("model", "small"), device="cpu", compute_type="int8", cpu_threads=4,
                             local_files_only=True)
        except Exception:
            m = WhisperModel(cfg.get("model", "small"), device="cpu", compute_type="int8", cpu_threads=4)
        for i, (bd, kt) in enumerate(cfg["moc"]):
            if str(i) in cfg.get("bo_qua", []):
                continue
            a, b = int(max(0, bd - 0.25) * 16000), int((kt + 0.25) * 16000)
            segs, _ = m.transcribe(am[a:b], language=cfg.get("ngon_ngu", "en"), beam_size=1,
                                   condition_on_previous_text=False, vad_filter=False)
            kq["nghe"][str(i)] = " ".join(s.text.strip() for s in segs)
    except Exception as e:
        kq["loi"].append("whisper: " + str(e)[:200])
if cfg.get("ref"):
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav
        enc = VoiceEncoder("cpu")
        e_ref = enc.embed_utterance(preprocess_wav(doc(cfg["ref"]), 16000))
        e_ra = enc.embed_utterance(preprocess_wav(am, 16000))
        tung_phan = []
        e_phan = []
        for i, (bd, kt) in enumerate(cfg["moc"]):
            if kt - bd < cfg.get("phan_toi_thieu_s", 1.5):          # câu quá ngắn: vector giọng không đáng tin
                continue
            k = am[int(bd * 16000):int(kt * 16000)]
            try:
                e_phan.append((i, enc.embed_utterance(preprocess_wav(k, 16000))))
            except Exception:
                continue
        if e_phan:
            E = np.array([v for _, v in e_phan])
            tt = np.median(E, axis=0)
            tt /= np.linalg.norm(tt)
            for (i, v) in e_phan:
                tung_phan.append([i, float(np.dot(e_ref, v)), float(np.dot(tt, v))])
        kq["giong"] = {"ca_bai": float(np.dot(e_ref, e_ra)), "tung_phan": tung_phan}
    except Exception as e:
        kq["loi"].append("giong: " + str(e)[:200])


def lay_mo_hinh(p, url, sha):
    if os.path.isfile(p):
        return p
    import urllib.request
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tam = p + ".tai"
    with urllib.request.urlopen(url, timeout=120) as r, open(tam, "wb") as f:
        while True:
            k = r.read(1 << 20)
            if not k:
                break
            f.write(k)
    with open(tam, "rb") as f:
        h = hashlib.sha256(f.read()).hexdigest()
    if h != sha:
        os.remove(tam)
        raise RuntimeError("mo hinh giong tai ve sai ma bam")
    os.replace(tam, p)
    return p


if cfg.get("mo_hinh_cau"):
    try:
        import onnxruntime as ort
        ses = ort.InferenceSession(lay_mo_hinh(cfg["mo_hinh_cau"], cfg["mo_hinh_cau_url"], cfg["mo_hinh_cau_sha256"]),
                                   providers=["CPUExecutionProvider"])

        def vec(k):
            f = fbank_kaldi(k)
            if f is None:
                return None
            e = ses.run(None, {"feats": f[None]})[0][0]
            return e / (np.linalg.norm(e) + 1e-12)

        chi, E = [], []
        for i, (bd, kt) in enumerate(cfg["moc"]):
            if kt - bd < cfg.get("phan_toi_thieu_s", 1.5):
                continue
            v = vec(am[int(bd * 16000):int(kt * 16000)])
            if v is not None:
                chi.append(i)
                E.append(v)
        e_mau = vec(doc(cfg["ref"])) if cfg.get("ref_cau") else None
        diem = giong_so_voi_bai(E, e_mau) if E else []
        kq["giong_cau"] = {"may": "wespeaker-resnet34",
                           "tung_cau": [[i, d, (float(E[j] @ e_mau) if e_mau is not None else None)]
                                        for j, (i, d) in enumerate(zip(chi, diem)) if d is not None]}
    except Exception as e:
        kq["loi"].append("giong_cau: " + str(e)[:200])
print("KQ>" + json.dumps(kq, ensure_ascii=True), flush=True)     # chỉ ASCII: ống dẫn cp1252 không chết
'''


def tim_python_nang(goi_y=""):
    """Python có faster_whisper (và nếu có thì resemblyzer). Trả (đường python, có_whisper, có_giọng)."""
    ung_vien = [goi_y] if goi_y else []
    for p in (r"E:\DocGiong-tools\.venv-do\Scripts\python.exe",):
        ung_vien.append(p)
    for ten in ("python", "py"):
        p = shutil.which(ten)
        if p and "WindowsApps" not in p:
            ung_vien.append(p)
    thay = None
    for p in ung_vien:
        if not p or not os.path.isfile(p):
            continue
        try:
            r = subprocess.run([p, "-I", "-c", "import importlib.util as u;print(int(bool(u.find_spec(\"faster_whisper\"))),"
                                "int(bool(u.find_spec(\"resemblyzer\"))))"],
                               capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
            w, g = (r.stdout.split() + ["0", "0"])[:2]
            if w == "1" and g == "1":
                return p, True, True
            if w == "1" and thay is None:
                thay = (p, True, False)
        except Exception:
            continue
    return thay or ("", False, False)


def nguong_giong_cau(giay):
    """Ngưỡng độ giống với các câu khác theo độ dài câu: 0,30 ở 1,5 s, tăng dần tới 0,45 từ 2,5 s."""
    t = min(1.0, max(0.0, (giay - PHAN_TOI_THIEU_S) / 1.0))
    return round(NGUONG_GIONG_CAU_NGAN + (NGUONG_GIONG_CAU - NGUONG_GIONG_CAU_NGAN) * t, 4)


def duong_mo_hinh_giong_cau():
    """Chỗ để mô hình giọng từng câu: cạnh mã nguồn (mo_hinh/) nếu có, không thì thư mục máy của DocVoice (tự tải lần đầu)."""
    canh = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mo_hinh", MO_HINH_GIONG_CAU)
    if os.path.isfile(canh):
        return canh
    goc = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(goc, "DocVoice", "mo_hinh", MO_HINH_GIONG_CAU)


def kiem_nang(duong_am, moc, ref="", python="", model="small", ngon_ngu="en", nghe_san=None, thu_muc_tam=None,
              cho_toi_da=3600):
    """Chạy tầng nặng ở tiến trình Python riêng. Trả dict {nghe, giong, loi, chua_kiem:[...]}.
    nghe_san: {chỉ số phần: chữ đã nhận dạng} (từ lúc sinh) để khỏi nhận dạng lại."""
    py, co_w, co_g = (python, True, True) if python else tim_python_nang()
    ra = {"nghe": dict(nghe_san or {}), "giong": None, "giong_cau": None, "loi": [], "chua_kiem": []}
    if not py:
        ra["chua_kiem"].append("Chưa kiểm giọng: máy chưa có Python kèm faster-whisper")
        return ra
    ff = tim_ffmpeg()
    thu = thu_muc_tam or os.path.dirname(os.path.abspath(duong_am))
    os.makedirs(thu, exist_ok=True)
    ma = os.path.join(thu, "kiem_nang_worker.py")
    cfg_p = os.path.join(thu, "kiem_nang_cfg.json")
    with open(ma, "w", encoding="utf-8") as f:
        f.write(_MA_NANG)
    cfg = {"ffmpeg": ff, "am": duong_am, "moc": [[m[0], m[1]] for m in moc], "whisper": co_w,
           "model": model, "ngon_ngu": ngon_ngu, "ref": ref if (ref and co_g) else "",
           "bo_qua": [str(k) for k in (nghe_san or {})], "phan_toi_thieu_s": PHAN_TOI_THIEU_S,
           "ref_cau": ref if ref else "",
           "mo_hinh_cau": duong_mo_hinh_giong_cau(), "mo_hinh_cau_url": MO_HINH_GIONG_CAU_URL,
           "mo_hinh_cau_sha256": MO_HINH_GIONG_CAU_SHA256}
    with open(cfg_p, "w", encoding="utf-8") as f:
        json.dump(cfg, f)
    try:
        r = subprocess.run([py, "-I", ma, cfg_p], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=cho_toi_da, creationflags=NO_WINDOW)
        dong = [d for d in (r.stdout or "").splitlines() if d.startswith("KQ>")]
        if not dong:
            ra["chua_kiem"].append("Chưa kiểm giọng: tiến trình kiểm không trả kết quả")
            ra["loi"].append((r.stderr or "")[-300:])
            return ra
        kq = json.loads(dong[-1][3:])
    except Exception as e:
        ra["chua_kiem"].append("Chưa kiểm giọng: " + str(e)[:160])
        return ra
    ra["nghe"].update(kq.get("nghe", {}))
    ra["giong"] = kq.get("giong")
    ra["giong_cau"] = kq.get("giong_cau")
    ra["loi"] += kq.get("loi", [])
    if ra["giong_cau"] is None:
        ra["chua_kiem"].append("Chưa đo sai giọng từng câu: máy chưa có onnxruntime hoặc chưa tải được mô hình giọng")
    if ra["giong"] is None:
        ra["chua_kiem"].append("Chưa đo độ giống giọng: máy chưa có resemblyzer hoặc thiếu mẫu giọng")
    if not co_w:
        ra["chua_kiem"].append("Chưa nhận dạng giọng: máy chưa có faster-whisper")
    return ra


# ---------------------------------------------------------------- kết luận, báo cáo
def _muc_cao(loi):
    return [e for e in loi if MUC.get(e["loai"]) == "cao"]


def ket_luan(bc):
    """Gán dat (True/False) và ly_do cho báo cáo dựa trên ngưỡng."""
    ly_do = []
    g = bc.get("giong_giong")
    if g and g.get("ca_bai") is not None and g["ca_bai"] < NGUONG_GIONG:
        ly_do.append(f"Giống giọng {g['ca_bai']:.3f}, dưới ngưỡng {NGUONG_GIONG}")
    w = bc.get("wer")
    ng_wer = NGUONG_WER if bc.get("ngon_ngu", "en") == "en" else NGUONG_WER_NGOAI   # model nhỏ nghe tiếng ngoài tiếng Anh kém hơn
    if w is not None and w > ng_wer:
        ly_do.append(f"WER {w:.1%}, trên ngưỡng {ng_wer:.0%}")
    al = bc.get("am_luong") or {}
    if al.get("lufs") is not None and abs(al["lufs"] - LUFS_MUC_TIEU) > LUFS_SAI_SO:
        ly_do.append(f"Âm lượng {al['lufs']:.1f} LUFS, lệch quá {LUFS_SAI_SO} dB so với {LUFS_MUC_TIEU}")
    if al.get("true_peak") is not None and al["true_peak"] > TRUE_PEAK_TOI_DA:
        ly_do.append(f"True peak {al['true_peak']:.1f} dBFS, vượt {TRUE_PEAK_TOI_DA}")
    dem = {}
    for e in bc.get("loi", []):
        if MUC.get(e["loai"]) == "cao" or e["loai"] == "click_cho_ghep":
            dem[e["loai"]] = dem.get(e["loai"], 0) + 1
    for k, v in dem.items():
        ly_do.append(f"{v} lỗi: {LOAI[k].lower()}")
    bc["dat"] = not ly_do
    bc["ly_do"] = ly_do
    return bc


def kiem_day_du(duong_am, moc=None, ref="", ten="", nang=True, nghe_san=None, python="", model="small",
                ngon_ngu="auto", thu_muc_tam=None):
    """Chấm một bản đọc: tầng tín hiệu luôn chạy; tầng nặng khi nang=True và máy có công cụ."""
    bc = kiem_tin_hieu(duong_am, moc, ten)
    moc_n = [(float(m[0]), float(m[1]), str(m[2]) if len(m) > 2 else "") for m in (moc or [])]
    bc["chua_kiem"] = []
    if ngon_ngu == "auto":
        ngon_ngu = doan_ngon_ngu(moc_n)
    bc["ngon_ngu"] = ngon_ngu
    if nang and moc_n:
        n = kiem_nang(duong_am, moc_n, ref, python, model, ngon_ngu, nghe_san, thu_muc_tam)
        bc["chua_kiem"] = n["chua_kiem"]
        if n["nghe"]:
            bc["loi"] += loi_tu_nghe(moc_n, n["nghe"], ngon_ngu)
            idx = sorted(int(k) for k in n["nghe"])
            goc = " ".join(moc_n[i][2] for i in idx if i < len(moc_n))
            nghe = " ".join(n["nghe"][str(i)] for i in idx if i < len(moc_n))
            bc["wer"] = round(so_cau(goc, nghe, ngon_ngu)["wer"], 4)
            bc["so_phan_da_nghe"] = len(idx)
        if n.get("giong"):
            tung = n["giong"].get("tung_phan", [])
            bc["giong_giong"] = {"ca_bai": round(n["giong"]["ca_bai"], 4), "so_phan_do": len(tung),
                                 "thap_nhat_so_voi_mau": round(min(t[1] for t in tung), 4) if tung else None}
        if n.get("giong_cau"):
            cau = n["giong_cau"].get("tung_cau", [])
            g = bc.setdefault("giong_giong", {})
            g["may_do_cau"] = n["giong_cau"].get("may")
            g["so_cau_do"] = len(cau)
            g["thap_nhat_so_voi_bai"] = round(min(t[1] for t in cau), 4) if cau else None
            for t in cau:
                i, s_bai = t[0], t[1]
                if i >= len(moc_n):
                    continue
                ng = nguong_giong_cau(moc_n[i][1] - moc_n[i][0])
                if s_bai < ng:
                    bc["loi"].append({"loai": "sai_giong", "bd": round(moc_n[i][0], 2), "kt": round(moc_n[i][1], 2), "phan": i,
                                      "mo_ta": f"Câu chỉ giống {s_bai:.2f} so với giọng chung của các câu khác trong bài "
                                               f"(dưới {ng:.2f}): nghe như giọng khác"})
        if n["loi"]:
            bc["loi_cong_cu"] = n["loi"]
    else:
        bc["chua_kiem"] = ["Chưa kiểm giọng (nhận dạng giọng, độ giống giọng): chưa bật hoặc không có mốc"]
    al = bc.get("am_luong") or {}
    if al.get("lufs") is not None and LUFS_CANH_BAO < abs(al["lufs"] - LUFS_MUC_TIEU) <= LUFS_SAI_SO:
        bc["loi"].append({"loai": "am_luong", "bd": 0.0, "kt": bc["thong_so"]["giay"],
                          "mo_ta": f"Âm lượng {al['lufs']:.1f} LUFS, lệch {abs(al['lufs'] - LUFS_MUC_TIEU):.1f} dB so với {LUFS_MUC_TIEU}"})
    bc["moc_so_phan"] = len(moc_n)
    gan_phan(bc["loi"], moc_n)
    bc["loi"].sort(key=lambda e: (e["bd"], e["loai"]))
    return ket_luan(bc)


def _gio(t):
    return f"{int(t // 60)}:{t % 60:05.2f}"


def html_bao_cao(bc):
    mau = {"cao": "#c0392b", "trung": "#d68910", "thap": "#7f8c8d"}
    hang = ""
    for e in bc.get("loi", []):
        hang += ("<tr%s><td style=\"color:%s\"><b>%s</b></td><td>%s đến %s</td><td>%s</td></tr>"
                 % (" style=\"background:#fdecea\"" if e.get("do") else "", mau.get(MUC.get(e["loai"]), "#000"), html.escape(LOAI.get(e["loai"], e["loai"])),
                    _gio(e["bd"]), _gio(e["kt"]), html.escape(e.get("mo_ta", ""))))
    if not hang:
        hang = "<tr><td colspan=3>Không phát hiện lỗi.</td></tr>"
    g = bc.get("giong_giong") or {}
    thong = [f"Thời lượng {bc['thong_so']['giay']:.1f} giây"]
    if g:
        thong.append(f"Giống giọng {g['ca_bai']:.3f}")
    if bc.get("wer") is not None:
        thong.append(f"WER {bc['wer']:.1%}")
    al = bc.get("am_luong") or {}
    if al.get("lufs") is not None:
        thong.append(f"{al['lufs']:.1f} LUFS, peak {al['true_peak']} dBFS")
    kq = "ĐẠT" if bc["dat"] else "KHÔNG ĐẠT"
    mau_kq = "#1e8449" if bc["dat"] else "#c0392b"
    chua = "".join("<li>%s</li>" % html.escape(c) for c in bc.get("chua_kiem", []))
    for v in bc.get("sinh_lai", []):
        chua += "<li>Vòng sinh lại %d: phần %s, lỗi %d rồi %s</li>" % (
            v["vong"], ", ".join(str(x + 1) for x in v["phan"]), v["so_loi_truoc"], v.get("so_loi_sau", "?"))
    lydo = "".join("<li>%s</li>" % html.escape(c) for c in bc.get("ly_do", []))
    return ("<!doctype html><html lang=\"vi\"><head><meta charset=\"utf-8\"><title>Kiểm chất lượng</title>"
            "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;max-width:900px}"
            "table{border-collapse:collapse;width:100%%}td,th{border:1px solid #ccc;padding:6px 8px;text-align:left}"
            "</style></head><body><h2>%s: <span style=\"color:%s\">%s</span></h2><p>%s</p><ul>%s</ul>"
            "<h3>Lỗi phát hiện (%d)</h3><table><tr><th>Loại</th><th>Mốc</th><th>Chi tiết</th></tr>%s</table>"
            "<ul>%s</ul></body></html>"
            % (html.escape(bc.get("kich_ban", "")), mau_kq, kq, html.escape(" | ".join(thong)), lydo,
               len(bc.get("loi", [])), hang, chua))


def luu_bao_cao(bc, mp3):
    goc = os.path.splitext(mp3)[0] + "-kiem-chat-luong"
    with open(goc + ".json", "w", encoding="utf-8") as f:
        json.dump(bc, f, ensure_ascii=False, indent=1)
    with open(goc + ".html", "w", encoding="utf-8") as f:
        f.write(html_bao_cao(bc))
    return goc + ".json", goc + ".html"


def bang_tong(cac_bc):
    """Bảng tổng nhiều kịch bản: danh sách dict gọn."""
    dong = []
    for bc in cac_bc:
        g = (bc.get("giong_giong") or {}).get("ca_bai")
        dong.append({"kich_ban": bc.get("kich_ban"), "dat": bc.get("dat"), "giong": g, "wer": bc.get("wer"),
                     "so_loi": len(bc.get("loi", [])), "so_loi_cao": len(_muc_cao(bc.get("loi", [])))})
    return dong


def bang_tong_chu(dong):
    ra = ["%-34s %-10s %-7s %-7s %s" % ("Kịch bản", "Kết quả", "Giống", "WER", "Số lỗi (nặng)")]
    for d in dong:
        ra.append("%-34s %-10s %-7s %-7s %d (%d)" % (
            str(d["kich_ban"])[:34], "ĐẠT" if d["dat"] else "KHÔNG ĐẠT",
            "-" if d["giong"] is None else f"{d['giong']:.3f}",
            "-" if d["wer"] is None else f"{d['wer']:.1%}", d["so_loi"], d["so_loi_cao"]))
    return "\n".join(ra)


def html_bang_tong(dong):
    h = ""
    for d in dong:
        h += ("<tr><td>%s</td><td style=\"color:%s\"><b>%s</b></td><td>%s</td><td>%s</td><td>%d (%d)</td></tr>"
              % (html.escape(str(d["kich_ban"])), "#1e8449" if d["dat"] else "#c0392b",
                 "ĐẠT" if d["dat"] else "KHÔNG ĐẠT", "-" if d["giong"] is None else f"{d['giong']:.3f}",
                 "-" if d["wer"] is None else f"{d['wer']:.1%}", d["so_loi"], d["so_loi_cao"]))
    return ("<!doctype html><html lang=\"vi\"><head><meta charset=\"utf-8\"><title>Bảng tổng kiểm chất lượng</title>"
            "<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px}table{border-collapse:collapse}"
            "td,th{border:1px solid #ccc;padding:6px 10px}</style></head><body><h2>Bảng tổng kiểm chất lượng</h2>"
            "<table><tr><th>Kịch bản</th><th>Kết quả</th><th>Giống giọng</th><th>WER</th><th>Số lỗi (nặng)</th></tr>"
            "%s</table></body></html>" % h)


# ---------------------------------------------------------------- sinh lại đoạn lỗi
def phan_can_sinh_lai(bc):
    """Chỉ số các phần có lỗi nặng (theo mốc) cần sinh lại."""
    return sorted({e["phan"] for e in bc.get("loi", [])
                   if e.get("phan") is not None and (MUC.get(e["loai"]) == "cao" or e["loai"] == "click_cho_ghep")
                   and e["loai"] not in ("lech_do_dai", "giong_thap")})


def xoa_phan_de_sinh_lai(thu_muc_viec, thu_muc_kho, chi_so):
    """Xoá wav của các phần (và bản trong kho câu) để lần chạy kế đọc lại đúng các phần đó."""
    file_so = os.path.join(thu_muc_viec, "so_viec.json")
    so = json.load(open(file_so, encoding="utf-8"))
    da = []
    for i in chi_so:
        khoa = f"{i:05d}"
        rec = so.get("phan", {}).pop(khoa, None)
        for p in (os.path.join(thu_muc_viec, f"phan_{khoa}.wav"), os.path.join(thu_muc_viec, f"phan_{khoa}.toc.wav")):
            if os.path.isfile(p):
                os.remove(p)
        if rec and rec.get("khoa_cau"):
            kho = os.path.join(thu_muc_kho, rec["khoa_cau"] + ".wav")
            if os.path.isfile(kho):
                os.remove(kho)
        da.append(i)
    with open(file_so, "w", encoding="utf-8") as f:
        json.dump(so, f, ensure_ascii=False)
    return da


def kiem_va_sua(doc_lai, kiem_mot, toi_da=2):
    """Vòng chấm rồi sinh lại đoạn lỗi. doc_lai(chi_so) đọc lại các phần đó và xuất lại;
    kiem_mot() trả báo cáo. Trả (báo cáo cuối, nhật ký các vòng). Vẫn lỗi sau toi_da vòng thì đánh dấu đỏ."""
    nhat_ky = []
    bc = kiem_mot()
    for lan in range(toi_da):
        ds = phan_can_sinh_lai(bc)
        if not ds:
            break
        nhat_ky.append({"vong": lan + 1, "phan": ds, "so_loi_truoc": len(bc["loi"])})
        doc_lai(ds)
        bc = kiem_mot()
        nhat_ky[-1]["so_loi_sau"] = len(bc["loi"])
    for e in bc.get("loi", []):
        if MUC.get(e["loai"]) == "cao":
            e["do"] = True
    bc["sinh_lai"] = nhat_ky
    return bc, nhat_ky


# ---------------------------------------------------------------- chấm một việc của app, chạy nhiều kịch bản
def doc_moc_viec(thu_muc_viec):
    """Mốc ghép của một việc clone: (danh sách (bắt đầu, kết thúc, chữ), đường mp3 đã xuất)."""
    with open(os.path.join(thu_muc_viec, "moc.json"), encoding="utf-8") as f:
        j = json.load(f)
    return [tuple(m) for m in j["muc_tg"]], j.get("mp3", "")


def kiem_mot_viec(thu_muc_viec, mp3="", ref="", ten="", nang=True, toi_da=2, doc_lai=None, thu_muc_kho="",
                  luu=True, **kw):
    """Chấm bản đọc của một việc clone (thư mục có moc.json) rồi sinh lại phần lỗi nặng tối đa toi_da vòng.
    doc_lai() chạy lại đường đọc của app: nó chỉ đọc các phần vừa bị xoá, ghép và xuất lại mp3 và moc.json."""
    def kiem_mot():
        moc, mp3_moc = doc_moc_viec(thu_muc_viec)
        return kiem_day_du(mp3 or mp3_moc, moc, ref, ten, nang=nang, **kw)
    if doc_lai is None or toi_da <= 0:
        bc = kiem_mot()
        bc["sinh_lai"] = []
    else:
        def sinh(ds):
            xoa_phan_de_sinh_lai(thu_muc_viec, thu_muc_kho, ds)
            doc_lai()
        bc, _ = kiem_va_sua(sinh, kiem_mot, toi_da)
    if luu:
        bc["bao_cao"] = luu_bao_cao(bc, mp3 or doc_moc_viec(thu_muc_viec)[1])
    return bc


def chay_nhieu(cac_kich_ban, doc_mot, toi_da=2, nang=True, thu_muc_ra=None, bao=None):
    """Chạy lần lượt nhiều kịch bản [(tên, văn bản)]. doc_mot(tên, văn bản) đọc xong và trả
    {"mp3", "viec", "ref", "doc_lai", "kho"}. Trả (bảng tổng, các báo cáo, đường html bảng tổng)."""
    cac_bc = []
    for i, (ten, vb) in enumerate(cac_kich_ban):
        if bao:
            bao(f"Kịch bản {i + 1}/{len(cac_kich_ban)}: {ten}")
        try:
            r = doc_mot(ten, vb)
            bc = kiem_mot_viec(r["viec"], r["mp3"], r.get("ref", ""), ten, nang, toi_da, r.get("doc_lai"),
                               r.get("kho", ""))
        except Exception as e:
            bc = {"kich_ban": ten, "dat": False, "ly_do": [f"Không đọc hoặc không chấm được: {e}"], "loi": [],
                  "chua_kiem": [], "thong_so": {"giay": 0.0}}
        cac_bc.append(bc)
    dong = bang_tong(cac_bc)
    duong = ""
    if thu_muc_ra:
        os.makedirs(thu_muc_ra, exist_ok=True)
        duong = os.path.join(thu_muc_ra, "bang-tong-kiem-chat-luong.html")
        with open(duong, "w", encoding="utf-8") as f:
            f.write(html_bang_tong(dong))
        with open(os.path.splitext(duong)[0] + ".json", "w", encoding="utf-8") as f:
            json.dump(dong, f, ensure_ascii=False, indent=1)
    return dong, cac_bc, duong


# ---------------------------------------------------------------- dòng lệnh
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Kiểm chất lượng bản đọc")
    ap.add_argument("--am", required=True, help="file mp3 hoặc wav")
    ap.add_argument("--moc", default="", help="moc.json của lần ghép")
    ap.add_argument("--ref", default="", help="mẫu giọng ref.wav")
    ap.add_argument("--ten", default="")
    ap.add_argument("--khong-nang", action="store_true", help="chỉ chạy tầng tín hiệu")
    a = ap.parse_args(argv)
    moc = None
    if a.moc:
        moc = [tuple(m) for m in json.load(open(a.moc, encoding="utf-8"))["muc_tg"]]
    bc = kiem_day_du(a.am, moc, a.ref, a.ten, nang=not a.khong_nang)
    j, h = luu_bao_cao(bc, a.am)
    print(json.dumps({"dat": bc["dat"], "ly_do": bc["ly_do"], "so_loi": len(bc["loi"]), "json": j, "html": h},
                     ensure_ascii=False, indent=1))
    return 0 if bc["dat"] else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
