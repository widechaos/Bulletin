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
import threading
import nest_asyncio
import configparser  # Import the configparser module

# 应用 nest_asyncio 以允许在运行的事件循环中使用嵌套事件循环
nest_asyncio.apply()

# 读取配置文件
config = configparser.ConfigParser()
config.read('config.ini')

# 使用你的真实机器人 API Token
bot_token = config['telegram']['bot_token']  # 从配置文件中读取 Token

# 创建一个全局列表来存储消息
messages = []

# 弹幕窗口类
class DanmakuWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Telegram Danmaku")
        self.geometry("800x200")
        self.configure(bg="black")
        self.overrideredirect(True)  # 移除窗口边框
        self.attributes("-topmost", True)  # 窗口置顶
        self.attributes('-alpha', 0.8)  # 设置透明度

        # 创建标签来显示弹幕
        self.label = tk.Label(self, text="", fg="white", bg="black", font=("Arial", 24))
        self.label.pack()

        # 初始位置
        self.label_x_position = 800

        # 设置面板
        self.settings_frame = tk.Frame(self, bg="black")
        self.font_size_var = tk.IntVar(value=24)
        self.font_color_var = tk.StringVar(value="white")

        # 创建字体大小设置
        tk.Label(self.settings_frame, text="字体大小:", fg="white", bg="black").pack(side=tk.LEFT)
        tk.Entry(self.settings_frame, textvariable=self.font_size_var, width=3).pack(side=tk.LEFT)

        # 创建字体颜色设置
        tk.Label(self.settings_frame, text="字体颜色:", fg="white", bg="black").pack(side=tk.LEFT)
        tk.Entry(self.settings_frame, textvariable=self.font_color_var, width=10).pack(side=tk.LEFT)

        # 添加鼠标事件
        self.bind("<Enter>", self.show_settings)
        self.bind("<Leave>", self.hide_settings)

        # 启动弹幕线程
        self.update_danmaku()

    def show_settings(self, event):
        self.settings_frame.pack()

    def hide_settings(self, event):
        self.settings_frame.pack_forget()

    def update_danmaku(self):
        if messages:
            # 从队列中获取消息
            message = messages.pop(0)
            self.label.config(text=message)

            # 获取用户设置
            font_size = self.font_size_var.get()
            font_color = self.font_color_var.get()
            self.label.config(font=("Arial", font_size), fg=font_color)

            # 重置起始位置
            self.label_x_position = 800

        # 更新文本位置
        if self.label_x_position > -self.label.winfo_width():
            self.label_x_position -= 5
            self.label.place(x=self.label_x_position, y=80)  # 在窗口中水平居中显示
        else:
            print("No messages to display.")

        # 定时调用自身
        self.after(50, self.update_danmaku)  # 每50毫秒更新一次位置

# Telegram 消息处理函数
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('欢迎使用桌面弹幕机器人！发送任意消息以查看弹幕效果。')

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        message_text = update.message.text
        messages.append(message_text)
        print(f"Received message: {message_text}")  # 打印接收到的消息
        print(f"Messages queue: {messages}")        # 打印当前消息队列
    else:
        print("No message received.")

async def run_telegram_bot():
    # 创建 Application 对象并传递 Token
    application = ApplicationBuilder().token(bot_token).build()

    # 添加命令处理程序
    application.add_handler(CommandHandler('start', start))

    # 添加消息处理程序
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    # 启动机器人
    await application.run_polling()

def run_danmaku_app():
    # 运行桌面弹幕应用
    app = DanmakuWindow()
    app.mainloop()

def start_asyncio_loop():
    # 使用现有事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(run_telegram_bot())

if __name__ == '__main__':
    # 在独立线程中启动 Telegram 机器人事件循环
    threading.Thread(target=start_asyncio_loop).start()

    # 运行桌面弹幕应用
    run_danmaku_app()
