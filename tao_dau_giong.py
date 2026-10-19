r"""Tạo dấu giọng (embedding OpenVoice V2, 256 số float32, 1 KB) từ một mẫu .wav để nhúng vào bản đóng sẵn.
Dùng: python tao_dau_giong.py <ref.wav> <slug>   ->  giong_nhung/<slug>/se.npy
Cần model nhẹ đã tải (%LOCALAPPDATA%\DocVoice\may_nhe) và ffmpeg. File ra KHÔNG chứa âm thanh; không đưa vào git."""
import os
import sys

import numpy as np

import may_nhe as mn


def main(wav, slug):
    m = mn.MayDocNhe()
    m.bat()
    g = np.asarray(m.se_dich(wav), dtype=np.float32)
    assert g.shape == (1, 256, 1) and np.isfinite(g).all()
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "giong_nhung", slug)
    os.makedirs(d, exist_ok=True)
    np.save(os.path.join(d, "se.npy"), g)
    print(os.path.join(d, "se.npy"), os.path.getsize(os.path.join(d, "se.npy")), "byte")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
