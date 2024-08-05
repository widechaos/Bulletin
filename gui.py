import tkinter as tk
import asyncio
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

bot_token = 'YOUR_BOT_TOKEN'
messages = []

class DanmakuWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Telegram Danmaku")
        self.geometry("800x200")
        self.configure(bg="black")
        self.overrideredirect(False)  # 显示边框以便调试
        self.attributes("-topmost", True)  # 窗口置顶

        self.label = tk.Label(self, text="", fg="white", bg="black", font=("Arial", 24))
        self.label.pack()

        self.update_danmaku()

    def update_danmaku(self):
        if messages:
            message = messages.pop(0)
            self.label.config(text=message)
        self.after(2000, self.update_danmaku)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('Bot is running!')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        messages.append(update.message.text)
        print(f"Received message: {update.message.text}")
    else:
        print("No message received.")

async def run_telegram_bot():
    application = ApplicationBuilder().token(bot_token).build()

    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    await application.run_polling()

def run_danmaku_app():
    app = DanmakuWindow()
    app.mainloop()

async def main():
    task = asyncio.create_task(run_telegram_bot())
    run_danmaku_app()
    await task

if __name__ == '__main__':
    asyncio.run(main())
