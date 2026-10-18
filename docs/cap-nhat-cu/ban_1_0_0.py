"""Bộ cập nhật nguyên văn của v1.0.0 (tag v1.0.0, doc_giong.py, KHO ducdg88/doc-giong).
Trích bằng ast từ git (commit 6f97ade), KHÔNG sửa tay: test kiem_tra_doc_voice.KiemTraTuCapNhat nạp file này
để chứng minh bản cũ có tự lên được bản mới không. Chỉ chứa hằng số và hàm cập nhật."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

PHIEN_BAN = "1.0.0"

KHO_GITHUB = "ducdg88/doc-giong"

TEN_EXE = "DocGiong.exe"

LINK_TAI_HOP_LE = f"https://github.com/{KHO_GITHUB}/releases/download/"

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
        cu = os.path.join(os.path.dirname(sys.executable), "DocGiong.old.exe")
        for _ in range(20):
            try:
                if os.path.exists(cu):
                    os.remove(cu)
                return
            except OSError:
                time.sleep(0.5)


