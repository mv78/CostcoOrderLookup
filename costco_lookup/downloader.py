"""
downloader.py — Download invoices/receipts as HTML files.
"""

import io
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from .client import GraphQLClient
from .orders import RECEIPT_DETAIL_QUERY, ORDER_DETAIL_QUERY
from .paths import BASE_DIR

log = logging.getLogger(__name__)

INVOICES_DIR = BASE_DIR / "invoices"

# ---------------------------------------------------------------------------
# Shared HTML skeleton
# ---------------------------------------------------------------------------

_CSS = """
  body { font-family: system-ui, -apple-system, sans-serif; background: #fff; color: #111;
         max-width: 900px; margin: 40px auto; padding: 0 20px; }
  h1 { font-size: 1.4rem; margin-bottom: 4px; }
  .subtitle { color: #555; font-size: 0.9rem; margin-bottom: 20px; }
  table { border-collapse: collapse; width: 100%; margin-bottom: 20px; font-size: 0.9rem; }
  th { background: #f0f0f0; text-align: left; padding: 6px 10px;
       border: 1px solid #ccc; }
  td { padding: 5px 10px; border: 1px solid #ddd; vertical-align: top; }
  tr:nth-child(even) td { background: #fafafa; }
  .section-label { font-weight: bold; margin: 16px 0 6px; font-size: 0.95rem; }
  .total-row td { font-weight: bold; background: #f0f0f0; }
  .footer { color: #999; font-size: 0.78rem; margin-top: 30px; border-top: 1px solid #eee;
            padding-top: 8px; }
  address { font-style: normal; line-height: 1.6; }
""".strip()


def _html_doc(title: str, body: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "<head>\n"
        "  <meta charset=\"UTF-8\">\n"
        f"  <title>{_esc(title)}</title>\n"
        "  <style>\n"
        f"    {_CSS}\n"
        "  </style>\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def _esc(text: Any) -> str:
    """Minimal HTML escaping."""
    s = str(text) if text is not None else ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _v(d: dict, *keys: str, default: str = "—") -> str:
    """Dig into dict with fallback keys; return string."""
    for k in keys:
        val = d.get(k)
        if val is not None and str(val).strip() not in ("", "None"):
            return str(val)
    return default


def _dig(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def download_documents(
    results: List[dict],
    client: GraphQLClient,
    item_number: str,
) -> List[Path]:
    """
    Download full detail for each result record and save as HTML.

    Returns a list of Path objects for files successfully written.
    """
    INVOICES_DIR.mkdir(parents=True, exist_ok=True)
    saved: List[Path] = []

    for record in results:
        source = record.get("source", "")
        order_id = record.get("order_id", "")
        date_str = record.get("date", "unknown")

        safe_order_id = re.sub(r"[^A-Za-z0-9]", "", order_id)
        filename = f"{item_number}_{source}_{safe_order_id}_{date_str}.html"
        out_path = INVOICES_DIR / filename

        if out_path.exists():
            log.warning("Skipping existing file: %s", out_path)
            continue

        try:
            if source == "warehouse":
                html = _fetch_and_render_warehouse(client, order_id, item_number)
            elif source == "online":
                html = _fetch_and_render_online(client, order_id, item_number)
            else:
                log.warning("Unknown source %r for order_id=%s — skipping", source, order_id)
                continue

            out_path.write_text(html, encoding="utf-8")
            log.info("Saved invoice: %s", out_path)
            saved.append(out_path)

        except Exception as exc:
            log.warning("Failed to download %s %s: %s", source, order_id, exc)
            continue

    return saved


# ---------------------------------------------------------------------------
# Fetch + render: warehouse
# ---------------------------------------------------------------------------

def _fetch_and_render_warehouse(
    client: GraphQLClient,
    barcode: str,
    item_number: str,
) -> str:
    variables = {"barcode": barcode, "documentType": "warehouse"}
    log.debug("RECEIPT_DETAIL_QUERY barcode=%s", barcode)
    data = client.execute(RECEIPT_DETAIL_QUERY, variables)

    result = _dig(data, "data", "receiptsWithCounts") or {}
    receipts = result.get("receipts") or []
    receipt_data = receipts[0] if receipts else {}
    return _generate_warehouse_html(receipt_data, item_number)


def _fetch_and_render_online(
    client: GraphQLClient,
    order_number: str,
    item_number: str,
) -> str:
    variables = {"orderNumbers": [order_number]}
    log.debug("ORDER_DETAIL_QUERY orderNumber=%s", order_number)
    data = client.execute(ORDER_DETAIL_QUERY, variables)

    orders = _dig(data, "data", "getOrderDetails") or []
    if isinstance(orders, list):
        order_data = orders[0] if orders else {}
    else:
        order_data = orders or {}
    return _generate_online_html(order_data, item_number)


# ---------------------------------------------------------------------------
# Barcode helper
# ---------------------------------------------------------------------------

def _barcode_svg(barcode_number: str) -> str:
    """Return an inline SVG Code128 barcode string, or empty string on failure."""
    try:
        import barcode as bc_mod
        from barcode.writer import SVGWriter
        bc = bc_mod.get("code128", barcode_number, writer=SVGWriter())
        buf = io.BytesIO()
        bc.write(buf, options={
            "module_height": 10,
            "text_distance": 1,
            "font_size": 0,
            "quiet_zone": 2,
            "write_text": False,
        })
        svg = buf.getvalue().decode("utf-8")
        svg = svg[svg.find("<svg"):]
        # Extract mm dimensions BEFORE stripping units (needed for viewBox)
        w_match = re.search(r'width="([\d.]+)mm"', svg)
        h_match = re.search(r'height="([\d.]+)mm"', svg)
        # Strip mm units from all numeric attributes so they become SVG user units
        # that the viewBox coordinate system can scale (mm units bypass viewBox scaling)
        svg = re.sub(r'([\d.]+)mm"', r'\1"', svg)
        if w_match and h_match:
            vb_w, vb_h = w_match.group(1), h_match.group(1)
            svg = svg.replace("<svg ", '<svg viewBox="0 0 {} {}" preserveAspectRatio="xMidYMid meet" '.format(vb_w, vb_h), 1)
        # width="100%" fills container; height > (width/aspect_ratio) ensures width is the
        # constraining dimension so the barcode spans the full container width
        svg = re.sub(r'width="[^"]*"', 'width="100%"', svg, count=1)
        svg = re.sub(r'height="[^"]*"', 'height="130"', svg, count=1)
        return svg
    except Exception as exc:
        log.warning("Barcode generation failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# HTML generators
# ---------------------------------------------------------------------------

def _generate_warehouse_html(receipt_data: dict, item_number: str) -> str:
    # --- CSS (verbatim from Costco) ---
    RECEIPT_CSS = """* { font-family: "Roboto", "Arial", sans-serif; }
body { background: #fff; margin: 0; padding: 20px; }
.wrapper {
    margin: 0 auto 1.5rem;
    width: 26rem;
    text-align: center;
    overflow: hidden;
    display: block;
}
.logo {
    height: 3.835rem;
    width: 11.664rem;
    display: inline;
    margin-bottom: 1.5rem;
}
.header {
    margin-bottom: 0.25rem;
    line-height: 1.172rem;
    font-size: 1rem;
    font-weight: bold;
}
.address {
    font-size: 0.875rem;
    line-height: 1rem;
}
.address1 {
    font-size: 0.875rem;
    line-height: 1rem;
    margin-bottom: 1.5rem;
}
.barcodeText {
    font-weight: 400;
    font-size: 0.875rem;
    line-height: 1rem;
    display: block;
    margin-bottom: 1.5rem;
}
.printReceipt {
    margin: 0 auto;
    width: 26rem;
}
.printWrapper {
    width: 100%;
    border-collapse: collapse;
}
.tableHead { margin-bottom: 0.5rem; }
.tableCell {
    text-align: left;
    font-weight: 400;
    font-size: 0.875rem;
    line-height: 1rem;
    padding: 2px 4px;
}
.tableRow { display: table; }
.footer { display: block; }
.footerTop { margin-top: 1.5rem; }
.greetingText {
    font-weight: 400;
    font-size: 1rem;
    line-height: 1.188rem;
    margin-bottom: 0.75rem;
}
.comeAgainText {
    font-weight: 400;
    font-size: 1rem;
    line-height: 1.188rem;
    margin-bottom: 1.5rem;
}
.boxWrapper {
    clear: both;
    position: relative;
    overflow: hidden;
    margin: 0 auto 1.5rem;
}
.inlineBox {
    display: inline-block;
    text-align: center;
    margin-left: 0.6665rem;
    margin-right: 0.6665rem;
}
.itemSold {
    margin-bottom: 0.5rem;
    text-align: left;
    font-size: 1.125rem;
    line-height: 1.313rem;
    font-weight: 700;
}
.boxSoldWrapper {
    clear: both;
    position: relative;
    overflow: hidden;
}
.divider {
    margin-top: 1rem;
    margin-bottom: 1rem;
    border: 1px dashed #cccccc;
    border-width: thin;
}
.date { margin-right: 8px; }
.time { margin-right: 6px; }
.recNumber { margin-right: 2px; }
.fullWidth { width: 100%; }
.pbEight { margin-bottom: 0.5rem; }
.visa { text-transform: uppercase; }
.visano { text-align: right; padding-right: 24px; }
.upperCase { text-transform: uppercase; }
td { padding: 2px 4px; font-size: 0.875rem; }"""

    # --- Parse transactionDateTime ---
    dt_raw = receipt_data.get("transactionDateTime") or ""
    dt_obj = None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            dt_obj = datetime.strptime(str(dt_raw)[:19], fmt[:len(fmt)])
            break
        except (ValueError, TypeError):
            pass
    if dt_obj is None and dt_raw:
        # last-chance: try strptime with just the first 19 chars
        try:
            dt_obj = datetime.strptime(str(dt_raw)[:19], "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            pass
    date_display = dt_obj.strftime("%m/%d/%Y") if dt_obj else str(dt_raw)[:10]
    time_display = dt_obj.strftime("%H:%M") if dt_obj else ""

    # --- Scalar fields ---
    warehouse_name = _v(receipt_data, "warehouseName")
    warehouse_number = _v(receipt_data, "warehouseNumber")
    addr1 = receipt_data.get("warehouseAddress1") or ""
    warehouse_city = receipt_data.get("warehouseCity") or ""
    warehouse_state = receipt_data.get("warehouseState") or ""
    warehouse_zip = receipt_data.get("warehousePostalCode") or ""
    barcode = _v(receipt_data, "transactionBarcode")
    membership = receipt_data.get("membershipNumber")
    register_num = _v(receipt_data, "registerNumber")
    transaction_num = _v(receipt_data, "transactionNumber")
    operator_num = _v(receipt_data, "operatorNumber")
    total_item_count = receipt_data.get("totalItemCount", "")

    def _fmt_amt(val):
        """Format numeric amount as XX.XX (no $ prefix)."""
        if val is None:
            return "—"
        try:
            return "{:.2f}".format(float(val))
        except (ValueError, TypeError):
            return str(val)

    sub_total = _fmt_amt(receipt_data.get("subTotal"))
    taxes = _fmt_amt(receipt_data.get("taxes"))
    total = _fmt_amt(receipt_data.get("total"))

    # --- Header block ---
    header_html = '<div class="wrapper">\n'
    header_html += '  <img class="logo" src="https://my.costco.com/assets/static/costco-logo-black-bb6aa5af19777f081f55ce70671cce68.svg" alt="Costco Wholesale">\n'
    header_html += '  <div class="header">{} #{}</div>\n'.format(_esc(warehouse_name), _esc(warehouse_number))
    if addr1:
        header_html += '  <div class="address">{}</div>\n'.format(_esc(addr1))
    city_state_zip = ", ".join(filter(None, [warehouse_city, warehouse_state])) + (" " + warehouse_zip if warehouse_zip else "")
    if city_state_zip.strip(", "):
        header_html += '  <div class="address1">{}</div>\n'.format(_esc(city_state_zip.strip()))
    barcode_svg = _barcode_svg(barcode)
    if barcode_svg:
        header_html += (
            '  <div class="barcodeText">'
            '{}'
            '<div style="margin-top:0.25rem">{}</div>'
            '</div>\n'
        ).format(barcode_svg, _esc(barcode))
    else:
        header_html += '  <div class="barcodeText">{}</div>\n'.format(_esc(barcode))
    header_html += '</div>\n'

    # --- Table rows ---
    rows = []

    # Member row
    if membership is not None:
        rows.append(
            '<tr class="tableRow"><th class="tableCell" colspan="4">Member {}</th></tr>'.format(_esc(str(membership)))
        )

    # Item rows
    items = receipt_data.get("itemArray") or []
    for item in items:
        identifier = _esc(str(item.get("itemIdentifier") or ""))
        item_num = _esc(str(item.get("itemNumber") or ""))
        desc01 = (item.get("itemDescription01") or "").strip()
        desc02 = (item.get("itemDescription02") or "").strip()
        desc = desc01 if desc01 else desc02
        amt = _fmt_amt(item.get("amount"))
        tax_flag = _esc(str(item.get("taxFlag") or ""))
        highlight = ' style="background:#fffbcc"' if str(item.get("itemNumber", "")) == str(item_number) else ""
        rows.append(
            '<tr{}>'
            '<td class="tableCell">{}</td>'
            '<td class="tableCell" style="text-align:right">{}</td>'
            '<td class="tableCell">{}</td>'
            '<td class="tableCell" style="text-align:right">{}{}</td>'
            '</tr>'.format(highlight, identifier, item_num, _esc(desc), amt, " " + tax_flag if tax_flag else "")
        )

    # Subtotal row
    rows.append(
        '<tr>'
        '<td colspan="2"></td>'
        '<td class="tableCell">SUBTOTAL</td>'
        '<td class="tableCell" style="text-align:right">{}</td>'
        '</tr>'.format(_esc(sub_total))
    )
    # Tax row
    rows.append(
        '<tr>'
        '<td colspan="2"></td>'
        '<td class="tableCell">TAX</td>'
        '<td class="tableCell" style="text-align:right">{}</td>'
        '</tr>'.format(_esc(taxes))
    )
    # Total row
    rows.append(
        '<tr>'
        '<td class="tableCell"></td>'
        '<td class="tableCell" style="text-align:right">****</td>'
        '<td class="tableCell upperCase">Total</td>'
        '<td class="tableCell" style="text-align:right"><strong>{}</strong></td>'
        '</tr>'.format(_esc(total))
    )

    # Divider
    rows.append('<tr><td colspan="4"><hr class="divider"></td></tr>')

    # Tender rows
    tenders = receipt_data.get("tenderArray") or []
    for t in tenders:
        acct = t.get("displayAccountNumber") or ""
        if acct and len(acct) >= 4:
            masked = "XXXXXXXXXXXXX" + acct[-4:]
        elif acct:
            masked = acct
        else:
            masked = ""

        entry_method = _v(t, "tenderEntryMethodDescription", "entryMethod", default="")
        card_type = _v(t, "tenderTypeName", "tenderDescription", default="")
        amt_raw = t.get("amountTender")
        try:
            amt_display = "${:.2f}".format(float(amt_raw)) if amt_raw is not None else "—"
        except (ValueError, TypeError):
            amt_display = str(amt_raw)
        tender_amt = _fmt_amt(amt_raw)

        if masked:
            rows.append(
                '<tr>'
                '<td class="tableCell visa" colspan="3">{}</td>'
                '<td class="tableCell">{}</td>'
                '</tr>'.format(_esc(masked), _esc(entry_method))
            )
        rows.append(
            '<tr><td class="tableCell" colspan="3">APPROVED - PURCHASE</td></tr>'
        )
        rows.append(
            '<tr><td class="tableCell" colspan="3">AMOUNT: {}</td></tr>'.format(_esc(amt_display))
        )
        # date/time/register row
        rows.append(
            '<tr>'
            '<td class="tableCell date">{}</td>'
            '<td class="tableCell time">{}</td>'
            '<td class="tableCell recNumber">{}</td>'
            '<td class="tableCell"></td>'
            '</tr>'.format(_esc(date_display), _esc(time_display), _esc(register_num))
        )
        # card type / amount row
        rows.append(
            '<tr>'
            '<td class="tableCell visa" colspan="3">{}</td>'
            '<td class="tableCell visano">{}</td>'
            '</tr>'.format(_esc(card_type), _esc(tender_amt))
        )
        # Change row
        rows.append(
            '<tr>'
            '<td class="tableCell">Change</td>'
            '<td class="tableCell">0</td>'
            '<td></td><td></td>'
            '</tr>'
        )

    # Closing divider
    rows.append('<tr><td colspan="4"><hr class="divider"></td></tr>')

    # Total tax row
    rows.append(
        '<tr>'
        '<td colspan="2"></td>'
        '<td class="tableCell">TOTAL TAX</td>'
        '<td class="tableCell" style="text-align:right">{}</td>'
        '</tr>'.format(_esc(taxes))
    )

    # Total number of items sold
    rows.append(
        '<tr><td class="tableCell" colspan="4">TOTAL NUMBER OF ITEMS SOLD = {}</td></tr>'.format(
            _esc(str(total_item_count))
        )
    )

    # date/time/warehouse/register/transaction/operator row
    rows.append(
        '<tr>'
        '<td class="tableCell date">{}</td>'
        '<td class="tableCell time">{}</td>'
        '<td class="tableCell">{}</td>'
        '<td class="tableCell">{}</td>'
        '</tr>'.format(
            _esc(date_display), _esc(time_display),
            _esc(warehouse_number), _esc(register_num)
        )
    )
    rows.append(
        '<tr>'
        '<td class="tableCell">Trn: {}</td>'
        '<td class="tableCell">OPT: {}</td>'
        '<td></td><td></td>'
        '</tr>'.format(_esc(transaction_num), _esc(operator_num))
    )

    table_html = (
        '<table class="printReceipt printWrapper" aria-label="receipt">\n'
        '  <thead>\n'
    )
    if membership is not None:
        table_html += '    <tr class="tableRow"><th class="tableCell" colspan="4">Member {}</th></tr>\n'.format(
            _esc(str(membership))
        )
    table_html += '  </thead>\n  <tbody>\n'
    for row in rows:
        # skip the member header row we already added to thead
        if row.startswith('<tr class="tableRow"><th class="tableCell" colspan="4">Member '):
            continue
        table_html += '    ' + row + '\n'
    table_html += '  </tbody>\n</table>\n'

    # --- Footer ---
    footer_html = (
        '<hr class="divider">\n'
        '<div class="footer">\n'
        '  <div class="footerTop"><div class="greetingText" style="text-align:center">Thank You!</div></div>\n'
        '  <div class="comeAgainText" style="text-align:center">Please Come Again</div>\n'
        '  <div class="boxWrapper">\n'
        '    <div class="inlineBox" style="text-align:center">Whse: {}</div>\n'
        '    <div class="inlineBox" style="text-align:center">Trm: {}</div>\n'
        '    <div class="inlineBox" style="text-align:center">Trn: {}</div>\n'
        '    <div class="inlineBox" style="text-align:center">OPT: {}</div>\n'
        '  </div>\n'
        '  <div class="boxSoldWrapper"><div class="itemSold">Items Sold: {}</div></div>\n'
        '  <div><div class="itemSold">P7 {} {}</div></div>\n'
        '</div>\n'
    ).format(
        _esc(warehouse_number), _esc(register_num),
        _esc(transaction_num), _esc(operator_num),
        _esc(str(total_item_count)),
        _esc(date_display), _esc(time_display),
    )

    body_html = header_html + table_html + footer_html

    return (
        "<!DOCTYPE html>\n"
        "<html>\n"
        "<head>\n"
        '<meta charset="UTF-8">\n'
        "<title>Receipt</title>\n"
        "<style>\n"
        + RECEIPT_CSS + "\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        + body_html
        + "</body>\n"
        "</html>\n"
    )


def _generate_online_html(order_data: dict, item_number: str) -> str:
    order_number = _v(order_data, "orderNumber")
    date_raw = _v(order_data, "orderPlacedDate")
    date_display = str(date_raw)[:10] if date_raw != "—" else "—"
    status = _v(order_data, "status")
    total_raw = order_data.get("orderTotal")
    try:
        total_display = f"${float(total_raw):.2f}" if total_raw is not None else "—"
    except (ValueError, TypeError):
        total_display = str(total_raw)

    # Address is flat on the order object (not a sub-object)
    addr_parts = [
        " ".join(filter(None, [order_data.get("firstName"), order_data.get("lastName")])),
        _v(order_data, "line1"),
        _v(order_data, "line2"),
        ", ".join(filter(lambda x: x != "—", [
            _v(order_data, "city"),
            _v(order_data, "state"),
            _v(order_data, "postalCode"),
        ])),
    ]
    addr_html = "<br>".join(_esc(p) for p in addr_parts if p and p != "—")

    # Payment
    payment = order_data.get("orderPayment") or {}
    if isinstance(payment, list):
        payment = payment[0] if payment else {}
    pay_type = _v(payment, "paymentType")
    pay_card = _v(payment, "cardNumber")
    pay_display = f"{pay_type} {pay_card}".strip() if pay_type != "—" else "—"

    # Line items are nested under orderShipTos[].orderLineItems[]
    line_items = []
    for ship_to in (order_data.get("orderShipTos") or []):
        line_items.extend(ship_to.get("orderLineItems") or [])

    # Line item rows
    item_rows = ""
    for line in line_items:
        shipment = line.get("shipment") or {}
        if isinstance(shipment, list):
            shipment = shipment[0] if shipment else {}

        qty = _v(line, "quantity", default="")
        unit_price = line.get("unitPrice")
        ext_price = line.get("merchandiseTotalAmount")
        try:
            unit_price_display = f"${float(unit_price):.2f}" if unit_price is not None else "—"
        except (ValueError, TypeError):
            unit_price_display = str(unit_price)
        try:
            ext_price_display = f"${float(ext_price):.2f}" if ext_price is not None else "—"
        except (ValueError, TypeError):
            ext_price_display = str(ext_price)

        tracking = _v(shipment, "trackingNumber")
        carrier = _v(shipment, "carrierName")
        tracking_display = f"{carrier}: {tracking}" if carrier != "—" and tracking != "—" else tracking
        line_status = _v(line, "orderStatus")

        highlight = ' style="background:#fffbcc;"' if str(line.get("itemNumber", "")) == str(item_number) else ""
        item_rows += (
            f"<tr{highlight}>"
            f"<td>{_esc(line.get('itemNumber', ''))}</td>"
            f"<td>{_esc(line.get('itemDescription', ''))}</td>"
            f"<td>{_esc(qty)}</td>"
            f"<td style=\"text-align:right\">{_esc(unit_price_display)}</td>"
            f"<td style=\"text-align:right\">{_esc(ext_price_display)}</td>"
            f"<td>{_esc(line_status)}</td>"
            f"<td>{_esc(tracking_display)}</td>"
            f"</tr>\n"
        )

    title = f"Online Order — {order_number}"
    body = f"""
<h1>{_esc(title)}</h1>
<div class="subtitle">
  Order #: {_esc(order_number)}
  &nbsp;&bull;&nbsp; Date: {_esc(date_display)}
  &nbsp;&bull;&nbsp; Status: {_esc(status)}
  &nbsp;&bull;&nbsp; Total: {_esc(total_display)}
  &nbsp;&bull;&nbsp; Payment: {_esc(pay_display)}
</div>

{('<div class="section-label">Shipping Address</div><address>' + addr_html + '</address>') if addr_html else ''}

<div class="section-label">Line Items</div>
<table>
  <thead>
    <tr>
      <th>Item #</th><th>Description</th><th>Qty</th>
      <th style="text-align:right">Unit Price</th>
      <th style="text-align:right">Extended Price</th>
      <th>Status</th><th>Tracking</th>
    </tr>
  </thead>
  <tbody>
{item_rows}  </tbody>
  <tfoot>
    <tr class="total-row">
      <td colspan="4" style="text-align:right">Order Total</td>
      <td style="text-align:right">{_esc(total_display)}</td>
      <td colspan="2"></td>
    </tr>
  </tfoot>
</table>

<div class="footer">Generated by Costco Order Lookup</div>
""".strip()

    return _html_doc(title, body)
