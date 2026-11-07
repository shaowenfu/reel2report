"""Render a fake bookkeeping-app screen recording with a few planted bugs."""
import math
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 540, 1170, 30
F = "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
f_s, f_m, f_l, f_xl = (ImageFont.truetype(F, n) for n in (22, 28, 36, 56))
BLUE, GREY, BG = (37, 99, 235), (120, 120, 120), (246, 247, 249)


def status(d):
    d.rectangle([0, 0, W, 44], fill=(255, 255, 255))
    d.text((20, 10), "9:41", font=f_s, fill=(0, 0, 0))
    d.text((W - 90, 10), "5G ▮▮▮", font=f_s, fill=(0, 0, 0))


def header(d, title):
    d.rectangle([0, 44, W, 120], fill=(255, 255, 255))
    d.text((W // 2 - len(title) * 18, 62), title, font=f_l, fill=(0, 0, 0))


def button(d, box, text, fill=BLUE, color=(255, 255, 255)):
    d.rounded_rectangle(box, 14, fill=fill)
    x0, y0, x1, y1 = box
    d.text(((x0 + x1) // 2 - len(text) * 14, (y0 + y1) // 2 - 16), text, font=f_m, fill=color)


def touch(d, x, y):
    d.ellipse([x - 26, y - 26, x + 26, y + 26], outline=(255, 255, 255), width=4, fill=(150, 150, 150))


def spinner(d, t, cx=W // 2, cy=H // 2):
    for k in range(8):
        a = t * 6 + k * math.pi / 4
        c = 60 + k * 22
        d.ellipse([cx + 40 * math.cos(a) - 8, cy + 40 * math.sin(a) - 8,
                   cx + 40 * math.cos(a) + 8, cy + 40 * math.sin(a) + 8], fill=(c, c, c))


def login(d, t, phone, code, tap=None):
    header(d, "登录")
    d.text((60, 220), "欢迎使用 小账本", font=f_l, fill=(0, 0, 0))
    d.rounded_rectangle([60, 320, W - 60, 390], 10, outline=GREY, width=2, fill=(255, 255, 255))
    d.text((80, 337), phone or "请输入手机号", font=f_m, fill=(0, 0, 0) if phone else GREY)
    d.rounded_rectangle([60, 420, 300, 490], 10, outline=GREY, width=2, fill=(255, 255, 255))
    d.text((80, 437), code or "验证码", font=f_m, fill=(0, 0, 0) if code else GREY)
    button(d, (320, 420, W - 60, 490), "获取验证码", fill=(230, 236, 250), color=BLUE)
    button(d, (60, 560, W - 60, 640), "登录")


def tabbar(d, active):
    d.rectangle([0, H - 100, W, H], fill=(255, 255, 255))
    for i, name in enumerate(["首页", "统计", "设置"]):
        d.text((70 + i * 170, H - 70), name, font=f_m, fill=BLUE if name == active else GREY)


def home(d, total, offset=0):
    header(d, "首页")
    d.rounded_rectangle([30, 150 + offset, W - 30, 360 + offset], 20, fill=BLUE)
    d.text((60, 175 + offset), "本月支出", font=f_m, fill=(220, 230, 255))
    d.text((60, 225 + offset), total, font=f_xl, fill=(255, 255, 255))
    items = [("午餐", "-¥32.00"), ("地铁", "-¥4.00"), ("咖啡", "-¥18.00"), ("超市", "-¥126.40")]
    if total != "¥1,280.00":
        items = [("餐饮 · 晚饭", "-¥35.50")] + items
    for k, (n, v) in enumerate(items):
        y = 400 + k * 90 + offset
        d.rectangle([30, y, W - 30, y + 76], fill=(255, 255, 255))
        d.text((60, y + 22), n, font=f_m, fill=(0, 0, 0))
        d.text((W - 190, y + 22), v, font=f_m, fill=(0, 0, 0))
    d.ellipse([W - 130, H - 230, W - 40, H - 140], fill=BLUE)
    d.text((W - 100, H - 212), "+", font=f_xl, fill=(255, 255, 255))
    tabbar(d, "首页")


def add_page(d, amount, x_off=0):
    d.rectangle([x_off, 44, x_off + W, H], fill=BG)
    d.rectangle([x_off, 44, x_off + W, 120], fill=(255, 255, 255))
    d.text((x_off + W // 2 - 36, 62), "记一笔", font=f_l, fill=(0, 0, 0))
    for k, c in enumerate(["餐饮", "交通", "购物", "娱乐"]):
        x = x_off + 40 + k * 120
        d.rounded_rectangle([x, 160, x + 100, 220], 12, fill=BLUE if c == "餐饮" else (255, 255, 255))
        d.text((x + 22, 174), c, font=f_m, fill=(255, 255, 255) if c == "餐饮" else (0, 0, 0))
    d.text((x_off + 40, 270), "金额", font=f_m, fill=GREY)
    d.text((x_off + 40, 310), f"¥ {amount}", font=f_xl, fill=(0, 0, 0))
    d.text((x_off + 40, 420), "备注：晚饭", font=f_m, fill=(0, 0, 0))
    button(d, (x_off + 60, 900, x_off + W - 60, 980), "保存")


def settings(d, toast=False):
    header(d, "设置")
    for k, n in enumerate(["账户与安全", "主题", "数据导出", "关于"]):
        y = 160 + k * 90
        d.rectangle([30, y, W - 30, y + 76], fill=(255, 255, 255))
        d.text((60, y + 22), n, font=f_m, fill=(0, 0, 0))
        d.text((W - 70, y + 22), ">", font=f_m, fill=GREY)
    tabbar(d, "设置")
    if toast:
        d.rounded_rectangle([60, 700, W - 60, 800], 16, fill=(40, 40, 40))
        d.text((90, 733), "导出失败：未知错误(500)", font=f_m, fill=(255, 255, 255))


def frame(t):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    phone = "13800138000"
    if t < 4.6:
        typed = phone[: max(0, min(11, int((t - 0.5) * 6)))]
        code = "" if t < 3.6 else "8264"[: int((t - 3.6) * 8) + 1]
        login(d, t, typed, code)
        if 3.0 < t < 3.3: touch(d, 400, 455)
        if 4.4 < t < 4.6: touch(d, W // 2, 600)
    elif t < 5.0:          # 0.4s loading flash (short transition)
        login(d, t, phone, "8264"); d.rectangle([0, 0, W, H], fill=(0, 0, 0, 0)); spinner(d, t)
    elif t < 8.2:
        home(d, "¥1,280.00")
        if 7.9 < t: touch(d, W - 85, H - 185)
    elif t < 8.5:          # slide-in animation
        home(d, "¥1,280.00"); add_page(d, "", x_off=int(W * (1 - (t - 8.2) / 0.3)))
    elif t < 13.0:
        amt = "35.50"[: max(0, min(5, int((t - 9.5) * 3)))]
        add_page(d, amt)
        if 12.7 < t: touch(d, W // 2, 940)
    elif t < 15.5:         # slow save: 2.5s spinner
        add_page(d, "35.50"); d.rectangle([W // 2 - 90, H // 2 - 90, W // 2 + 90, H // 2 + 90], fill=(255, 255, 255))
        spinner(d, t)
    elif t < 15.7:         # white flash
        d.rectangle([0, 0, W, H], fill=(255, 255, 255))
    elif t < 19.0:         # BUG: total not refreshed after save
        home(d, "¥1,280.00")
    elif t < 20.0:         # pull to refresh
        off = int(120 * math.sin((t - 19.0) * math.pi))
        home(d, "¥1,280.00", offset=off); spinner(d, t, cy=110 + off // 3)
    elif t < 23.0:
        home(d, "¥1,315.50")
        if 22.7 < t: touch(d, 410, H - 55)
    elif t < 26.0:
        settings(d)
        if 25.7 < t: touch(d, W // 2, 378)
    elif t < 28.0:
        settings(d, toast=True)
    else:
        settings(d)
    status(d)
    return im


DUR = 30.0
out = sys.argv[1]
proc = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-pix_fmt", "yuv420p", out],
                        stdin=subprocess.PIPE)
for i in range(int(DUR * FPS)):
    proc.stdin.write(frame(i / FPS).tobytes())
proc.stdin.close()
proc.wait()
print("wrote", out)
