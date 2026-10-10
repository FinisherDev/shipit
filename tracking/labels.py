from io import BytesIO

from django.conf import settings
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128, qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import inch
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas

#Printable 4x6 inch shipping label (PDF) with a Code 128 barcode and a QR code.

WIDTH, HEIGHT = 4 * inch, 6 * inch
MARGIN = 0.25 * inch


def _wrapped(c, text, x, y, width, font='Helvetica', size=9, leading=11, max_lines=3):
    """Draw wrapped text downwards from y; returns the y position after the last line."""
    c.setFont(font, size)
    for line in simpleSplit(text or '-', font, size, width)[:max_lines]:
        c.drawString(x, y, line)
        y -= leading
    return y


def build_label_pdf(shipment):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(WIDTH, HEIGHT))
    c.setTitle(f"Label {shipment.tracking_id}")
    inner = WIDTH - 2 * MARGIN
    tracking_url = settings.TRACKING_URL_TEMPLATE.format(tracking_id=shipment.tracking_id)

    c.rect(MARGIN / 2, MARGIN / 2, WIDTH - MARGIN, HEIGHT - MARGIN)

    # Header
    y = HEIGHT - MARGIN - 14
    c.setFont('Helvetica-Bold', 16)
    c.drawString(MARGIN, y, 'ShipIt')
    c.setFont('Helvetica-Bold', 11)
    c.drawRightString(WIDTH - MARGIN, y, shipment.get_service_type_display().upper())
    y -= 8
    c.line(MARGIN, y, WIDTH - MARGIN, y)

    # Destination (the part couriers read first)
    y -= 16
    c.setFont('Helvetica', 8)
    c.drawString(MARGIN, y, 'DELIVER TO')
    y -= 15
    c.setFont('Helvetica-Bold', 14)
    c.drawString(MARGIN, y, (shipment.receiver_name or '')[:32])
    y -= 14
    y = _wrapped(c, shipment.receiver_address, MARGIN, y, inner, size=10, leading=12)
    c.setFont('Helvetica-Bold', 12)
    c.drawString(MARGIN, y - 2, (shipment.destination or '').upper()[:34])
    y -= 14
    if shipment.receiver_phone:
        c.setFont('Helvetica', 10)
        c.drawString(MARGIN, y - 2, f"Tel: {shipment.receiver_phone}")
        y -= 12

    # Sender
    y -= 8
    c.line(MARGIN, y, WIDTH - MARGIN, y)
    y -= 13
    c.setFont('Helvetica', 8)
    c.drawString(MARGIN, y, 'FROM')
    y -= 12
    c.setFont('Helvetica-Bold', 10)
    c.drawString(MARGIN, y, f"{shipment.sender_name}, {shipment.origin}"[:48])
    y -= 12

    # Package details
    y -= 6
    c.setFont('Helvetica', 9)
    c.drawString(MARGIN, y, f"Weight: {shipment.chargeable_weight} kg")
    c.drawRightString(WIDTH - MARGIN, y, f"Paid: {'YES' if shipment.payment_status == 'paid' else 'NO'}")
    y -= 11
    if shipment.estimated_delivery:
        c.drawString(MARGIN, y, f"Est. delivery: {shipment.estimated_delivery:%d %b %Y}")
        y -= 11

    # Barcode + QR code at the bottom
    qr_size = 1.0 * inch
    widget = qr.QrCodeWidget(tracking_url)
    x1, y1, x2, y2 = widget.getBounds()
    drawing = Drawing(qr_size, qr_size, transform=[qr_size / (x2 - x1), 0, 0, qr_size / (y2 - y1), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, WIDTH - MARGIN - qr_size, MARGIN + 18)

    barcode = code128.Code128(shipment.tracking_id, barHeight=0.7 * inch, barWidth=0.0085 * inch)
    barcode.drawOn(c, MARGIN, MARGIN + 38)
    c.setFont('Helvetica-Bold', 11)
    c.drawString(MARGIN, MARGIN + 22, shipment.tracking_id)
    c.setFont('Helvetica', 7)
    c.drawString(MARGIN, MARGIN + 6, 'Scan the QR code to track this shipment')

    c.showPage()
    c.save()
    return buffer.getvalue()