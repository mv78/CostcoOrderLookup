"""
web.py — Flask app factory and all routes for Costco Order Lookup Web UI.
"""

import json
import logging
import queue
import secrets
import threading

import requests
from flask import Flask, Response, flash, redirect, render_template, request, stream_with_context, url_for

from costco_lookup import auth, config as cfg
from costco_lookup.client import GraphQLClient
from costco_lookup.downloader import _fetch_and_render_online, _fetch_and_render_warehouse
from costco_lookup.orders import find_orders_by_description, find_orders_by_item
from costco_lookup.paths import TEMPLATE_DIR

log = logging.getLogger(__name__)

# Module-level result cache for SSE search results
_result_cache: dict = {}
_cache_lock = threading.Lock()


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=str(TEMPLATE_DIR),
    )
    # Secret key required for flash() / session
    app.secret_key = "costco-order-lookup-web-ui"

    # ------------------------------------------------------------------
    # Context processor — inject token_valid into every template
    # ------------------------------------------------------------------

    @app.context_processor
    def inject_token_status():
        try:
            auth.get_valid_token()
            token_valid = True
        except RuntimeError:
            token_valid = False
        return {"token_valid": token_valid}

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.route("/")
    def index():
        log.debug("GET /")
        return render_template("index.html")

    @app.route("/inject-token", methods=["POST"])
    def inject_token():
        token = (request.form.get("token") or "").strip()
        refresh_token = (request.form.get("refresh_token") or "").strip() or None
        log.debug("POST /inject-token token_length=%d refresh_token=%s", len(token), "present" if refresh_token else "absent")
        if not token:
            flash("Token cannot be empty.", "error")
            return redirect(url_for("index"))
        try:
            auth.inject_token(token, refresh_token=refresh_token)
            if refresh_token:
                flash("Token + refresh token saved. Token will auto-renew on expiry.", "success")
            else:
                flash("Token injected successfully. You can now search orders.", "success")
            log.info("Token injected via web UI (refresh_token=%s)", "present" if refresh_token else "absent")
        except Exception as exc:
            log.error("inject_token failed: %s", exc)
            flash(f"Failed to save token: {exc}", "error")
        return redirect(url_for("index"))

    @app.route("/search")
    def search():
        item = (request.args.get("item") or "").strip()
        description = (request.args.get("description") or "").strip()
        years = request.args.get("years", "5")
        log.debug("GET /search item=%r description=%r years=%r", item, description, years)

        if not item and not description:
            flash("Please enter an item number or description.", "warning")
            return redirect(url_for("index"))

        return render_template("loading.html", item=item, description=description, years=years)

    @app.route("/search/stream")
    def search_stream():
        item = (request.args.get("item") or "").strip()
        description = (request.args.get("description") or "").strip()
        try:
            years = int(request.args.get("years", 5))
        except (ValueError, TypeError):
            years = 5
        log.debug("GET /search/stream item=%r description=%r years=%d", item, description, years)

        def generate():
            q = queue.Queue()
            search_id = secrets.token_hex(8)

            def on_progress(current, total, message):
                q.put({"type": "progress", "current": current, "total": total, "message": message})

            def run_search():
                try:
                    config = cfg.load_config()
                    token = auth.get_valid_token()
                    client = GraphQLClient(requests.Session(), config, token)
                    warehouse_number = config.get("warehouse_number", "")
                    if item:
                        results = find_orders_by_item(
                            client, item, warehouse_number, years, on_progress=on_progress
                        )
                        search_meta = {"type": "item", "query": item}
                    else:
                        results = find_orders_by_description(
                            client, description, warehouse_number, years, on_progress=on_progress
                        )
                        search_meta = {"type": "description", "query": description}
                    with _cache_lock:
                        _result_cache[search_id] = {
                            "results": results,
                            "meta": search_meta,
                            "years": years,
                        }
                    q.put({"type": "done", "search_id": search_id})
                except RuntimeError as exc:
                    q.put({"type": "error", "message": str(exc)})
                except Exception as exc:
                    log.error("run_search failed: %s", exc)
                    q.put({"type": "error", "message": f"Search failed: {exc}"})

            threading.Thread(target=run_search, daemon=True).start()

            while True:
                event = q.get()
                yield f"data: {json.dumps(event)}\n\n"
                if event["type"] in ("done", "error"):
                    break

        return Response(
            stream_with_context(generate()),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.route("/search/results/<search_id>")
    def search_results(search_id):
        log.debug("GET /search/results/%s", search_id)
        with _cache_lock:
            cached = _result_cache.pop(search_id, None)
        if cached is None:
            flash("Search results expired or not found.", "warning")
            return redirect(url_for("index"))
        results = cached["results"]
        meta = cached["meta"]
        years = cached["years"]
        online_count = sum(1 for r in results if r.get("source") == "online")
        warehouse_count = sum(1 for r in results if r.get("source") == "warehouse")
        return render_template(
            "results.html",
            results=results,
            item_number=meta["query"] if meta["type"] == "item" else None,
            description_query=meta["query"] if meta["type"] == "description" else None,
            search_type=meta["type"],
            years=years,
            online_count=online_count,
            warehouse_count=warehouse_count,
        )

    @app.route("/receipt/<barcode>")
    def view_receipt(barcode: str):
        log.debug("GET /receipt/%s", barcode)
        try:
            config = cfg.load_config()
            token = auth.get_valid_token()
            client = GraphQLClient(requests.Session(), config, token)
            html = _fetch_and_render_warehouse(client, barcode, "")
        except RuntimeError as exc:
            log.warning("view_receipt failed: %s", exc)
            return Response(
                f"<html><body><p style='color:red'>[error] {exc}</p></body></html>",
                mimetype="text/html",
                status=400,
            )
        except Exception as exc:
            log.error("view_receipt unexpected error: %s", exc)
            return Response(
                f"<html><body><p style='color:red'>[error] {exc}</p></body></html>",
                mimetype="text/html",
                status=500,
            )
        return Response(html, mimetype="text/html")

    @app.route("/order/<order_number>")
    def view_order(order_number: str):
        log.debug("GET /order/%s", order_number)
        try:
            config = cfg.load_config()
            token = auth.get_valid_token()
            client = GraphQLClient(requests.Session(), config, token)
            html = _fetch_and_render_online(client, order_number, "")
        except RuntimeError as exc:
            log.warning("view_order failed: %s", exc)
            return Response(
                f"<html><body><p style='color:red'>[error] {exc}</p></body></html>",
                mimetype="text/html",
                status=400,
            )
        except Exception as exc:
            log.error("view_order unexpected error: %s", exc)
            return Response(
                f"<html><body><p style='color:red'>[error] {exc}</p></body></html>",
                mimetype="text/html",
                status=500,
            )
        return Response(html, mimetype="text/html")

    return app
