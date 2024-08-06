import tkinter as tk
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import threading
import nest_asyncio
import json
import os

# 应用 nest_asyncio 以允许在运行的事件循环中使用嵌套事件循环
nest_asyncio.apply()

# 使用你的真实机器人 API Token
bot_token = 'YOUR_BOT_TOKEN'  # 请在此替换为正确的 Token

# 配置文件路径
config_file = 'config.json'

# 默认配置
default_config = {
    "font_size": 24,
    "font_color": "white",
    "transparency": 0.8,
    "direction": "Right to Left"
}

# 创建一个全局列表来存储消息
messages = []


# 读取配置文件
def load_config():
    if os.path.exists(config_file):
        with open(config_file, 'r') as f:
            return json.load(f)
    else:
        return default_config


# 保存配置文件
def save_config(config):
    with open(config_file, 'w') as f:
        json.dump(config, f, indent=4)


# 弹幕窗口类
class DanmakuWindow(tk.Tk):
    def __init__(self):
        super().__init__()

        # 加载配置
        self.config_data = load_config()

        self.title("Telegram Danmaku")
        self.geometry("800x200")
        self.configure(bg="black")
        self.overrideredirect(True)  # 移除窗口边框
        self.attributes("-topmost", True)  # 窗口置顶
        self.attributes('-alpha', self.config_data['transparency'])  # 设置透明度

        # 允许调整窗口大小
        self.resizable(True, True)

        # 创建标签来显示弹幕
        self.label = tk.Label(self, text="", fg=self.config_data['font_color'], bg="black",
                              font=("Arial", self.config_data['font_size']))
        self.label.pack()

        # 初始位置
        self.label_x_position = self.winfo_width()

        # 添加窗口拖动功能
        self.bind("<ButtonPress-1>", self.start_move)
        self.bind("<B1-Motion>", self.do_move)
        self.bind("<Button-3>", self.open_settings)  # 右键打开设置窗口

        # 启动弹幕线程
        self.update_danmaku()

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        self.geometry(f"+{self.winfo_x() + deltax}+{self.winfo_y() + deltay}")

    def update_danmaku(self):
        if messages:
            # 从队列中获取消息
            message = messages.pop(0)
            self.label.config(text=message)
            self.label.update_idletasks()  # 强制更新显示

            # 重置起始位置
            self.label_x_position = self.winfo_width()

        # 更新文本位置和方向
        direction = self.config_data['direction']

        if direction == "Right to Left":
            self.label_x_position -= 5
            self.label.place(x=self.label_x_position, y=self.winfo_height() // 2 - self.label.winfo_height() // 2)
        elif direction == "Left to Right":
            self.label_x_position += 5
            self.label.place(x=self.label_x_position, y=self.winfo_height() // 2 - self.label.winfo_height() // 2)
        elif direction == "Top to Bottom":
            self.label.place(x=self.winfo_width() // 2 - self.label.winfo_width() // 2, y=self.label_x_position)
            self.label_x_position += 5
        elif direction == "Bottom to Top":
            self.label.place(x=self.winfo_width() // 2 - self.label.winfo_width() // 2, y=self.label_x_position)
            self.label_x_position -= 5

        # 检查是否需要重置位置
        if self.label_x_position < -self.label.winfo_width() or self.label_x_position > self.winfo_width() or self.label_x_position > self.winfo_height() or self.label_x_position < -self.label.winfo_height():
            self.label_x_position = self.winfo_width()

        # 定时调用自身
        self.after(50, self.update_danmaku)  # 每50毫秒更新一次位置

    def open_settings(self, event):
        # 创建设置窗口
        settings_window = tk.Toplevel(self)
        settings_window.title("设置")
        settings_window.geometry("300x400")
        settings_window.configure(bg="black")

        # 字体大小设置
        tk.Label(settings_window, text="字体大小:", fg="white", bg="black").pack()
        font_size_entry = tk.Entry(settings_window)
        font_size_entry.insert(0, self.config_data['font_size'])
        font_size_entry.pack()

        # 字体颜色设置
        tk.Label(settings_window, text="字体颜色:", fg="white", bg="black").pack()
        font_color_entry = tk.Entry(settings_window)
        font_color_entry.insert(0, self.config_data['font_color'])
        font_color_entry.pack()

        # 透明度设置
        tk.Label(settings_window, text="透明度:", fg="white", bg="black").pack()
        transparency_scale = tk.Scale(settings_window, from_=0.1, to=1.0, resolution=0.1, orient=tk.HORIZONTAL)
        transparency_scale.set(self.config_data['transparency'])
        transparency_scale.pack()

        # 滚动方向设置
        tk.Label(settings_window, text="滚动方向:", fg="white", bg="black").pack()
        direction_var = tk.StringVar(value=self.config_data['direction'])
        direction_options = ["Right to Left", "Left to Right", "Top to Bottom", "Bottom to Top"]
        direction_menu = tk.OptionMenu(settings_window, direction_var, *direction_options)
        direction_menu.pack()

        # 保存按钮
        save_button = tk.Button(settings_window, text="保存",
                                command=lambda: self.save_settings(settings_window, font_size_entry, font_color_entry,
                                                                   transparency_scale, direction_var))
        save_button.pack()

    def save_settings(self, window, font_size_entry, font_color_entry, transparency_scale, direction_var):
        # 更新配置
        self.config_data['font_size'] = int(font_size_entry.get())
        self.config_data['font_color'] = font_color_entry.get()
        self.config_data['transparency'] = transparency_scale.get()
        self.config_data['direction'] = direction_var.get()

        # 应用配置
        self.label.config(font=("Arial", self.config_data['font_size']), fg=self.config_data['font_color'])
        self.attributes('-alpha', self.config_data['transparency'])

        # 保存配置
        save_config(self.config_data)

        # 关闭设置窗口
        window.destroy()


# Telegram 消息处理函数
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text('欢迎使用桌面弹幕机器人！发送任意消息以查看弹幕效果。')


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        message_text = update.message.text
        messages.append(message_text)
        print(f"Received message: {message_text}")  # 打印接收到的消息
        print(f"Messages queue: {messages}")  # 打印当前消息队列
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
