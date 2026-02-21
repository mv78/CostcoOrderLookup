"""
orders.py — Real GraphQL queries (copied from HAR) for online orders and
in-warehouse receipts.  Searches both sources for a given item number.

Search strategy:
  - Date range: last DEFAULT_SEARCH_YEARS years, queried in 6-month chunks
  - Online orders:   getOnlineOrders  → filters orderLineItems by itemNumber
  - Warehouse receipts: receiptsWithCounts → filters itemArray by itemNumber
"""

import logging
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from typing import Any

from .client import GraphQLClient

log = logging.getLogger(__name__)

DEFAULT_SEARCH_YEARS = 5

# ---------------------------------------------------------------------------
# Real queries (verbatim from HAR)
# ---------------------------------------------------------------------------

GET_ONLINE_ORDERS_QUERY = """
query getOnlineOrders($startDate:String!, $endDate:String!, $pageNumber:Int, $pageSize:Int, $warehouseNumber:String!) {
    getOnlineOrders(startDate:$startDate, endDate:$endDate, pageNumber: $pageNumber, pageSize: $pageSize, warehouseNumber: $warehouseNumber) {
      pageNumber
      pageSize
      totalNumberOfRecords
      bcOrders {
        orderHeaderId
        orderPlacedDate : orderedDate
        orderNumber : sourceOrderNumber
        orderTotal
        warehouseNumber
        status
        emailAddress
        orderLineItems {
          itemNumber
          itemDescription
          status
          orderStatus
          shippingType
          deliveryDate
          scheduledDeliveryDate
          shipment {
            trackingNumber
            carrierName
            deliveredDate
            status
          }
        }
      }
    }
  }
"""

RECEIPTS_WITH_COUNTS_QUERY = """
query receiptsWithCounts($startDate: String!, $endDate: String!, $documentType: String!, $documentSubType: String!) {
    receiptsWithCounts(startDate: $startDate, endDate: $endDate, documentType: $documentType, documentSubType: $documentSubType) {
      inWarehouse
      gasStation
      carWash
      receipts {
        warehouseName
        receiptType
        documentType
        transactionDateTime
        transactionBarcode
        total
        totalItemCount
        itemArray {
          itemNumber
        }
        tenderArray {
          tenderDescription
          amountTender
        }
      }
    }
  }
"""

RECEIPT_DETAIL_QUERY = """
query receiptsWithCounts($barcode: String!, $documentType: String!) {
    receiptsWithCounts(barcode: $barcode, documentType: $documentType) {
      receipts {
        warehouseName
        warehouseNumber
        warehouseAddress1
        warehouseAddress2
        warehouseCity
        warehouseState
        warehousePostalCode
        receiptType
        documentType
        transactionDateTime
        transactionBarcode
        total
        subTotal
        taxes
        totalItemCount
        membershipNumber
        registerNumber
        transactionNumber
        operatorNumber
        instantSavings
        itemArray {
          itemNumber
          itemDescription01
          itemDescription02
          itemIdentifier
          unit
          amount
          itemUnitPriceAmount
          taxFlag
        }
        tenderArray {
          tenderDescription
          tenderTypeName
          amountTender
          displayAccountNumber
          tenderEntryMethodDescription
          entryMethod
        }
      }
    }
  }
"""

ORDER_DETAIL_QUERY = """
query getOrderDetails($orderNumbers: [String]!) {
    getOrderDetails(orderNumbers: $orderNumbers) {
      orderNumber: sourceOrderNumber
      orderPlacedDate: orderedDate
      status
      orderTotal
      firstName
      lastName
      line1
      line2
      city
      state
      postalCode
      orderPayment {
        paymentType
        cardNumber
        totalCharged
      }
      orderShipTos {
        orderLineItems {
          itemNumber
          itemDescription: sourceItemDescription
          quantity: orderedTotalQuantity
          unitPrice
          merchandiseTotalAmount
          orderStatus
          scheduledDeliveryDate
          shipment {
            trackingNumber
            carrierName
            deliveredDate
            estimatedArrivalDate
          }
        }
      }
    }
  }
"""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def find_orders_by_item(
    client: GraphQLClient,
    item_number: str,
    warehouse_number: str,
    search_years: int = DEFAULT_SEARCH_YEARS,
) -> list[dict]:
    """
    Search both online orders and in-warehouse receipts for the given item.
    Returns a combined, date-sorted list of order records.
    """
    results: list[dict] = []
    date_chunks = _build_date_chunks(search_years)
    log.info("Searching item %s over %d year(s) in %d date chunks",
             item_number, search_years, len(date_chunks))

    # --- Online orders ---
    log.info("Searching online orders…")
    online_total = 0
    for start, end in date_chunks:
        chunk = _fetch_online_orders(client, item_number, warehouse_number, start, end)
        online_total += len(chunk)
        results.extend(chunk)
    log.info("Online orders: %d match(es) found for item %s", online_total, item_number)

    # --- In-warehouse receipts ---
    log.info("Searching warehouse receipts…")
    receipt_total = 0
    for start, end in date_chunks:
        chunk = _fetch_receipts(client, item_number, start, end)
        receipt_total += len(chunk)
        results.extend(chunk)
    log.info("Warehouse receipts: %d match(es) found for item %s", receipt_total, item_number)

    results.sort(key=lambda r: r.get("date", ""), reverse=True)
    log.info("Total results for item %s: %d", item_number, len(results))
    return results


# ---------------------------------------------------------------------------
# Online orders
# ---------------------------------------------------------------------------

def _fetch_online_orders(
    client: GraphQLClient,
    item_number: str,
    warehouse_number: str,
    start: date,
    end: date,
) -> list[dict]:
    page = 1
    page_size = 50
    records: list[dict] = []

    while True:
        variables = {
            "startDate": _fmt_date_online(start),
            "endDate": _fmt_date_online(end),
            "pageNumber": page,
            "pageSize": page_size,
            "warehouseNumber": str(warehouse_number),
        }
        log.debug("getOnlineOrders page=%d range=%s–%s warehouse=%s",
                  page, variables["startDate"], variables["endDate"], warehouse_number)
        try:
            data = client.execute(GET_ONLINE_ORDERS_QUERY, variables)
        except RuntimeError as exc:
            log.warning("getOnlineOrders failed for %s–%s: %s", start, end, exc)
            break

        raw = _dig(data, "data", "getOnlineOrders")
        # API returns a list wrapper: [{"pageNumber":..., "bcOrders":[...]}]
        if isinstance(raw, list):
            result = raw[0] if raw else {}
        else:
            result = raw or {}
        orders = result.get("bcOrders") or []
        total = result.get("totalNumberOfRecords", 0)
        log.debug("getOnlineOrders page=%d returned %d orders (total=%d)", page, len(orders), total)

        for order in orders:
            for line in (order.get("orderLineItems") or []):
                if str(line.get("itemNumber", "")) == str(item_number):
                    shipment = line.get("shipment") or {}
                    # API may return shipment as a list; take the first entry
                    if isinstance(shipment, list):
                        shipment = shipment[0] if shipment else {}
                    record = {
                        "source":        "online",
                        "order_id":      str(_get(order, "orderNumber", "orderHeaderId", default="—")),
                        "date":          _fmt_display_date(_get(order, "orderPlacedDate", default="")),
                        "item_number":   str(line.get("itemNumber", item_number)),
                        "description":   str(line.get("itemDescription", "—")),
                        "status":        str(line.get("orderStatus") or line.get("status") or order.get("status") or "—"),
                        "carrier":       str(shipment.get("carrierName", "—")),
                        "tracking":      str(shipment.get("trackingNumber", "—")),
                        "receipt_total": f"${float(order.get('orderTotal', 0)):.2f}",
                        "warehouse":     str(order.get("warehouseNumber", "—")),
                        "tender":        "—",
                    }
                    log.debug("Match: order_id=%s date=%s status=%s",
                              record["order_id"], record["date"], record["status"])
                    records.append(record)

        fetched_so_far = (page - 1) * page_size + len(orders)
        if fetched_so_far >= total or len(orders) < page_size:
            break
        page += 1

    return records


# ---------------------------------------------------------------------------
# In-warehouse receipts
# ---------------------------------------------------------------------------

def _fetch_receipts(
    client: GraphQLClient,
    item_number: str,
    start: date,
    end: date,
) -> list[dict]:
    variables = {
        "startDate": _fmt_date_receipt(start),
        "endDate": _fmt_date_receipt(end),
        "documentType": "all",
        "documentSubType": "all",
    }
    log.debug("receiptsWithCounts range=%s–%s",
              variables["startDate"], variables["endDate"])
    records: list[dict] = []
    try:
        data = client.execute(RECEIPTS_WITH_COUNTS_QUERY, variables)
    except RuntimeError as exc:
        log.warning("receiptsWithCounts failed for %s–%s: %s", start, end, exc)
        return records

    result = _dig(data, "data", "receiptsWithCounts") or {}
    receipts = result.get("receipts") or []
    log.debug("receiptsWithCounts returned %d receipts for %s–%s", len(receipts), start, end)

    for receipt in receipts:
        item_numbers_in_receipt = [
            str(i.get("itemNumber", "")) for i in (receipt.get("itemArray") or [])
        ]
        if str(item_number) not in item_numbers_in_receipt:
            continue

        tenders = receipt.get("tenderArray") or []
        tender_str = ", ".join(
            f"{t.get('tenderDescription', '?')} ${float(t.get('amountTender', 0)):.2f}"
            for t in tenders
        ) or "—"

        record = {
            "source":        "warehouse",
            "order_id":      str(receipt.get("transactionBarcode", "—")),
            "date":          _fmt_display_date(receipt.get("transactionDateTime", "")),
            "item_number":   str(item_number),
            "description":   "Warehouse purchase (see receipt for detail)",
            "status":        "Purchased",
            "carrier":       "—",
            "tracking":      "—",
            "receipt_total": f"${float(receipt.get('total', 0)):.2f} (receipt total)",
            "warehouse":     str(receipt.get("warehouseName", "—")),
            "tender":        tender_str,
        }
        log.debug("Receipt match: barcode=%s date=%s warehouse=%s",
                  record["order_id"], record["date"], record["warehouse"])
        records.append(record)

    return records


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _build_date_chunks(years: int) -> list[tuple[date, date]]:
    """Build list of (start, end) 6-month chunks from today back `years` years."""
    chunks = []
    end = date.today()
    limit = end - relativedelta(years=years)
    while end > limit:
        start = max(end - relativedelta(months=6), limit)
        chunks.append((start, end))
        end = start - timedelta(days=1)
    return chunks


def _fmt_date_online(d: date) -> str:
    return f"{d.year}-{d.month}-{d.day}"


def _fmt_date_receipt(d: date) -> str:
    return f"{d.month}/{d.day:02d}/{d.year}"


def _fmt_display_date(raw: str) -> str:
    if not raw:
        return "—"
    return str(raw)[:10]


def _get(d: dict, *keys: str, default: Any = "—") -> Any:
    for key in keys:
        if key in d and d[key] is not None:
            return d[key]
    return default


def _dig(data: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data
