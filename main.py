import threading
import nest_asyncio
import asyncio
from config_manager import load_config
from danmaku_window import DanmakuWindow
from telegram_bot import run_telegram_bot

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

def run_danmaku_app():
    # Run the desktop danmaku application
    app = DanmakuWindow()
    app.mainloop()

def start_asyncio_loop():
    # Use existing event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_telegram_bot())

if __name__ == '__main__':
    # Load configuration
    config = load_config()

    # Start Telegram bot event loop in a separate thread
    threading.Thread(target=start_asyncio_loop, daemon=True).start()

    # Run the desktop danmaku application
    run_danmaku_app()
