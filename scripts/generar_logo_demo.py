"""Genera un logo PNG demo para probar la inclusion en el PDF.

En produccion, el cliente sube su propio logo. Esto es solo para validar
que la cadena PDF + logo funciona.
"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parent.parent
out_dir = root / "data"
out_dir.mkdir(exist_ok=True)
out = out_dir / "logo_demo.png"

# Tamano apropiado para PDF: 400x200 px (200 DPI, ~5cm x 2.5cm en A4)
W, H = 400, 200
BLAU = (0, 77, 152)        # #004D98 (azul Barca)
GRANA = (165, 0, 68)       # #A50044 (granate Barca)

img = Image.new("RGBA", (W, H), (255, 255, 255, 255))
draw = ImageDraw.Draw(img)

# Banda diagonal blaugrana (referencia escudo)
draw.polygon([(0, 0), (W, 0), (W, H // 2), (0, H // 2)], fill=BLAU)
draw.polygon([(0, H // 2), (W, H // 2), (W, H), (0, H)], fill=GRANA)

# Texto
try:
    font_big = ImageFont.truetype("arialbd.ttf", 64)
    font_sml = ImageFont.truetype("arial.ttf", 22)
except Exception:
    font_big = ImageFont.load_default()
    font_sml = ImageFont.load_default()

# AFISCA en blanco al centro
texto = "AFISCA"
bbox = draw.textbbox((0, 0), texto, font=font_big)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.text(((W - tw) // 2, (H - th) // 2 - 12), texto, fill=(255, 255, 255), font=font_big)

# Subtitulo
sub = "S.A.C."
bbox = draw.textbbox((0, 0), sub, font=font_sml)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
draw.text(((W - tw) // 2, H - th - 18), sub, fill=(255, 255, 255), font=font_sml)

img.save(str(out), "PNG")
print(f"[OK] Logo generado: {out}")
print(f"     Tamano: {out.stat().st_size} bytes")
