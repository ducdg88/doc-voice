"""Máy đọc riêng của Đọc Voice cho giọng clone.

Chạy bằng python của venv VoxCPM (không phải python của app). Nạp VoxCPM2-fp16 MỘT lần, mã hoá
mẫu giọng (ref) MỘT lần cho cả bài (build_prompt_cache), rồi nằm chờ lệnh qua stdin/stdout, mỗi
dòng một JSON. Không mở cổng mạng, không đụng voice_daemon.py dùng chung của DG Voice Studio.
Tiến trình cha (app) chết thì stdin đóng, máy đọc tự thoát và nhả VRAM; rảnh quá 10 phút cũng thoát.

Vì sao nhanh hơn daemon cũ (RTF khoảng 13):
- Daemon gọi VoxCPM.generate(reference_wav_path=...) nên mỗi câu lại mã hoá ref.wav qua VAE.
- Mỗi bước sinh (một patch, 1/6,25 giây tiếng) là hàng nghìn kernel nhỏ: DiT chạy (timesteps-1) lần,
  LM chính 28 lớp, LM phụ 8 lớp, bộ mã hoá 12 lớp. Chạy eager thì CPU phát kernel không kịp (GPU chỉ
  khoảng 30%), máy bận việc khác càng chậm. Venv không có triton nên optimize()/torch.compile tắt.
- Ở đây cả một bước được chụp thành MỘT CUDA graph (torch.cuda.CUDAGraph, không cần triton): mỗi
  bước chỉ còn vài lệnh phát. Phần chuẩn bị chữ, thử lại khi badcase và giải mã VAE vẫn là mã của
  thư viện (chỉ thay vòng _inference), nên đầu ra cùng phân phối với đường cũ.
- Bản ghi KV của LM được cắt còn kv_toi_da vị trí (mặc định 1.024) thay vì 8.192, nên attention mỗi
  bước nhẹ đi 8 lần; câu dài hơn khung thì tự dùng khung 8.192.
- API cố định lô 1 (setup_cache(1, ...)), nên không batch nhiều câu một lượt được.

Lệnh:
  {"lenh": "ping"}
  {"lenh": "sinh", "text", "ref", "out", "cfg", "timesteps", "seed"?, "che_do"?}
      che_do: "do_thi" (mặc định, CUDA graph), "eager" (cùng vòng, không graph), "goc" (vòng của thư viện),
              "cu" (y như daemon dùng chung: generate(reference_wav_path) mã hoá lại ref mỗi câu, chỉ để đo)
      -> {"ok", "out", "giay_sinh", "giay_am", "buoc", "rtf", "che_do", "thu_lai"}
  {"lenh": "sinh_lo", "cac_cau": [{"id", "text", "out", "cfg"?}...], "ref", "timesteps", ...}
      -> mỗi câu xong trả ngay một dòng {"id", ...}, cuối cùng {"xong_lo": true}
  {"lenh": "kiem_do_thi", "text", "ref", "timesteps", "cfg", "seed"} -> so feat graph với eager
  {"lenh": "thoat"}
Dòng đầu tiên in ra khi nạp xong: {"san_sang": true, ...} hoặc {"san_sang": false, "loi": ...}.
"""
import json
import os
import queue
import sys
import threading
import time

os.environ.setdefault("TQDM_DISABLE", "1")          # thanh tiến độ từng bước làm chậm và rác log
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

# Kênh trả lời là stdout gốc; mọi print của thư viện dồn sang stderr (file log) để không lẫn.
_KENH = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
os.dup2(2, 1)
sys.stdout = sys.stderr

RANH_TOI_DA = float(os.environ.get("DOCVOICE_RANH_GIAY") or 600)   # rảnh quá thì thoát, nhả VRAM
KV_TOI_DA = int(os.environ.get("DOCVOICE_KV") or 1024)
SO_DO_THI_GIU = 6                                                   # số graph (timesteps, cfg, kv) giữ sẵn


def tra(obj):
    _KENH.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _KENH.flush()


def nhat_ky(chu):
    print(f"[may-doc-clone {time.strftime('%H:%M:%S')}] {chu}", file=sys.stderr, flush=True)


def uu_tien_cao():
    """ABOVE_NORMAL cho riêng tiến trình này: CPU bận việc khác vẫn phát kịp lệnh cho GPU."""
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetPriorityClass(k32.GetCurrentProcess(), 0x8000)
        except Exception:
            pass


class BuocDoThi:
    """Một bước sinh patch: DiT (vòng Euler có CFG) -> stop -> bộ mã hoá patch -> LM chính -> LM phụ.
    Đầu vào, đầu ra là bộ đệm tĩnh; dung_do_thi=True thì cả bước chụp thành một CUDA graph."""

    def __init__(self, m, timesteps, cfg, kv, dung_do_thi=True):
        import torch
        self.torch = torch
        self.m, self.cfg, self.kv = m, float(cfg), int(kv)
        tham = next(m.base_lm.parameters())
        dev, dt = tham.device, tham.dtype
        H = m.config.lm_config.hidden_size
        P, D = m.patch_size, m.audio_vae.latent_dim
        self.lm = torch.zeros(1, H, device=dev, dtype=dt)
        self.res = torch.zeros(1, H, device=dev, dtype=dt)
        self.cond = torch.zeros(1, P, D, device=dev, dtype=dt)
        self.z = torch.zeros(1, m.feat_decoder.in_channels, P, device=dev, dtype=dt)
        self.pos_b = torch.zeros(1, device=dev, dtype=torch.long)
        self.pos_r = torch.zeros(1, device=dev, dtype=torch.long)
        self.pred_out = torch.zeros(1, P, D, device=dev, dtype=dt)
        self.stop_out = torch.zeros(1, device=dev, dtype=torch.long)
        t_span = torch.linspace(1, 0, int(timesteps) + 1, device=dev, dtype=dt)
        self.t_span = t_span + 1.0 * (torch.cos(torch.pi / 2 * t_span) - 1 + t_span)   # sway 1.0 như thư viện
        self.do_thi = None
        if dung_do_thi:
            self._chup()

    def _kv_cat(self):
        """Trong lúc chạy bước: LM chỉ nhìn kv vị trí đầu của bộ nhớ KV (ghi vẫn vào đúng chỗ)."""
        kv = self.kv
        ds = []
        for lm in (self.m.base_lm, self.m.residual_lm):
            c = lm.kv_cache
            if kv < c.max_length:
                ds.append((c, c.get_layer_cache))
                c.get_layer_cache = (lambda c: lambda i: (c.kv_cache[0, i][:, :, :kv], c.kv_cache[1, i][:, :, :kv]))(c)
        return ds

    def _than(self):
        m = self.m
        torch = self.torch
        cu = self._kv_cat()
        try:
            dit = torch.cat((m.lm_to_dit_proj(self.lm), m.res_to_dit_proj(self.res)), dim=-1)
            x = m.feat_decoder.solve_euler(x=self.z, t_span=self.t_span, mu=dit,
                                           cond=self.cond.transpose(1, 2).contiguous(),
                                           cfg_value=self.cfg, use_cfg_zero_star=True)
            pred = x.transpose(1, 2)                                                   # b, p, d
            stop = m.stop_head(m.stop_actn(m.stop_proj(self.lm))).argmax(dim=-1)
            emb = m.enc_to_lm_proj(m.feat_encoder(pred.unsqueeze(1)))[:, 0, :]
            lm = m.fsq_layer(m.base_lm.forward_step(emb, self.pos_b))
            res = m.residual_lm.forward_step(m.fusion_concat_proj(torch.cat((lm, emb), dim=-1)), self.pos_r)
            self.pred_out.copy_(pred)
            self.stop_out.copy_(stop)
            self.cond.copy_(pred)
            self.lm.copy_(lm)
            self.res.copy_(res)
        finally:
            for c, ham in cu:
                c.get_layer_cache = ham

    def _chup(self):
        torch = self.torch
        # chạy nháp ở vị trí cuối khung KV (bị mask, mỗi câu fill_caches xoá lại) rồi mới chụp
        self.pos_b.fill_(self.kv - 1)
        self.pos_r.fill_(self.kv - 1)
        s = torch.cuda.Stream()
        s.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(s):
            for _ in range(2):
                self._than()
        torch.cuda.current_stream().wait_stream(s)
        torch.cuda.synchronize()
        g = torch.cuda.CUDAGraph()
        with torch.cuda.graph(g):
            self._than()
        torch.cuda.synchronize()
        self.do_thi = g

    def nap(self, lm, res, cond):
        self.lm.copy_(lm)
        self.res.copy_(res)
        self.cond.copy_(cond)

    def chay(self, pb, pr):
        self.pos_b.fill_(pb)
        self.pos_r.fill_(pr)
        self.z.normal_()
        if self.do_thi is not None:
            self.do_thi.replay()
        else:
            self._than()
        return self.pred_out.clone()


class MayDoc:
    def __init__(self, tts):
        import torch
        self.torch = torch
        self.tts = tts
        self.goc_inference = tts._inference          # vòng gốc của thư viện (bound method)
        self.che_do = "do_thi"
        self.do_thi = {}                             # (timesteps, cfg, kv) -> BuocDoThi
        self.thong_ke = {}
        tts._inference = self._inference             # _generate_with_prompt_cache gọi self._inference

    def lay_buoc(self, timesteps, cfg, kv, dung_do_thi):
        khoa = (int(timesteps), round(float(cfg), 3), int(kv), bool(dung_do_thi))
        if khoa not in self.do_thi:
            t = time.time()
            if len(self.do_thi) >= SO_DO_THI_GIU:
                self.do_thi.pop(next(iter(self.do_thi)))
            self.do_thi[khoa] = BuocDoThi(self.tts, timesteps, cfg, kv, dung_do_thi)
            nhat_ky(f"buoc {khoa} san sang {time.time() - t:.2f}s")
        return self.do_thi[khoa]

    def _inference(self, text, text_mask, feat, feat_mask, min_len=2, max_len=2000, inference_timesteps=10,
                   cfg_value=2.0, streaming=False, streaming_prefix_len=4):
        torch = self.torch
        m = self.tts
        if streaming or self.che_do == "goc" or int(feat_mask[0, -1].item()) == 1:
            yield from self.goc_inference(text, text_mask, feat, feat_mask, min_len=min_len, max_len=max_len,
                                          inference_timesteps=inference_timesteps, cfg_value=cfg_value,
                                          streaming=streaming, streaming_prefix_len=streaming_prefix_len)
            return
        from einops import rearrange
        with torch.inference_mode():
            B = feat.shape[0]
            # --- nạp ngữ cảnh: y như _inference của VoxCPM2 (chế độ reference, không có âm nối tiếp)
            prefill_encoder = getattr(m, "_feat_encoder_raw", m.feat_encoder)
            feat_embed = m.enc_to_lm_proj(prefill_encoder(feat))
            scale_emb = m.config.lm_config.scale_emb if m.config.lm_config.use_mup else 1.0
            text_embed = m.base_lm.embed_tokens(text) * scale_emb
            combined_embed = text_mask.unsqueeze(-1) * text_embed + feat_mask.unsqueeze(-1) * feat_embed
            prefix_feat_cond = feat[:, -1, ...]
            enc_outputs, kv_cache_tuple = m.base_lm(inputs_embeds=combined_embed, is_causal=True)
            m.base_lm.kv_cache.fill_caches(kv_cache_tuple)
            enc_outputs = m.fsq_layer(enc_outputs) * feat_mask.unsqueeze(-1) + enc_outputs * text_mask.unsqueeze(-1)
            lm_hidden = enc_outputs[:, -1, :]
            residual_enc_inputs = m.fusion_concat_proj(
                torch.cat((enc_outputs, feat_mask.unsqueeze(-1) * feat_embed), dim=-1))
            residual_enc_outputs, residual_kv_cache_tuple = m.residual_lm(inputs_embeds=residual_enc_inputs,
                                                                          is_causal=True)
            m.residual_lm.kv_cache.fill_caches(residual_kv_cache_tuple)
            residual_hidden = residual_enc_outputs[:, -1, :]

            # --- vòng sinh: mỗi bước một lần replay graph; cờ dừng đọc trễ một bước để GPU không chờ CPU
            dau = m.base_lm.kv_cache.current_length
            kv = KV_TOI_DA if dau + max_len + 2 <= KV_TOI_DA else m.base_lm.kv_cache.max_length
            buoc = self.lay_buoc(inference_timesteps, cfg_value, kv, self.che_do == "do_thi")
            buoc.nap(lm_hidden, residual_hidden, prefix_feat_cond)
            ghim = [torch.zeros(1, dtype=torch.long).pin_memory() for _ in range(2)]
            seq, cho, dung_o = [], None, None
            for i in range(max_len):
                pb = m.base_lm.kv_cache.step()
                pr = m.residual_lm.kv_cache.step()
                seq.append(buoc.chay(pb, pr).unsqueeze(1))
                h = ghim[i % 2]
                h.copy_(buoc.stop_out, non_blocking=True)
                ev = torch.cuda.Event()
                ev.record()
                if cho is not None:
                    j, hj, ej = cho
                    ej.synchronize()
                    if j > min_len and int(hj.item()) == 1:
                        dung_o = j
                        break
                cho = (i, h, ev)
            if dung_o is None and cho is not None:
                j, hj, ej = cho
                ej.synchronize()
                if j > min_len and int(hj.item()) == 1:
                    dung_o = j
            if dung_o is not None:
                seq = seq[:dung_o + 1]
            self.thong_ke["buoc"] = len(seq)
            pred_feat_seq = torch.cat(seq, dim=1)
            feat_pred = rearrange(pred_feat_seq, "b t p d -> b d (t p)", b=B, p=m.patch_size)
            generated_feat = pred_feat_seq.squeeze(0).cpu()
        yield feat_pred, generated_feat, 0


def main():
    goc_model = sys.argv[1] if len(sys.argv) > 1 else ""
    thiet_bi = os.environ.get("DOCVOICE_DEVICE") or "cuda"
    uu_tien_cao()
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from voxcpm import VoxCPM
        t0 = time.time()
        vox = VoxCPM.from_pretrained(goc_model, load_denoiser=False, optimize=False, device=thiet_bi,
                                     local_files_only=True)
        tts = vox.tts_model
        may = MayDoc(tts)
        tham_so = next(tts.base_lm.parameters())
        thong_tin = {"device": str(tham_so.device), "dtype": str(tham_so.dtype).replace("torch.", ""),
                     "sample_rate": int(tts.sample_rate), "giay_nap": round(time.time() - t0, 1),
                     "torch": torch.__version__, "pid": os.getpid(), "kv_toi_da": KV_TOI_DA,
                     "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else ""}
        if os.environ.get("DOCVOICE_GRAPH") == "0" or not torch.cuda.is_available():
            may.che_do = "eager"
        nhat_ky(f"nap xong {thong_tin}")
    except Exception as e:  # thiếu venv, thiếu model, hết VRAM
        tra({"san_sang": False, "loi": f"{type(e).__name__}: {e}"})
        return 1
    tra({"san_sang": True, **thong_tin})

    bo_nho_ref = {}   # (đường ref, mtime) -> prompt cache: mã hoá mẫu giọng một lần cho cả bài

    def lay_cache(ref):
        khoa = (os.path.abspath(ref), os.path.getmtime(ref))
        if khoa not in bo_nho_ref:
            t = time.time()
            bo_nho_ref.clear()
            bo_nho_ref[khoa] = tts.build_prompt_cache(reference_wav_path=ref)
            nhat_ky(f"cache ref {ref} {time.time() - t:.2f}s")
        return bo_nho_ref[khoa]

    def sinh_mot(yc, ref, timesteps, che_do):
        """Một câu: y như daemon (max_len theo số token, thử lại tối đa 3 lần khi badcase)."""
        chu = " ".join(str(yc["text"]).split())
        ra = str(yc["out"])
        cfg = float(yc.get("cfg") or 2.0)
        try:
            so_token = len(tts.text_tokenizer(chu))
        except Exception:
            so_token = max(1, len(chu) // 3)
        max_len = max(8, min(int(so_token * 6.0 + 10), 512))
        if yc.get("seed") is not None:
            torch.manual_seed(int(yc["seed"]))
        if che_do == "cu":
            # đường cũ của daemon dùng chung (để đo trước/sau): mỗi câu mã hoá lại ref.wav, vòng thư viện
            cu = may.che_do
            may.che_do = "goc"
            try:
                t = time.time()
                wav = vox.generate(chu, reference_wav_path=ref, cfg_value=cfg, inference_timesteps=int(timesteps),
                                   max_len=max_len, normalize=False, denoise=False, retry_badcase=True)
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                giay_sinh = time.time() - t
            finally:
                may.che_do = cu
            am = np.asarray(wav, dtype=np.float32).reshape(-1)
            os.makedirs(os.path.dirname(os.path.abspath(ra)) or ".", exist_ok=True)
            sf.write(ra, am, int(tts.sample_rate))
            giay_am = len(am) / float(tts.sample_rate)
            return {"ok": True, "out": ra, "giay_sinh": round(giay_sinh, 3), "giay_am": round(giay_am, 3),
                    "rtf": round(giay_sinh / max(giay_am, 1e-6), 3), "che_do": "cu", "timesteps": int(timesteps)}
        cache = lay_cache(ref)
        cu = may.che_do
        may.che_do = che_do or cu
        try:
            if may.che_do != "goc" and not torch.cuda.is_available():
                may.che_do = "eager"
            t = time.time()
            wav, _, feat = tts.generate_with_prompt_cache(
                target_text=chu, prompt_cache=cache, min_len=2, max_len=max_len,
                inference_timesteps=int(timesteps), cfg_value=cfg, retry_badcase=bool(yc.get("thu_lai", True)),
                retry_badcase_max_times=3, retry_badcase_ratio_threshold=6.0)
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            giay_sinh = time.time() - t
            dung = may.che_do
        except Exception as e:
            if may.che_do == "do_thi":     # graph lỗi thì tự quay về vòng gốc của thư viện, ghi log
                nhat_ky(f"do thi loi, quay ve goc: {type(e).__name__}: {e}")
                may.che_do = "goc"
                t = time.time()
                wav, _, feat = tts.generate_with_prompt_cache(
                    target_text=chu, prompt_cache=cache, min_len=2, max_len=max_len,
                    inference_timesteps=int(timesteps), cfg_value=cfg, retry_badcase=True,
                    retry_badcase_max_times=3, retry_badcase_ratio_threshold=6.0)
                giay_sinh = time.time() - t
                dung = "goc (do thi loi)"
            else:
                raise
        finally:
            may.che_do = cu
        am = np.asarray(wav.squeeze(0).float().cpu().numpy() if hasattr(wav, "cpu") else wav,
                        dtype=np.float32).reshape(-1)
        os.makedirs(os.path.dirname(os.path.abspath(ra)) or ".", exist_ok=True)
        sf.write(ra, am, int(tts.sample_rate))
        giay_am = len(am) / float(tts.sample_rate)
        buoc = int(feat.shape[0])
        return {"ok": True, "out": ra, "giay_sinh": round(giay_sinh, 3), "giay_am": round(giay_am, 3),
                "buoc": buoc, "buoc_moi_giay": round(buoc / max(giay_sinh, 1e-6), 2),
                "rtf": round(giay_sinh / max(giay_am, 1e-6), 3), "che_do": dung, "timesteps": int(timesteps)}

    def kiem_do_thi(yc):
        """Cùng seed: feat của graph phải trùng feat của vòng eager cùng mã (sai số do thứ tự kernel)."""
        ref = str(yc["ref"])
        cache = lay_cache(ref)
        chu = " ".join(str(yc["text"]).split())
        kq = {}
        feats = {}
        for che_do in ("eager", "do_thi"):
            may.che_do = che_do
            torch.manual_seed(int(yc.get("seed") or 1234))
            t = time.time()
            _, _, feat = tts.generate_with_prompt_cache(
                target_text=chu, prompt_cache=cache, min_len=2, max_len=200,
                inference_timesteps=int(yc.get("timesteps") or 16), cfg_value=float(yc.get("cfg") or 2.0),
                retry_badcase=False)
            torch.cuda.synchronize()
            kq[f"giay_{che_do}"] = round(time.time() - t, 3)
            feats[che_do] = feat.float()
        may.che_do = "do_thi"
        a, b = feats["eager"], feats["do_thi"]
        n = min(a.shape[0], b.shape[0])
        kq.update({"buoc_eager": int(a.shape[0]), "buoc_do_thi": int(b.shape[0]),
                   "sai_tb": round(float((a[:n] - b[:n]).abs().mean()), 6),
                   "sai_max": round(float((a[:n] - b[:n]).abs().max()), 6)})
        return kq

    hang = queue.Queue()

    def doc_lenh():
        for dong in sys.stdin:
            hang.put(dong)
        hang.put(None)                               # stdin đóng: app đã tắt

    threading.Thread(target=doc_lenh, daemon=True).start()
    while True:
        try:
            dong = hang.get(timeout=RANH_TOI_DA)
        except queue.Empty:
            nhat_ky(f"ranh qua {RANH_TOI_DA:.0f}s, thoat de nha VRAM")
            break
        if dong is None:
            break
        dong = dong.strip()
        if not dong:
            continue
        try:
            yc = json.loads(dong)
        except ValueError:
            tra({"ok": False, "loi": "dòng lệnh không phải JSON"})
            continue
        lenh = yc.get("lenh")
        if lenh == "thoat":
            tra({"ok": True})
            break
        try:
            if lenh == "ping":
                tra({"ok": True, "san_sang": True, **thong_tin, "che_do": may.che_do,
                     "vram_gb": round(torch.cuda.memory_allocated() / 1e9, 2) if torch.cuda.is_available() else 0})
            elif lenh == "sinh":
                tra(sinh_mot(yc, str(yc["ref"]), int(yc.get("timesteps") or 16), yc.get("che_do")))
            elif lenh == "sinh_lo":
                for cau in yc.get("cac_cau") or []:
                    try:
                        r = sinh_mot({**yc, **cau}, str(yc["ref"]), int(yc.get("timesteps") or 16), yc.get("che_do"))
                    except Exception as e:
                        nhat_ky(f"loi sinh {cau.get('id')}: {type(e).__name__}: {e}")
                        r = {"ok": False, "loi": f"{type(e).__name__}: {e}"}
                    tra({"id": cau.get("id"), **r})
                tra({"xong_lo": True})
            elif lenh == "kiem_do_thi":
                tra({"ok": True, **kiem_do_thi(yc)})
            else:
                tra({"ok": False, "loi": f"không có lệnh {lenh}"})
        except Exception as e:
            nhat_ky(f"loi {lenh}: {type(e).__name__}: {e}")
            tra({"ok": False, "loi": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
