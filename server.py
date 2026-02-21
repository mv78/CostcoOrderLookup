"""Web UI entry point. Run: python server.py [--port 8080]"""
import argparse
import threading
import webbrowser

from costco_lookup.web import create_app


def main():
    parser = argparse.ArgumentParser(description="Costco Order Lookup — Web UI")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    app = create_app()
    url = f"http://localhost:{args.port}"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print(f"Starting web UI at {url}  (Ctrl+C to stop)")
    app.run(host="127.0.0.1", port=args.port, debug=False)


if __name__ == "__main__":
    main()
