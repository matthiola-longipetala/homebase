import moms_apriltag as apriltag
import numpy as np
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

FAMILY = "tag36h11"
IDS = list(range(10))
TAG_CM = 12.0          # black tag square edge = tag_size for pose estimation
GRID = 8               # tag36h11 raster: 6x6 data + 1-cell black border = 8x8
CELL_CM = TAG_CM / GRID
QUIET_CELLS = 2        # white quiet zone (>=1 required; 2 for safety)
UPSCALE = 60
OUT_PDF = "AprilTagok_tag36h11_12cm.pdf"

tg = apriltag.TagGenerator2(FAMILY)
c = canvas.Canvas(OUT_PDF, pagesize=A4)
page_w, page_h = A4

for tid in IDS:
    bits = tg.generate(tid)
    assert bits.shape == (GRID, GRID), bits.shape
    full = np.full((GRID + 2*QUIET_CELLS, GRID + 2*QUIET_CELLS), 255, dtype=np.uint8)
    full[QUIET_CELLS:QUIET_CELLS+GRID, QUIET_CELLS:QUIET_CELLS+GRID] = bits
    img = Image.fromarray(full, "L").resize(
        (full.shape[1]*UPSCALE, full.shape[0]*UPSCALE), Image.NEAREST)
    total_cm = (GRID + 2*QUIET_CELLS) * CELL_CM
    draw_w = total_cm * cm
    x = (page_w - draw_w)/2
    y = (page_h - draw_w)/2 + 1.0*cm
    c.drawImage(ImageReader(img), x, y, width=draw_w, height=draw_w)
    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(page_w/2, y - 1.0*cm, f"AprilTag  tag36h11   ID = {tid}")
    c.setFont("Helvetica", 10)
    c.drawCentredString(page_w/2, y - 1.7*cm,
        f"black square edge = {TAG_CM:.0f} cm  |  do NOT cut off the white border (quiet zone)")
    tag_draw = TAG_CM*cm
    tx = (page_w - tag_draw)/2
    ty = y + QUIET_CELLS*CELL_CM*cm
    c.setLineWidth(0.3); m = 0.4*cm
    for (cx, cy) in [(tx,ty),(tx+tag_draw,ty),(tx,ty+tag_draw),(tx+tag_draw,ty+tag_draw)]:
        c.line(cx-m,cy,cx+m,cy); c.line(cx,cy-m,cx,cy+m)
    c.showPage()
c.save()
print("wrote", OUT_PDF)
