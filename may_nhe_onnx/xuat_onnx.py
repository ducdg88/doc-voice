"""Xuất bộ đổi chất giọng OpenVoice V2 ra ONNX dạng KHUNG (không chứa trọng số lớn).

Trọng số được nạp lúc chạy từ checkpoint.pth công khai trên Hugging Face (myshell-ai/OpenVoiceV2,
MIT) bằng bộ đọc không cần torch; file này chỉ ghi đồ thị + công thức map tên trọng số.
Chạy bằng venv có torch + onnx: .venv-ov\\Scripts\\python.exe scripts\\xuat_onnx.py <thư mục ra>
"""
import json
import os
import sys
import types

import numpy as np
import torch

sys.path.insert(0, r"E:\DocGiong-tools\ov-src")
fake = types.ModuleType("openvoice.text")
fake.text_to_sequence = None
sys.modules["openvoice.text"] = fake
from openvoice import utils  # noqa: E402
from openvoice.models import SynthesizerTrn  # noqa: E402
import onnx  # noqa: E402
from onnx import numpy_helper  # noqa: E402

RA = sys.argv[1]
os.makedirs(RA, exist_ok=True)
CFG = r"E:\DocGiong-tools\ov-model\config.json"
CKPT = r"E:\DocGiong-tools\ov-model\checkpoint.pth"
hps = utils.get_hparams_from_file(CFG)
model = SynthesizerTrn(0, hps.data.filter_length // 2 + 1, n_speakers=hps.data.n_speakers, **hps.model)
sd_goc = torch.load(CKPT, map_location="cpu")["model"]
model.load_state_dict(sd_goc, strict=False)
model.eval()
for m in model.modules():
    try:
        torch.nn.utils.remove_weight_norm(m)
    except (ValueError, AttributeError):
        pass
sd = {k: v.detach().numpy() for k, v in model.state_dict().items()}


class Ref(torch.nn.Module):
    def __init__(s, m):
        super().__init__()
        s.ref_enc = m.ref_enc

    def forward(s, spec_t):            # [1, T, 513]
        return s.ref_enc(spec_t).unsqueeze(-1)


class Doi(torch.nn.Module):
    def __init__(s, m):
        super().__init__()
        s.enc_q, s.flow, s.dec = m.enc_q, m.flow, m.dec

    def forward(s, spec, g_src, g_tgt, tau):   # spec [1,513,T]
        n = torch.ones(1, dtype=torch.long) * spec.shape[2]
        z, m_q, logs_q, y_mask = s.enc_q(spec, n, g=torch.zeros_like(g_src), tau=tau)
        z_p = s.flow(z, y_mask, g=g_src)
        z_hat = s.flow(z_p, y_mask, g=g_tgt, reverse=True)
        return s.dec(z_hat * y_mask, g=torch.zeros_like(g_tgt))


def khung(duong_onnx, ten_ra):
    """Bỏ trọng số khớp checkpoint khỏi đồ thị, ghi công thức nạp lại."""
    mo = onnx.load(duong_onnx)
    cong_thuc, giu = {}, []
    for init in mo.graph.initializer:
        a = numpy_helper.to_array(init)
        ct = None
        if init.name in sd and sd[init.name].shape == a.shape and np.allclose(sd[init.name], a, atol=1e-6):
            ct = {"kieu": "ten", "khoa": init.name}
        else:
            # GRU của ref_enc: ONNX xếp cổng z,r,h; torch xếp r,z,n
            for loai, khoa in (("W", "ref_enc.gru.weight_ih_l0"), ("R", "ref_enc.gru.weight_hh_l0")):
                t = sd[khoa]
                h = t.shape[0] // 3
                doi = np.concatenate([t[h:2 * h], t[:h], t[2 * h:]])[None]
                if doi.shape == a.shape and np.allclose(doi, a, atol=1e-6):
                    ct = {"kieu": "gru", "khoa": khoa}
            bi, bh = sd["ref_enc.gru.bias_ih_l0"], sd["ref_enc.gru.bias_hh_l0"]
            h = bi.shape[0] // 3
            r = lambda b: np.concatenate([b[h:2 * h], b[:h], b[2 * h:]])
            bb = np.concatenate([r(bi), r(bh)])[None]
            if bb.shape == a.shape and np.allclose(bb, a, atol=1e-6):
                ct = {"kieu": "gru_bias"}
        if ct and a.size > 64:
            cong_thuc[init.name] = ct
        else:
            giu.append(init)
    con = [i for i in mo.graph.initializer if i.name not in cong_thuc]
    bo = [i for i in mo.graph.initializer if i.name in cong_thuc]
    for i in bo:   # trọng số tách ra thành đầu vào của đồ thị, lúc chạy nạp từ checkpoint
        mo.graph.input.append(onnx.helper.make_tensor_value_info(i.name, i.data_type, list(i.dims)))
    del mo.graph.initializer[:]
    mo.graph.initializer.extend(con)
    onnx.save(mo, os.path.join(RA, ten_ra))
    lon = sum(numpy_helper.to_array(i).size for i in con)
    print(ten_ra, "trong so tach:", len(cong_thuc), "con giu:", len(con), "so phan tu giu:", lon)
    return cong_thuc


T = 200
spec = torch.randn(1, 513, T).abs()
g = torch.randn(1, 256, 1)
tam = os.path.join(RA, "_tam.onnx")
with torch.no_grad():
    torch.onnx.export(Ref(model), (spec.transpose(1, 2),), tam, input_names=["spec_t"], output_names=["g"],
                      dynamic_axes={"spec_t": {1: "T"}}, opset_version=17, do_constant_folding=False, dynamo=False)
    ct_ref = khung(tam, "ov_ref.onnx")
    torch.onnx.export(Doi(model), (spec, g, g, torch.tensor(0.3)), tam, input_names=["spec", "g_src", "g_tgt", "tau"],
                      output_names=["am"], dynamic_axes={"spec": {2: "T"}, "am": {2: "N"}}, opset_version=17,
                      do_constant_folding=False, dynamo=False)
    ct_doi = khung(tam, "ov_doi.onnx")
os.remove(tam)
with open(os.path.join(RA, "ov_cong_thuc.json"), "w", encoding="utf-8") as f:
    json.dump({"ov_ref.onnx": ct_ref, "ov_doi.onnx": ct_doi, "nguon": "huggingface.co/myshell-ai/OpenVoiceV2 converter/checkpoint.pth (MIT)",
               "sha256_checkpoint": None}, f, ensure_ascii=False, indent=0)

# so khớp: torch vs đồ thị gốc (tau=0 để bỏ ngẫu nhiên)
with torch.no_grad():
    g1 = Ref(model)(spec.transpose(1, 2)).numpy()
    o1 = Doi(model)(spec, g, g, torch.tensor(0.0)).numpy()
np.save(os.path.join(RA, "_kiem_spec.npy"), spec.numpy())
np.save(os.path.join(RA, "_kiem_g.npy"), g.numpy())
np.save(os.path.join(RA, "_kiem_g1.npy"), g1)
np.save(os.path.join(RA, "_kiem_o1.npy"), o1)
print("xong")
