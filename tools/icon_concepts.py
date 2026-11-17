"""Four app-icon concepts for Dualyrics, drawn with Pillow at 4x and downsampled. Writes docs/icon-concepts/*.png + sheet.png"""
import os, math
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docs', 'icon-concepts')
os.makedirs(OUT, exist_ok=True)
S = 2048                      # supersampled canvas
R = int(S * 0.225)            # squircle corner radius


def grad(size, c1, c2, angle=135):
    w, h = size
    g = Image.new('RGB', size)
    px = g.load()
    a = math.radians(angle)
    dx, dy = math.cos(a), math.sin(a)
    for y in range(h):
        for x in range(w):
            t = ((x / w - .5) * dx + (y / h - .5) * dy) / 1.0 + .5
            t = max(0, min(1, t))
            px[x, y] = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
    return g


def base(c1, c2, angle=135):
    small = grad((256, 256), c1, c2, angle).resize((S, S), Image.BICUBIC)
    mask = Image.new('L', (S, S), 0)
    m = int(S * 0.04)
    ImageDraw.Draw(mask).rounded_rectangle([m, m, S - m, S - m], R, fill=255)
    img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
    img.paste(small, (0, 0), mask)
    return img, mask


def shadow(img, shape_fn, off=(0, 28), blur=40, alpha=110):
    sh = Image.new('RGBA', img.size, (0, 0, 0, 0))
    shape_fn(ImageDraw.Draw(sh), (0, 0, 0, alpha), off)
    sh = sh.filter(ImageFilter.GaussianBlur(blur))
    img.alpha_composite(sh)


def font(names, size):
    for n in names:
        for p in (os.path.join(r'C:\Windows\Fonts', n), os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Windows', 'Fonts', n)):
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


BOLD = ['segoeuib.ttf', 'arialbd.ttf']
FA = ['Vazirmatn-Bold.ttf', 'Vazirmatn[wght].ttf', 'tahomabd.ttf', 'segoeuib.ttf']


# A â€” two overlapping speech bubbles: original (white, "A") + translation (amber, "Ø¹")
def concept_a():
    img, _ = base((17, 24, 39), (49, 46, 129), 140)
    d = ImageDraw.Draw(img)
    def back(dr, col, o=(0, 0)):
        x, y = 820 + o[0], 760 + o[1]
        dr.rounded_rectangle([x, y, x + 900, y + 700], 210, fill=col)
        dr.polygon([(x + 640, y + 680), (x + 820, y + 900), (x + 520, y + 690)], fill=col)
    def front(dr, col, o=(0, 0)):
        x, y = 330 + o[0], 470 + o[1]
        dr.rounded_rectangle([x, y, x + 900, y + 700], 210, fill=col)
        dr.polygon([(x + 230, y + 680), (x + 90, y + 900), (x + 380, y + 690)], fill=col)
    shadow(img, back); back(d, (251, 191, 36))
    shadow(img, front); front(d, (255, 255, 255))
    d.text((780, 820), 'A', font=font(BOLD, 520), fill=(30, 27, 75), anchor='mm')
    d.text((1300, 1160), 'Ø¹', font=font(FA, 470), fill=(120, 53, 15), anchor='mm')
    return img


# B â€” sound wave on the left flowing into two subtitle lines (cyan original, coral translation)
def concept_b():
    img, _ = base((8, 47, 73), (15, 23, 42), 160)
    d = ImageDraw.Draw(img)
    cx, cy = 560, 1024
    heights = [260, 520, 820, 520, 300]
    def wave(dr, col, o=(0, 0)):
        for i, hgt in enumerate(heights):
            x = 360 + i * 120 + o[0]
            dr.rounded_rectangle([x, cy - hgt // 2 + o[1], x + 70, cy + hgt // 2 + o[1]], 35, fill=col)
    def lines(dr, col1, col2, o=(0, 0)):
        dr.rounded_rectangle([1040 + o[0], 800 + o[1], 1700 + o[0], 940 + o[1]], 70, fill=col1)
        dr.rounded_rectangle([1040 + o[0], 1110 + o[1], 1560 + o[0], 1250 + o[1]], 70, fill=col2)
    shadow(img, lambda dr, c, o: (wave(dr, c, o), lines(dr, c, c, o)))
    wave(d, (34, 211, 238)); lines(d, (255, 255, 255), (251, 113, 133))
    for k in range(3):                                  # flow dots between wave and text
        x = 950 + 0 * k
    d.ellipse([930, 990, 990, 1050], fill=(34, 211, 238))
    return img


# C â€” monogram "D" built from two stacked rounded bars (white over pink) on violet
def concept_c():
    img, _ = base((76, 29, 149), (190, 24, 93), 135)
    d = ImageDraw.Draw(img)
    def dshape(dr, col, o=(0, 0)):
        x0, y0, x1, y1 = 520 + o[0], 470 + o[1], 1560 + o[0], 1580 + o[1]
        dr.rounded_rectangle([x0, y0, x0 + 230, y1], 115, fill=col)
        dr.pieslice([x1 - 1100 + 40, y0, x1, y1], -90, 90, fill=col)
        dr.rectangle([x0 + 115, y0, x1 - 500, y0 + 230], fill=col)
        dr.rectangle([x0 + 115, y1 - 230, x1 - 500, y1], fill=col)
    shadow(img, dshape); dshape(d, (255, 255, 255))
    # hollow + two bars inside the D (original + translation)
    d.pieslice([520 + 230, 470 + 230, 1560 - 230, 1580 - 230], -90, 90, fill=(140, 28, 120))
    d.rectangle([750, 700, 1060, 1350], fill=(140, 28, 120))
    d.rounded_rectangle([830, 870, 1290, 990], 60, fill=(255, 255, 255))
    d.rounded_rectangle([830, 1080, 1180, 1200], 60, fill=(251, 191, 36))
    return img


# D â€” cinema / game screen with two subtitle bars and a small note badge
def concept_d():
    img, _ = base((2, 6, 23), (30, 41, 59), 150)
    d = ImageDraw.Draw(img)
    def screen(dr, col, o=(0, 0)):
        dr.rounded_rectangle([330 + o[0], 470 + o[1], 1718 + o[0], 1470 + o[1]], 150, fill=col)
    shadow(img, screen); screen(d, (30, 58, 138))
    d.rounded_rectangle([370, 510, 1678, 1430], 120, fill=(37, 99, 235))
    d.polygon([(900, 760), (900, 1080), (1170, 920)], fill=(255, 255, 255, 235))
    d.rounded_rectangle([560, 1180, 1490, 1270], 45, fill=(255, 255, 255))
    d.rounded_rectangle([700, 1320, 1350, 1400], 40, fill=(250, 204, 21))
    # note badge
    shadow(img, lambda dr, c, o: dr.ellipse([1390 + o[0], 300 + o[1], 1840 + o[0], 750 + o[1]], fill=c))
    d.ellipse([1390, 300, 1840, 750], fill=(244, 63, 94))
    d.text((1615, 525), 'â™ª', font=font(['seguisym.ttf', 'segoeuib.ttf'], 330), fill=(255, 255, 255), anchor='mm')
    return img


concepts = {'A-bubbles': concept_a(), 'B-wave': concept_b(), 'C-monogram': concept_c(), 'D-screen': concept_d()}
sheet = Image.new('RGB', (4 * 560 + 40, 760), (24, 24, 27))
sd = ImageDraw.Draw(sheet)
lab = font(BOLD, 34)
for i, (name, im) in enumerate(concepts.items()):
    big = im.resize((512, 512), Image.LANCZOS)
    big.save(os.path.join(OUT, name + '.png'))
    x = 40 + i * 560
    sheet.paste(big, (x, 40), big)
    sd.text((x + 256, 590), name.split('-')[0], font=lab, fill=(255, 255, 255), anchor='mm')
    for j, s in enumerate((64, 32, 16)):                      # small-size legibility check
        sm = im.resize((s, s), Image.LANCZOS)
        sheet.paste(sm, (x + 130 + j * 110 + (64 - s) // 2, 640 + (64 - s) // 2), sm)
sheet.save(os.path.join(OUT, 'sheet.png'))
print('ok', OUT)

