"""Script tạo logo và icon chuyên nghiệp cho Doc Voice (Đọc Voice)."""
import os
import math
from PIL import Image, ImageDraw, ImageFilter

def tao_logo_doc_voice(kich_thuoc=512):
    # Tạo ảnh RGBA siêu mẫu để vẽ nét mịn (supersampling 2x)
    ss = 2
    size = kich_thuoc * ss
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))

    # 1. Vẽ nền Bo tròn Squircle với viền phát quang
    pad = int(24 * ss)
    r = int(108 * ss)
    box = [pad, pad, size - pad, size - pad]

    # Tạo gradient nền: Deep Indigo sang Cyber Violet (#111827 -> #1E1B4B -> #312E81)
    nen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    nen_draw = ImageDraw.Draw(nen)
    for y in range(pad, size - pad):
        ty = (y - pad) / (size - 2 * pad)
        r_c = int(17 * (1 - ty) + 30 * ty)
        g_c = int(24 * (1 - ty) + 27 * ty)
        b_c = int(39 * (1 - ty) + 75 * ty)
        nen_draw.line([(pad, y), (size - pad, y)], fill=(r_c, g_c, b_c, 255))

    # Mask bo góc tròn
    mask_nen = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask_nen).rounded_rectangle(box, radius=r, fill=255)
    img.paste(nen, (0, 0), mask_nen)

    # Viền phát quang tinh tế
    vien = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(vien).rounded_rectangle(box, radius=r, outline=(99, 102, 241, 140), width=int(3 * ss))
    img.alpha_composite(vien)

    # Ánh sáng tỏa tròn tâm (Radial Glow) màu tím ngọc
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    cx, cy = size // 2, size // 2
    glow_r = int(160 * ss)
    for gr in range(glow_r, 0, -2):
        alpha = int(45 * (1 - gr / glow_r) ** 1.5)
        glow_draw.ellipse([cx - gr, cy - gr, cx + gr, cy + gr], fill=(99, 102, 241, alpha))
    img.alpha_composite(glow)

    # 2. Vẽ các cột sóng âm thanh AI (Sound Spectrum Waves)
    # Tọa độ đối xứng qua trục giữa
    bars = [
        {"dx": -130, "h": 70, "color": (56, 189, 248), "w": 14},   # Sky Blue
        {"dx": -95,  "h": 125, "color": (96, 165, 250), "w": 16},  # Light Blue
        {"dx": -60,  "h": 180, "color": (129, 140, 248), "w": 18}, # Indigo Light
        {"dx": -25,  "h": 220, "color": (168, 85, 247), "w": 20},  # Purple
        {"dx": 0,    "h": 245, "color": (236, 72, 153), "w": 22},  # Pink Gradient center
        {"dx": 25,   "h": 220, "color": (168, 85, 247), "w": 20},
        {"dx": 60,   "h": 180, "color": (129, 140, 248), "w": 18},
        {"dx": 95,   "h": 125, "color": (96, 165, 250), "w": 16},
        {"dx": 130,  "h": 70,  "color": (56, 189, 248), "w": 14},
    ]

    song_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    song_draw = ImageDraw.Draw(song_layer)

    for b in bars:
        bx = cx + int(b["dx"] * ss)
        bw = int(b["w"] * ss) // 2
        bh = int(b["h"] * ss) // 2
        bradius = bw

        # Vẽ dải sóng bo tròn 2 đầu
        r_b, g_b, b_b = b["color"]
        # Gradient dọc nhẹ cho mỗi thanh
        rect = [bx - bw, cy - bh, bx + bw, cy + bh]
        song_draw.rounded_rectangle(rect, radius=bradius, fill=(r_b, g_b, b_b, 235))

    # Đổ bóng nhẹ cho các thanh sóng
    glow_song = song_layer.filter(ImageFilter.GaussianBlur(int(8 * ss)))
    img.alpha_composite(glow_song)
    img.alpha_composite(song_layer)

    # 3. Biểu tượng Microphone / Voice Studio ở trung tâm
    # Vẽ đầu mic dạng capsule bo tròn
    mic_w = int(32 * ss)
    mic_top = cy - int(90 * ss)
    mic_bot = cy + int(10 * ss)
    mic_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mic_draw = ImageDraw.Draw(mic_layer)

    # Thân mic capsule
    mic_rect = [cx - mic_w, mic_top, cx + mic_w, mic_bot]
    mic_draw.rounded_rectangle(mic_rect, radius=mic_w, fill=(255, 255, 255, 245),
                               outline=(224, 231, 255, 255), width=int(3 * ss))

    # Lưới mic (grille line ngang)
    for gy in range(mic_top + int(16 * ss), mic_bot - int(12 * ss), int(12 * ss)):
        # Chiều rộng tại độ cao gy
        gw = int(math.sqrt(max(0, mic_w**2 - (gy - (mic_top + mic_w))**2))) if gy < mic_top + mic_w else mic_w - int(4 * ss)
        gw = max(gw, int(mic_w * 0.7))
        mic_draw.line([(cx - gw + int(4*ss), gy), (cx + gw - int(4*ss), gy)],
                      fill=(147, 197, 253, 200), width=int(2 * ss))

    # Cung đỡ microphone hình chữ U
    u_pad = int(14 * ss)
    u_box = [cx - mic_w - u_pad, cy - int(40 * ss), cx + mic_w + u_pad, mic_bot + u_pad]
    mic_draw.arc(u_box, start=0, end=180, fill=(255, 255, 255, 240), width=int(5 * ss))

    # Chân đế và trục mic
    truc_top = mic_bot + u_pad
    truc_bot = truc_top + int(42 * ss)
    mic_draw.line([(cx, truc_top), (cx, truc_bot)], fill=(255, 255, 255, 240), width=int(5 * ss))

    # Đế ngang bo tròn
    de_w = int(48 * ss)
    mic_draw.line([(cx - de_w, truc_bot), (cx + de_w, truc_bot)], fill=(255, 255, 255, 240), width=int(6 * ss))

    # Biểu tượng tia sáng AI (Sparkle / Star) ở góc trên bên phải
    star_x = cx + int(105 * ss)
    star_y = cy - int(105 * ss)
    star_r = int(22 * ss)
    star_pts = []
    for i in range(8):
        rad = (i * 45) * math.pi / 180
        dist = star_r if i % 2 == 0 else int(star_r * 0.35)
        star_pts.append((star_x + dist * math.cos(rad), star_y + dist * math.sin(rad)))
    mic_draw.polygon(star_pts, fill=(253, 224, 71, 245))  # Ánh sao vàng AI

    # Gộp mic layer
    img.alpha_composite(mic_layer)

    # Thu nhỏ về kích thước chuẩn với bộ lọc Lanczos chất lượng cao nhất
    ket_qua = img.resize((kich_thuoc, kich_thuoc), Image.Resampling.LANCZOS)
    return ket_qua

def tao_cac_file_logo():
    thu_muc = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    os.makedirs(thu_muc, exist_ok=True)

    # 1. Logo chính 512x512
    logo_512 = tao_logo_doc_voice(512)
    p_512 = os.path.join(thu_muc, "logo.png")
    logo_512.save(p_512, "PNG")
    print(f"Đã tạo: {p_512}")

    # 2. Logo các kích cỡ 256, 128, 64, 48, 32, 16
    kich_co = [256, 128, 64, 48, 32, 16]
    anh_list = []
    for kc in kich_co:
        anh = logo_512.resize((kc, kc), Image.Resampling.LANCZOS)
        anh.save(os.path.join(thu_muc, f"logo_{kc}.png"), "PNG")
        anh_list.append(anh)

    # 3. File icon .ico đa kích thước cho Windows
    p_ico = os.path.join(thu_muc, "icon.ico")
    logo_512.save(p_ico, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(f"Đã tạo: {p_ico}")

if __name__ == "__main__":
    tao_cac_file_logo()
