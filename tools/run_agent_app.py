"""Start the local analysis app: python -m tools.run_agent_app --prompt-key."""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Agentic Minds local analysis workspace")
    parser.add_argument("--host", choices=["127.0.0.1", "localhost", "::1", "0.0.0.0"], default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8870)
    parser.add_argument("--db", type=Path)
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--prompt-key", action="store_true", help="Read MIA key privately from the terminal")
    args = parser.parse_args()
    key = os.environ.get("MIA_API_KEY")
    if args.prompt_key and not key:
        key = getpass.getpass("MIA API key (hidden): ").strip()
    from app.server import DEFAULT_DB, create_app
    from tools.mia_client import MiaClient
    import uvicorn

    client = MiaClient(key) if key else None
    app = create_app(runtime_root=args.runtime_root, source_db=args.db or DEFAULT_DB, client=client, searxng_url=os.environ.get("SEARXNG_URL"))
    print(f"Agentic Minds: http://{args.host}:{args.port}", flush=True)
    if not key:
        print("Kloudeks is not configured; use MIA_API_KEY or --prompt-key for live analysis.", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
