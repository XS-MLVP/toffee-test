"""Desktop protocol registration and direct native coverage application launching."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess

from .utils.code_coverage.native import launch_native, register_native, state_dir


def main(argv=None):
    """Register a local application handler or dispatch a native report link."""
    parser = argparse.ArgumentParser(prog="toffee-test")
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register-native", help="Install the local desktop application handler")
    register.add_argument("--viewer-config", type=Path,
                          help="JSON mapping viewer IDs to argv arrays; default: Verdi")
    launch = commands.add_parser("native-open", help="Open a registered native coverage URI")
    launch.add_argument("uri")
    args = parser.parse_args(argv)
    try:
        if args.command == "register-native":
            viewers = json.loads(args.viewer_config.read_text()) if args.viewer_config else None
            desktop = register_native(viewers)
            print(f"Registered native coverage handler: {desktop}")
            print("Open your native report's index.html and click the application link.")
        else:
            result = launch_native(args.uri)
            print(f"Application started (PID {result['pid']}). Log: {result['log']}")
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        if args.command == "native-open":
            # The desktop handler has no console; retain diagnostics and notify the user.
            state_dir().mkdir(parents=True, exist_ok=True, mode=0o700)
            log = state_dir() / "last-error.txt"
            log.write_text(str(error) + "\n", encoding="utf-8")
            notify = shutil.which("notify-send")
            if notify:
                try:
                    subprocess.run([notify, "Toffee Coverage", f"{error}\nDetails: {log}"],
                                   timeout=5, check=False, capture_output=True)
                except (OSError, subprocess.SubprocessError):
                    pass
        parser.error(str(error))


if __name__ == "__main__":
    main()
