"""JARVIS entry point.

    python main.py          terminal chat (type, or 'v' for push-to-talk)
    python main.py --hud    holographic HUD in the browser (main screen)
"""

import argparse
import sys

from jarvis.config import ConfigError, load_config
from jarvis.logging_setup import setup_logging


def main():
    parser = argparse.ArgumentParser(description="JARVIS personal AI assistant")
    parser.add_argument("--hud", action="store_true", help="start the holographic HUD (browser main screen)")
    parser.add_argument("--no-browser", action="store_true", help="with --hud: do not open the browser")
    parser.add_argument("--skip-model-check", action="store_true", help="do not verify model IDs at startup")
    args = parser.parse_args()

    try:
        config = load_config()
    except ConfigError as error:
        sys.exit(f"Configuration error: {error}")
    setup_logging(config)

    from jarvis.app import build_app

    if args.hud:
        from jarvis.server import run_server
        run_server(config, open_browser=not args.no_browser, check_models=not args.skip_model_check)
        return

    from jarvis.cli import reminder_printer, run_terminal

    speaker_holder = []
    print("Starting JARVIS...")
    app = build_app(config, notify=reminder_printer(speaker_holder),
                    check_models=not args.skip_model_check)
    speaker_holder.append(app.speaker)
    app.reminders.check_due()  # deliver reminders missed while JARVIS was off
    app.reminders.start()
    try:
        run_terminal(app)
    finally:
        app.shutdown()


if __name__ == "__main__":
    main()
