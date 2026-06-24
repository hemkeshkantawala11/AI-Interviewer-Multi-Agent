"""Text-only CLI driver for quick local testing.

The real product UI is the web app — run `py server.py` and open the browser.
This CLI just exercises the LangGraph end-to-end in the terminal.

    py run_cli.py            # type answers
    py run_cli.py --auto     # scripted, no typing
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))
try:  # Windows consoles default to cp1252 and can't encode emoji
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from interviewer.graph.build import build_graph, resume, start  # noqa: E402
from interviewer.graph.state import new_session  # noqa: E402

AUTO = iter([
    "1/3 because two of six outcomes exceed 4.",
    "Because the favourable faces are 5 and 6, two out of six.",
    "350, using 7C3 times 5C2.",
    "I selected the men and women separately, then multiplied.",
    "My best attempt is as above.",
])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Demo Student")
    ap.add_argument("--email", default="student@example.com")
    ap.add_argument("--auto", action="store_true")
    args = ap.parse_args()

    app = build_graph()
    sid = "session_cli_demo"
    cfg = {"configurable": {"thread_id": sid}}

    def emit(msgs):
        for m in msgs or []:
            print(f"\n🤖 {m}")

    def get_text() -> str:
        if args.auto:
            t = next(AUTO, "My best attempt.")
            print(f"\n🧑 (auto) {t}")
            return t
        return input("\n🧑 You: ")

    step = start(app, new_session(args.name, args.email, sid), cfg)
    while True:
        emit(step["payload"].get("outbox"))
        if step["done"]:
            print(f"\n=== Interview complete — {step['state'].get('status')} | report saved in ./reports/ ===")
            break
        step = resume(app, get_text(), cfg)


if __name__ == "__main__":
    main()
