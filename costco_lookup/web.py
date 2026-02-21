"""
web.py — Flask app factory and all routes for Costco Order Lookup Web UI.
"""

import logging

import requests
from flask import Flask, Response, flash, redirect, render_template, request, url_for

from costco_lookup import auth, config as cfg
from costco_lookup.client import GraphQLClient
from costco_lookup.downloader import _fetch_and_render_online, _fetch_and_render_warehouse
from costco_lookup.orders import find_orders_by_item
from costco_lookup.paths import BASE_DIR

log = logging.getLogger(__name__)


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(
        __name__,
        template_folder=str(BASE_DIR / "costco_lookup" / "templates"),
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
        log.debug("POST /inject-token token_length=%d", len(token))
        if not token:
            flash("Token cannot be empty.", "error")
            return redirect(url_for("index"))
        try:
            auth.inject_token(token)
            flash("Token injected successfully. You can now search orders.", "success")
            log.info("Token injected via web UI")
        except Exception as exc:
            log.error("inject_token failed: %s", exc)
            flash(f"Failed to save token: {exc}", "error")
        return redirect(url_for("index"))

    @app.route("/search")
    def search():
        item = (request.args.get("item") or "").strip()
        years_raw = request.args.get("years", "5")
        log.debug("GET /search item=%r years=%r", item, years_raw)

        if not item:
            flash("Item number is required.", "error")
            return redirect(url_for("index"))

        try:
            years = int(years_raw)
        except (ValueError, TypeError):
            years = 5

        try:
            config = cfg.load_config()
            token = auth.get_valid_token()
            client = GraphQLClient(requests.Session(), config, token)
        except RuntimeError as exc:
            log.warning("search: client setup failed: %s", exc)
            flash(str(exc), "error")
            return redirect(url_for("index"))
        except (FileNotFoundError, ValueError) as exc:
            log.warning("search: config error: %s", exc)
            flash(str(exc), "error")
            return redirect(url_for("index"))

        try:
            results = find_orders_by_item(
                client,
                item_number=item,
                warehouse_number=config.get("warehouse_number", ""),
                search_years=years,
            )
        except Exception as exc:
            log.error("find_orders_by_item failed: %s", exc)
            flash(f"Search failed: {exc}", "error")
            return redirect(url_for("index"))

        online_count = sum(1 for r in results if r.get("source") == "online")
        warehouse_count = sum(1 for r in results if r.get("source") == "warehouse")

        return render_template(
            "results.html",
            results=results,
            item_number=item,
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
