import argparse
import getpass
import logging
import sys
import threading
import webbrowser
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="orienter", description="Walk east, one step at a time.")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--no-browser", action="store_true")

    sub.add_parser("garmin-login", help="log in to Garmin Connect once and store tokens")

    sync = sub.add_parser("sync", help="fetch steps and extend the route (for Task Scheduler)")
    sync.add_argument("--force", action="store_true", help="ignore the 1-hour resync delay")

    imp = sub.add_parser("import", help="import Garmin Connect steps CSV export(s)")
    imp.add_argument("files", nargs="+", type=Path)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from orienter.config import load_config

    cfg = load_config()

    if args.command == "garmin-login":
        from garminconnect import GarminConnectTooManyRequestsError

        from orienter.sources.garmin import interactive_login

        email = input("Garmin email: ").strip()
        password = getpass.getpass("Garmin password (not stored): ")
        try:
            name = interactive_login(cfg.garmin_tokenstore, email, password)
        except GarminConnectTooManyRequestsError:
            print(
                "Garmin is rate limiting logins from this computer/network (HTTP 429).\n"
                "Don't retry right away: each attempt extends the block. Try again in an hour\n"
                "or more, or once from another network (e.g. a phone hotspot).",
                file=sys.stderr,
            )
            return 1
        print(f"Logged in as {name}. Tokens saved to {cfg.garmin_tokenstore}")
        return 0

    from orienter.app import App

    core = App(cfg)

    if args.command == "import":
        from orienter.sources.csv_import import CsvImportError, parse_steps_csv

        for path in args.files:
            try:
                counts = parse_steps_csv(path.read_text(encoding="utf-8-sig"))
            except (OSError, CsvImportError) as e:
                print(f"{path}: {e}", file=sys.stderr)
                return 1
            core.game.import_steps(counts, source="garmin-csv")
            print(f"{path}: {len(counts)} days ({min(counts)} to {max(counts)})")
        if core.game.configured:
            for err in core.sync().errors:
                print(f"error: {err}", file=sys.stderr)
        return 0

    if args.command == "sync":
        if not core.game.configured:
            print("Not configured yet: run `orienter serve` and complete the setup.")
            return 1
        status = core.sync(force=args.force)
        for err in status.errors:
            print(f"error: {err}", file=sys.stderr)
        stats = core.game.state()["stats"]
        print(f"{stats['steps']:,} steps, {stats['walked_m'] / 1000:.1f} km walked.")
        return 1 if status.errors else 0

    import uvicorn

    from orienter.api import create_app

    url = f"http://127.0.0.1:{args.port}"
    if not args.no_browser:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    if core.game.configured:
        core.sync_in_background()
    uvicorn.run(create_app(core), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
