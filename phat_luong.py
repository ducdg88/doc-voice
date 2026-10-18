"""Phát luồng: nghe ngay trong lúc máy đọc còn sinh các câu sau.

lay_phan(i) trả (mảng float32, tần số mẫu); thiết bị mở theo tần số của phần đầu tiên, phần khác tần số
thì nội suy lại. Phát qua waveOut của Windows (ctypes, không thư viện ngoài, không mở cửa sổ), ghi từng khối 80 ms,
giữ hàng đợi khoảng 0,4 giây. Lấy âm thanh từng phần qua hàm lay_phan(i) trả mảng float32, hoặc None nếu
phần đó chưa sinh xong; chưa có thì chờ (thiết bị cạn hàng đợi trong lúc chờ thì tính là một lần vấp).
Tạm dừng, Tiếp, Dừng, phát từ phần bất kỳ, báo phần đang nghe để tô chữ và thanh tiến độ.

Đệm thích ứng: máy đọc chậm hơn tiếng phát (máy bận, hệ số thời gian thực RTF trên 1) thì phát
từng câu một sẽ vấp đi vấp lại. Nên khi máy bận (RTF trung bình các câu đã đọc trên 0,85; mới có 1 đến 2 câu thì trên
1,1; xét lại mỗi vòng trước khi phát tiếng đầu) hoặc vừa vấp, luồng gom sẵn một lượng
tiếng phía trước (DEM_DAU 6 giây; mỗi lần vấp thêm gấp đôi, tối đa DEM_TOI_DA 30 giây) rồi mới phát. Gom tới khi đủ
lượng, hoặc các câu còn lại đều đã sẵn, hoặc quá DEM_CHO_TOI_DA giây. Máy rảnh (RTF dưới 0,85) thì không đệm thêm,
tiếng đầu ra sớm như cũ."""
import ctypes
import threading
import time
from ctypes import wintypes

import numpy as np

_wm = ctypes.windll.winmm
WAVE_MAPPER = 0xFFFFFFFF
WHDR_DONE = 0x1
TIME_SAMPLES = 0x2


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD), ("nSamplesPerSec", wintypes.DWORD),
                ("nAvgBytesPerSec", wintypes.DWORD), ("nBlockAlign", wintypes.WORD),
                ("wBitsPerSample", wintypes.WORD), ("cbSize", wintypes.WORD)]


class _WAVEHDR(ctypes.Structure):
    pass


_WAVEHDR._fields_ = [("lpData", ctypes.c_void_p), ("dwBufferLength", wintypes.DWORD),
                     ("dwBytesRecorded", wintypes.DWORD), ("dwUser", ctypes.c_size_t),
                     ("dwFlags", wintypes.DWORD), ("dwLoops", wintypes.DWORD),
                     ("lpNext", ctypes.POINTER(_WAVEHDR)), ("reserved", ctypes.c_size_t)]


class _MMTIME(ctypes.Structure):
    _fields_ = [("wType", wintypes.UINT), ("u", wintypes.DWORD * 2)]


_wm.waveOutOpen.argtypes = [ctypes.POINTER(ctypes.c_void_p), wintypes.UINT, ctypes.POINTER(_WAVEFORMATEX),
                            ctypes.c_size_t, ctypes.c_size_t, wintypes.DWORD]
for _ten in ("waveOutPrepareHeader", "waveOutUnprepareHeader", "waveOutWrite"):
    getattr(_wm, _ten).argtypes = [ctypes.c_void_p, ctypes.POINTER(_WAVEHDR), wintypes.UINT]
for _ten in ("waveOutReset", "waveOutClose", "waveOutPause", "waveOutRestart"):
    getattr(_wm, _ten).argtypes = [ctypes.c_void_p]
_wm.waveOutGetPosition.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MMTIME), wintypes.UINT]
_wm.waveOutSetVolume.argtypes = [ctypes.c_void_p, wintypes.DWORD]


class PhatLuong:
    KHOI = 0.08          # giây mỗi khối ghi ra thiết bị
    HANG_DOI = 5         # số khối giữ sẵn trong thiết bị (khoảng 0,4 giây)
    NGUONG_RTF = 0.85    # trung bình các câu đã đọc chậm hơn mức này (giây sinh / giây tiếng) thì coi là máy bận, đệm trước
    NGUONG_RTF_DAU = 1.1  # mới đọc được 1 đến 2 câu (đo chưa chắc, câu đầu hay chậm do khởi động): ngưỡng nới hơn
    SO_CAU_TIN_CAY = 3   # từ ngần này câu đã đọc trở lên thì dùng NGUONG_RTF
    DEM_DAU = 6.0        # giây tiếng gom sẵn trước khi phát lúc máy bận, và sau lần vấp đầu
    DEM_TOI_DA = 30.0    # mỗi lần vấp, lượng đệm gấp đôi nhưng không quá mức này
    DEM_CHO_TOI_DA = 45.0  # đợi gom đệm không quá lâu: quá thì phát những gì đang có

    def __init__(self, so_phan, lay_phan, khi_doi_phan=None, khi_het=None, im_lang=False, do_rtf=None, khi_dem=None):
        self.sr = None
        self.loi = ""
        self.so_phan = so_phan
        self.lay_phan = lay_phan
        self.khi_doi_phan = khi_doi_phan or (lambda i: None)
        self.khi_het = khi_het or (lambda: None)
        self.do_rtf = do_rtf or (lambda: None)         # hệ số thời gian thực của máy đọc đến lúc này (None: chưa biết)
        self.khi_dem = khi_dem or (lambda dang: None)  # True khi bắt đầu gom đệm, False khi xong (để hiện "máy đang bận")
        self.muc_dem = 0.0              # lượng đệm (giây) đòi trước khi phát lại; 0 là phát ngay
        self.so_lan_dem = 0
        self.giay_dem = 0.0             # tổng giây chờ gom đệm (chưa tính vào giay_vap nếu là đệm lúc bắt đầu)
        self._truoc = {}                # phần -> (mảng, tần số) đã lấy sẵn khi gom đệm
        self.im_lang = im_lang          # chỉ để đo: thiết bị vẫn chạy đúng nhịp nhưng âm lượng 0
        self._lenh = threading.Condition()
        self._nhay = None               # phần cần nhảy tới (bấm câu)
        self._tam_dung = False
        self._dung = False
        self.dang_phan = None
        self.so_lan_vap = 0
        self.giay_vap = 0.0
        self.t_ghi_dau = None           # lúc ghi khối đầu tiên ra thiết bị
        self.t_ra_loa = None            # lúc thiết bị báo đã phát mẫu đầu tiên
        self.giay_da_phat = 0.0
        self.lech_doi_phan = {}         # phần -> (giây trễ của lúc báo so với lúc mẫu đầu của phần ra loa, lúc báo)
        self._luong = None

    # ---- điều khiển từ giao diện
    def phat_tu(self, i=0):
        with self._lenh:
            self._nhay = max(0, min(self.so_phan - 1, int(i)))
            self._dung = False
            self._tam_dung = False
            self._lenh.notify_all()
        if self._luong is None or not self._luong.is_alive():
            self._luong = threading.Thread(target=self._chay, daemon=True)
            self._luong.start()

    def tam_dung(self):
        with self._lenh:
            self._tam_dung = True

    def tiep(self):
        with self._lenh:
            self._tam_dung = False

    def dung(self):
        with self._lenh:
            self._dung = True
        if self._luong is not None and self._luong is not threading.current_thread():
            self._luong.join(timeout=3)

    @property
    def dang_tam_dung(self):
        return self._tam_dung

    @property
    def dang_chay(self):
        return self._luong is not None and self._luong.is_alive()

    # ---- luồng phát
    def _chay(self):
        hwo = ctypes.c_void_p()
        dang_doi = []            # [(WAVEHDR, bộ đệm, chỉ số phần, mẫu kết thúc tích luỹ)]
        dau_phan = {}            # phần -> mẫu bắt đầu (tích luỹ từ lần reset gần nhất)
        da_ghi = 0               # tổng mẫu đã ghi kể từ lần reset gần nhất
        khoi = 0
        i, vi_tri, am = 0, 0, None
        dang_pause = False
        bat_dau_vap = None
        bat_dau_dem = None
        mmt = _MMTIME()
        try:
            while True:
                with self._lenh:
                    if self._dung:
                        break
                    if self._nhay is not None:              # nhảy tới câu khác: bỏ phần đang xếp hàng
                        i, vi_tri, am = self._nhay, 0, None
                        self._nhay = None
                        if hwo:
                            _wm.waveOutReset(hwo)
                        self._don(hwo, dang_doi, tat_ca=True)
                        da_ghi = 0
                        dau_phan = {}
                        bat_dau_vap = None
                        self._truoc.clear()
                        self.muc_dem = 0.0               # bấm câu: nghe ngay, vấp lại thì mới đệm
                        if bat_dau_dem is not None:
                            bat_dau_dem = None
                            self.khi_dem(False)
                        dang_pause = False
                    if hwo and self._tam_dung != dang_pause:
                        (_wm.waveOutPause if self._tam_dung else _wm.waveOutRestart)(hwo)
                        dang_pause = self._tam_dung
                self._don(hwo, dang_doi)
                mmt.wType = TIME_SAMPLES                    # phần đang nghe theo vị trí thật của thiết bị
                if hwo and _wm.waveOutGetPosition(hwo, ctypes.byref(mmt), ctypes.sizeof(mmt)) == 0:
                    pos = mmt.u[0]
                    if pos > 0 and self.t_ra_loa is None:
                        self.t_ra_loa = time.time()
                    for _h, _b, pi, ket in dang_doi:
                        if pos < ket:
                            if pi != self.dang_phan:
                                self.dang_phan = pi
                                if pi in dau_phan and self.sr:
                                    self.lech_doi_phan[pi] = (max(0.0, (pos - dau_phan[pi]) / self.sr), time.time())
                                self.khi_doi_phan(pi)
                            break
                if dang_pause:
                    time.sleep(0.03)
                    continue
                if len(dang_doi) >= self.HANG_DOI:
                    time.sleep(0.01)
                    continue
                if am is None:
                    if i >= self.so_phan:
                        if not dang_doi:
                            break                        # phát hết
                        time.sleep(0.02)
                        continue
                    lay = self._lay(i)
                    if lay is None:                      # câu này chưa sinh xong: chờ
                        if not dang_doi and self.t_ghi_dau is not None and bat_dau_vap is None:
                            bat_dau_vap = time.time()
                            self.so_lan_vap += 1
                            self.muc_dem = self.DEM_DAU if self.muc_dem <= 0 else min(self.DEM_TOI_DA, self.muc_dem * 2)
                        time.sleep(0.02)
                        continue
                    if self.t_ghi_dau is None and not dang_doi:
                        # Chưa phát tiếng nào: xét lại mỗi vòng theo tốc độ trung bình các câu đã đọc (không chỉ câu
                        # đầu, câu đầu ngắn và hay chậm do khởi động). Máy bận thì đệm trước vài câu, hết bận thì thôi.
                        self.muc_dem = self.DEM_DAU if self._may_ban(i) else 0.0
                    can = self.muc_dem > 0 and not dang_doi and self._can_dem(i, lay)
                    if can and bat_dau_dem is not None and time.time() - bat_dau_dem > self.DEM_CHO_TOI_DA:
                        can = False                      # đợi quá lâu: phát những gì đang có, không đứng im
                    if can:
                        if bat_dau_dem is None:          # chưa đủ đệm: giữ lại, chưa phát
                            bat_dau_dem = time.time()
                            self.so_lan_dem += 1
                            self.khi_dem(True)
                        time.sleep(0.05)
                        continue
                    if bat_dau_dem is not None:
                        self.giay_dem += time.time() - bat_dau_dem
                        bat_dau_dem = None
                        self.khi_dem(False)
                    am, sr = lay
                    self._truoc.pop(i, None)
                    am = np.asarray(am, dtype=np.float32)
                    if not hwo:                          # mở thiết bị theo tần số mẫu của phần đầu tiên
                        self.sr = int(sr)
                        fmt = _WAVEFORMATEX(1, 1, self.sr, self.sr * 2, 2, 16, 0)
                        if _wm.waveOutOpen(ctypes.byref(hwo), WAVE_MAPPER, ctypes.byref(fmt), 0, 0, 0) != 0:
                            hwo = ctypes.c_void_p()
                            self.loi = "Không mở được thiết bị phát âm thanh."
                            break
                        if self.im_lang:
                            _wm.waveOutSetVolume(hwo, 0)
                        khoi = int(self.sr * self.KHOI)
                    elif int(sr) != self.sr and len(am):
                        n = int(round(len(am) * self.sr / sr))
                        am = np.interp(np.linspace(0, len(am) - 1, n), np.arange(len(am)), am).astype(np.float32)
                    vi_tri = 0
                if bat_dau_vap is not None:
                    self.giay_vap += time.time() - bat_dau_vap
                    bat_dau_vap = None
                doan = am[vi_tri:vi_tri + khoi]
                vi_tri += len(doan)
                if len(doan):
                    du = (np.clip(doan, -1.0, 1.0) * 32767).astype("<i2").tobytes()
                    bo_dem = ctypes.create_string_buffer(du, len(du))
                    h = _WAVEHDR()
                    h.lpData = ctypes.cast(bo_dem, ctypes.c_void_p)
                    h.dwBufferLength = len(du)
                    _wm.waveOutPrepareHeader(hwo, ctypes.byref(h), ctypes.sizeof(h))
                    _wm.waveOutWrite(hwo, ctypes.byref(h), ctypes.sizeof(h))
                    da_ghi += len(doan)
                    self.giay_da_phat += len(doan) / self.sr
                    dau_phan.setdefault(i, da_ghi - len(doan))
                    dang_doi.append((h, bo_dem, i, da_ghi))
                    if self.t_ghi_dau is None:
                        self.t_ghi_dau = time.time()
                if vi_tri >= len(am):
                    i, am = i + 1, None
        finally:
            if hwo:
                _wm.waveOutReset(hwo)
                self._don(hwo, dang_doi, tat_ca=True)
                _wm.waveOutClose(hwo)
            self.dang_phan = None
            if bat_dau_dem is not None:
                self.khi_dem(False)
            self.khi_het()

    def _may_ban(self, i):
        """True nếu máy đọc đang bận: RTF trung bình các câu đã đọc vượt ngưỡng (ít câu thì ngưỡng nới). do_rtf lỗi
        (ví dụ tranh chấp luồng: từ điển đang được luồng sinh thêm khoá) thì coi như chưa biết, không làm chết luồng phát."""
        try:
            rtf = self.do_rtf()
        except Exception:
            return False
        if rtf is None:
            return False
        n, j = 0, i
        while n < self.SO_CAU_TIN_CAY and j < self.so_phan and self._lay(j) is not None:
            n, j = n + 1, j + 1
        return rtf > (self.NGUONG_RTF if n >= self.SO_CAU_TIN_CAY else self.NGUONG_RTF_DAU)

    def _lay(self, j):
        """Âm thanh phần j (lấy sẵn nếu đã gom), None nếu chưa sinh xong."""
        if j in self._truoc:
            return self._truoc[j]
        r = self.lay_phan(j)
        if r is not None:
            self._truoc[j] = r
        return r

    def _can_dem(self, i, lay_i):
        """True nếu còn phải đệm thêm: tiếng sẵn từ phần i trở đi chưa tới muc_dem giây, các câu sau chưa đủ sẵn."""
        co = len(lay_i[0]) / float(lay_i[1])
        j = i + 1
        while co < self.muc_dem and j < self.so_phan:
            r = self._lay(j)
            if r is None:
                break
            co += len(r[0]) / float(r[1])
            j += 1
        return not (co >= self.muc_dem or j >= self.so_phan)   # đủ đệm, hoặc mọi câu còn lại đều đã sẵn: thôi

    @staticmethod
    def _don(hwo, dang_doi, tat_ca=False):
        while dang_doi and (tat_ca or dang_doi[0][0].dwFlags & WHDR_DONE):
            h = dang_doi.pop(0)[0]
            _wm.waveOutUnprepareHeader(hwo, ctypes.byref(h), ctypes.sizeof(h))
