"""
orders.py — Real GraphQL queries (copied from HAR) for online orders and
in-warehouse receipts.  Searches both sources for a given item number.

Search strategy:
  - Date range: last DEFAULT_SEARCH_YEARS years, queried in 6-month chunks
  - Online orders:   getOnlineOrders  → filters orderLineItems by itemNumber
  - Warehouse receipts: receiptsWithCounts → filters itemArray by itemNumber
"""

import logging
import string
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from typing import Any, Optional

from .client import GraphQLClient

log = logging.getLogger(__name__)

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)

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
# Public entry point — item search
# ---------------------------------------------------------------------------

def find_orders_by_item(
    client: GraphQLClient,
    item_number: str,
    warehouse_number: str,
    search_years: int = DEFAULT_SEARCH_YEARS,
    on_progress=None,
    token: str = "",
    config: dict = None,
) -> list[dict]:
    """
    Search both online orders and in-warehouse receipts for the given item.
    Returns a combined, date-sorted list of order records.

    on_progress: optional callable(current, total, message) — called after
    each chunk is processed. If None, behaviour is identical to before.

    token/config: when provided, each worker thread creates its own GraphQLClient
    (thread-safe). Falls back to the shared client when omitted.
    """
    results: list[dict] = []
    date_chunks = _build_date_chunks(search_years)
    total_tasks = len(date_chunks) * 2
    log.info("Searching item %s over %d year(s) in %d date chunks (%d parallel tasks)",
             item_number, search_years, len(date_chunks), total_tasks)

    _lock = threading.Lock()
    _counter = [0]

    def _progress(message: str):
        with _lock:
            _counter[0] += 1
            if on_progress:
                on_progress(_counter[0], total_tasks, message)

    def _fetch_online_chunk(start, end):
        c = _make_client(token, config) if token and config else client
        records = _fetch_online_orders(c, item_number, warehouse_number, start, end)
        _progress(f"Online orders {_fmt_date_online(start)} – {_fmt_date_online(end)}")
        return records

    def _fetch_receipt_chunk(start, end):
        c = _make_client(token, config) if token and config else client
        records = _fetch_receipts(c, item_number, start, end)
        _progress(f"Receipts {_fmt_date_receipt(start)} – {_fmt_date_receipt(end)}")
        return records

    max_workers = min(total_tasks, 5)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = []
        for start, end in date_chunks:
            futures.append(executor.submit(_fetch_online_chunk, start, end))
            futures.append(executor.submit(_fetch_receipt_chunk, start, end))
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as exc:
                log.warning("Chunk fetch failed: %s", exc)

    results.sort(key=lambda r: r.get("date", ""), reverse=True)
    log.info("Total results for item %s: %d", item_number, len(results))
    return results


# ---------------------------------------------------------------------------
# Public entry point — description search
# ---------------------------------------------------------------------------

def find_orders_by_description(
    client: GraphQLClient,
    description_query: str,
    warehouse_number: str,
    search_years: int = DEFAULT_SEARCH_YEARS,
    on_progress=None,
    token: str = "",
    config: dict = None,
) -> list[dict]:
    """
    Search both online orders and in-warehouse receipts for items whose
    description contains description_query (case-insensitive substring match).
    Returns a combined, date-sorted list of order records.

    Progress tracking uses a two-phase total:
      Phase 1: len(chunks) * 2  (online chunks + receipt summary chunks, run in parallel)
      Phase 2: adds len(all_receipts) to the total after summaries are fetched

    token/config: when provided, each worker thread creates its own GraphQLClient
    (thread-safe). Falls back to the shared client when omitted.
    """
    results: list[dict] = []
    date_chunks = _build_date_chunks(search_years)
    needle = _normalize(description_query)

    log.info("Description search %r over %d year(s) in %d date chunks",
             description_query, search_years, len(date_chunks))

    phase1_tasks = len(date_chunks) * 2
    _lock = threading.Lock()
    _counter = [0]

    def _progress(current_total: int, message: str):
        with _lock:
            _counter[0] += 1
            if on_progress:
                on_progress(_counter[0], current_total, message)

    def _online_desc_chunk(start, end):
        c = _make_client(token, config) if token and config else client
        records = _fetch_online_orders_by_description(c, needle, warehouse_number, start, end)
        _progress(phase1_tasks, f"Online orders {_fmt_date_online(start)} – {_fmt_date_online(end)}")
        return ("online", records)

    def _summary_chunk(start, end):
        c = _make_client(token, config) if token and config else client
        summaries = _fetch_receipt_summaries(c, start, end)
        _progress(phase1_tasks, f"Receipt summaries {_fmt_date_receipt(start)} – {_fmt_date_receipt(end)}")
        return ("summary", summaries)

    # Phase 1: online description chunks + receipt summary chunks in parallel
    all_receipt_summaries: list[dict] = []
    max_workers_p1 = min(phase1_tasks, 5)
    with ThreadPoolExecutor(max_workers=max_workers_p1) as executor:
        futures = []
        for start, end in date_chunks:
            futures.append(executor.submit(_online_desc_chunk, start, end))
            futures.append(executor.submit(_summary_chunk, start, end))
        for future in as_completed(futures):
            try:
                kind, data = future.result()
                if kind == "online":
                    results.extend(data)
                else:
                    all_receipt_summaries.extend(data)
            except Exception as exc:
                log.warning("Phase 1 chunk failed: %s", exc)

    log.info("Phase 1 complete: %d online results, %d receipt summaries",
             len(results), len(all_receipt_summaries))

    # Phase 2: fetch receipt details in parallel
    phase2_total = phase1_tasks + len(all_receipt_summaries)

    def _detail_fetch(receipt_summary):
        c = _make_client(token, config) if token and config else client
        record = _fetch_receipt_detail_by_description(c, receipt_summary, needle)
        barcode = receipt_summary.get("transactionBarcode", "?")
        _progress(phase2_total, f"Receipt detail {barcode}")
        return record

    max_workers_p2 = min(len(all_receipt_summaries), 8) if all_receipt_summaries else 1
    with ThreadPoolExecutor(max_workers=max_workers_p2) as executor:
        futures = [executor.submit(_detail_fetch, s) for s in all_receipt_summaries]
        for future in as_completed(futures):
            try:
                record = future.result()
                if record is not None:
                    results.append(record)
            except Exception as exc:
                log.warning("Receipt detail fetch failed: %s", exc)

    results.sort(key=lambda r: r.get("date", ""), reverse=True)
    log.info("Total description search results for %r: %d", description_query, len(results))
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
                    record = _build_online_record(order, line, item_number)
                    log.debug("Match: order_id=%s date=%s status=%s",
                              record["order_id"], record["date"], record["status"])
                    records.append(record)

        fetched_so_far = (page - 1) * page_size + len(orders)
        if fetched_so_far >= total or len(orders) < page_size:
            break
        page += 1

    return records


def _fetch_online_orders_by_description(
    client: GraphQLClient,
    needle: str,
    warehouse_number: str,
    start: date,
    end: date,
) -> list[dict]:
    """Fetch ALL online orders for a date range and filter by description substring."""
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
        log.debug("getOnlineOrders (desc) page=%d range=%s–%s", page, variables["startDate"], variables["endDate"])
        try:
            data = client.execute(GET_ONLINE_ORDERS_QUERY, variables)
        except RuntimeError as exc:
            log.warning("getOnlineOrders failed for %s–%s: %s", start, end, exc)
            break

        raw = _dig(data, "data", "getOnlineOrders")
        if isinstance(raw, list):
            result = raw[0] if raw else {}
        else:
            result = raw or {}
        orders = result.get("bcOrders") or []
        total = result.get("totalNumberOfRecords", 0)

        for order in orders:
            for line in (order.get("orderLineItems") or []):
                desc = str(line.get("itemDescription", ""))
                if needle in _normalize(desc):
                    item_num = str(line.get("itemNumber", "—"))
                    record = _build_online_record(order, line, item_num)
                    log.debug("Desc match: order_id=%s item=%s desc=%s",
                              record["order_id"], item_num, desc)
                    records.append(record)

        fetched_so_far = (page - 1) * page_size + len(orders)
        if fetched_so_far >= total or len(orders) < page_size:
            break
        page += 1

    return records


def _build_online_record(order: dict, line: dict, item_number: str) -> dict:
    """Normalize an online order line item into the standard record dict."""
    shipment = line.get("shipment") or {}
    if isinstance(shipment, list):
        shipment = shipment[0] if shipment else {}
    return {
        "source":        "online",
        "order_id":      str(_get(order, "orderNumber", "orderHeaderId", default="—")),
        "date":          _fmt_display_date(_get(order, "orderPlacedDate", default="")),
        "item_number":   str(item_number),
        "description":   str(line.get("itemDescription", "—")),
        "status":        str(line.get("orderStatus") or line.get("status") or order.get("status") or "—"),
        "carrier":       str(shipment.get("carrierName", "—")),
        "tracking":      str(shipment.get("trackingNumber", "—")),
        "receipt_total": f"${float(order.get('orderTotal', 0)):.2f}",
        "warehouse":     str(order.get("warehouseNumber", "—")),
        "tender":        "—",
    }


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


def _fetch_receipt_summaries(
    client: GraphQLClient,
    start: date,
    end: date,
) -> list[dict]:
    """Fetch all receipt summary records for a date range (no item filtering)."""
    variables = {
        "startDate": _fmt_date_receipt(start),
        "endDate": _fmt_date_receipt(end),
        "documentType": "all",
        "documentSubType": "all",
    }
    log.debug("receiptsWithCounts (summary) range=%s–%s", variables["startDate"], variables["endDate"])
    try:
        data = client.execute(RECEIPTS_WITH_COUNTS_QUERY, variables)
    except RuntimeError as exc:
        log.warning("receiptsWithCounts failed for %s–%s: %s", start, end, exc)
        return []

    result = _dig(data, "data", "receiptsWithCounts") or {}
    receipts = result.get("receipts") or []
    log.debug("receiptsWithCounts summary returned %d receipts for %s–%s", len(receipts), start, end)
    return receipts


def _fetch_receipt_detail_by_description(
    client: GraphQLClient,
    receipt_summary: dict,
    needle: str,
) -> Optional[dict]:
    """
    Fetch full receipt detail for a summary record, filter itemArray by description
    needle. Returns a normalized record dict if any item matches, else None.
    """
    barcode = str(receipt_summary.get("transactionBarcode") or "").strip()
    if not barcode:
        return None

    variables = {"barcode": barcode, "documentType": "warehouse"}
    log.debug("RECEIPT_DETAIL_QUERY barcode=%s", barcode)
    try:
        data = client.execute(RECEIPT_DETAIL_QUERY, variables)
    except RuntimeError as exc:
        log.warning("RECEIPT_DETAIL_QUERY failed for barcode=%s: %s", barcode, exc)
        return None

    result = _dig(data, "data", "receiptsWithCounts") or {}
    receipts = result.get("receipts") or []
    if not receipts:
        return None
    receipt = receipts[0]

    item_array = receipt.get("itemArray") or []
    matched_item = None
    matched_item_number = None
    for item in item_array:
        full_desc = _normalize(
            (item.get("itemDescription01") or "") + " " + (item.get("itemDescription02") or "")
        )
        if needle in full_desc:
            matched_item = item
            matched_item_number = str(item.get("itemNumber", "—"))
            break

    if matched_item is None:
        return None

    tenders = receipt.get("tenderArray") or []
    tender_str = ", ".join(
        f"{t.get('tenderDescription', '?')} ${float(t.get('amountTender', 0)):.2f}"
        for t in tenders
    ) or "—"

    record = {
        "source":        "warehouse",
        "order_id":      str(receipt.get("transactionBarcode", barcode)),
        "date":          _fmt_display_date(receipt.get("transactionDateTime", "")),
        "item_number":   matched_item_number,
        "description":   (
            (matched_item.get("itemDescription01") or "") + " " +
            (matched_item.get("itemDescription02") or "")
        ).strip() or "Warehouse purchase",
        "status":        "Purchased",
        "carrier":       "—",
        "tracking":      "—",
        "receipt_total": f"${float(receipt.get('total', 0)):.2f} (receipt total)",
        "warehouse":     str(receipt.get("warehouseName", "—")),
        "tender":        tender_str,
    }
    log.debug("Desc receipt match: barcode=%s item=%s", barcode, matched_item_number)
    return record


# ---------------------------------------------------------------------------
# Thread helpers
# ---------------------------------------------------------------------------

def _make_client(token: str, config: dict) -> GraphQLClient:
    """Create a fresh GraphQLClient with its own requests.Session (thread-safe)."""
    import requests
    return GraphQLClient(requests.Session(), config, token)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase and strip punctuation for fuzzy description matching."""
    return (text or "").lower().translate(_PUNCT_TABLE)


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
