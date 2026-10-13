from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# OpenCV calibration target: inner corners 9x6  => 10x7 squares.
# IMPORTANT: no border frame, and a clear WHITE quiet zone around the board
# (OpenCV needs the outer squares surrounded by white to find the corners).
COLS, ROWS = 10, 7
SQ_MM = 25.0
OUT = "Kamera_kalibracio_sakktabla_9x6_25mm.pdf"

page_w, page_h = A4
board_w = COLS*SQ_MM*mm
board_h = ROWS*SQ_MM*mm
x0 = (page_w - board_w)/2
y0 = (page_h - board_h)/2 + 12*mm   # leave white all around + room for caption

c = canvas.Canvas(OUT, pagesize=A4)
c.setFillGray(0)
for r in range(ROWS):
    for col in range(COLS):
        if (r+col) % 2 == 0:
            c.rect(x0+col*SQ_MM*mm, y0+r*SQ_MM*mm, SQ_MM*mm, SQ_MM*mm, fill=1, stroke=0)
c.setFillGray(0)
c.setFont("Helvetica-Bold", 14)
c.drawCentredString(page_w/2, y0 - 14*mm, "Camera calibration checkerboard")
c.setFont("Helvetica", 10)
c.drawCentredString(page_w/2, y0 - 21*mm,
    "9 x 6 inner corners (10x7 squares)  |  square edge = 25 mm  |  after printing, MEASURE the actual size")
c.drawCentredString(page_w/2, y0 - 27*mm,
    "Print at 100% / actual size (NOT fit to page). Glue onto a flat, rigid board. Do not cut off the white border.")
c.showPage(); c.save()
print("wrote", OUT)
