"""Entry point for the Anketka collector and Telegram bot."""
import os
import sys

from collector.rustore import main as collector
from collector.faq import main as faq
from bot.anketka import main as bot
from bot.anketka import send_faq_report
from web.main import main as web

def faq_report(_argv):
    count = send_faq_report()
    print(f"[faq] admin report sent to {count} chat(s)", flush=True)
    return 0


BLOCKS = {"collector": collector, "faq": faq, "faq-report": faq_report, "bot": lambda _argv: bot(), "web": web}

if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("RAILWAY_SERVICE_NAME", "")
    if name not in BLOCKS:
        sys.exit(f"unknown block '{name}', expected one of: {', '.join(BLOCKS)}")
    sys.exit(BLOCKS[name](sys.argv[2:]))
