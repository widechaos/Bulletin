import tkinter as tk
from tkinter import ttk, colorchooser, messagebox, font
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
import configparser
import os
import sys

# 应用 nest_asyncio 以允许在运行的事件循环中使用嵌套事件循环
nest_asyncio.apply()

# 读取配置文件
config = configparser.ConfigParser()

# 配置文件路径
config_file_path = 'config.ini'

# 检查配置文件是否存在
if not os.path.exists(config_file_path):
    # 创建默认配置文件
    config['telegram'] = {'bot_token': 'YOUR_TELEGRAM_BOT_TOKEN'}
    config['settings'] = {
        'font_size': '24',
        'font_color': 'white',
        'font_family': 'Arial',
        'opacity': '0.8',
        'scroll_direction': 'right-to-left'
    }
    with open(config_file_path, 'w') as configfile:
        config.write(configfile)

# 读取配置文件
config.read(config_file_path)

# 使用你的真实机器人 API Token
bot_token = config['telegram']['bot_token']

# 创建一个全局列表来存储消息
messages = []

# 弹幕窗口类
class DanmakuWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Telegram Danmaku")
        self.geometry("800x200")
        self.configure(bg="black")
        self.attributes("-topmost", True)  # 窗口置顶
        self.attributes('-alpha', float(config['settings']['opacity']))  # 设置透明度

        # 窗口边框可调整大小
        self.resizable(True, True)

        # 可拖动窗口
        self.bind('<Button-1>', self.start_move)
        self.bind('<B1-Motion>', self.do_move)

        # 右键菜单
        self.bind("<Button-3>", self.show_context_menu)

        # 创建标签来显示弹幕
        self.label = tk.Label(self, text="", fg=config['settings']['font_color'],
                              bg="black", font=(config['settings']['font_family'], int(config['settings']['font_size'])))
        self.label.pack(fill=tk.BOTH, expand=True)

        # 初始位置
        self.label_x_position = self.winfo_width()

        # 启动弹幕线程
        self.update_danmaku()

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        x = self.winfo_x() + event.x - self.x
        y = self.winfo_y() + event.y - self.y
        self.geometry(f"+{x}+{y}")

    def show_context_menu(self, event):
        # 创建右键菜单
        context_menu = tk.Menu(self, tearoff=0)
        context_menu.add_command(label="配置", command=self.open_settings_window)
        context_menu.add_command(label="退出", command=self.quit_application)
        context_menu.post(event.x_root, event.y_root)

    def open_settings_window(self):
        # 创建配置窗口
        settings_window = tk.Toplevel(self)
        settings_window.title("配置")
        settings_window.geometry("400x400")
        settings_window.configure(bg="black")

        # 字体大小
        font_size_var = tk.IntVar(value=int(config['settings']['font_size']))
        tk.Label(settings_window, text="字体大小:", fg="white", bg="black").pack(anchor=tk.W)
        tk.Entry(settings_window, textvariable=font_size_var, width=5).pack(anchor=tk.W, padx=10, pady=5)

        # 字体颜色
        font_color_var = tk.StringVar(value=config['settings']['font_color'])
        tk.Label(settings_window, text="字体颜色:", fg="white", bg="black").pack(anchor=tk.W)

        def choose_color():
            color_code = colorchooser.askcolor(title="选择颜色")[1]
            if color_code:
                font_color_var.set(color_code)

        tk.Button(settings_window, text="选择颜色", command=choose_color).pack(anchor=tk.W, padx=10, pady=5)

        # 透明度
        opacity_var = tk.DoubleVar(value=float(config['settings']['opacity']))
        tk.Label(settings_window, text="透明度:", fg="white", bg="black").pack(anchor=tk.W)
        tk.Scale(settings_window, variable=opacity_var, from_=0.1, to=1.0, resolution=0.1, orient=tk.HORIZONTAL,
                 bg="black", fg="white").pack(anchor=tk.W, padx=10, pady=5)

        # 滚动方向
        scroll_direction_var = tk.StringVar(value=config['settings']['scroll_direction'])
        tk.Label(settings_window, text="滚动方向:", fg="white", bg="black").pack(anchor=tk.W)
        ttk.Combobox(settings_window, textvariable=scroll_direction_var, values=[
                     "right-to-left", "left-to-right", "top-to-bottom", "bottom-to-top"]).pack(anchor=tk.W, padx=10, pady=5)

        # 字体系列
        font_family_var = tk.StringVar(value=config['settings']['font_family'])
        tk.Label(settings_window, text="字体系列:", fg="white", bg="black").pack(anchor=tk.W)
        font_families = list(font.families())
        font_family_combo = ttk.Combobox(settings_window, textvariable=font_family_var, values=font_families)
        font_family_combo.pack(anchor=tk.W, padx=10, pady=5)

        # 保存设置按钮
        def save_settings():
            config['settings']['font_size'] = str(font_size_var.get())
            config['settings']['font_color'] = font_color_var.get()
            config['settings']['opacity'] = str(opacity_var.get())
            config['settings']['scroll_direction'] = scroll_direction_var.get()
            config['settings']['font_family'] = font_family_var.get()
            with open(config_file_path, 'w') as configfile:
                config.write(configfile)
            messagebox.showinfo("信息", "设置已保存。")
            self.update_settings()
            settings_window.destroy()

        tk.Button(settings_window, text="保存设置", command=save_settings).pack(anchor=tk.W, padx=10, pady=10)

    def update_settings(self):
        # 更新当前窗口设置
        self.label.config(font=(config['settings']['font_family'], int(config['settings']['font_size'])),
                          fg=config['settings']['font_color'])
        self.attributes('-alpha', float(config['settings']['opacity']))

    def quit_application(self):
        self.destroy()
        sys.exit(0)  # 确保退出应用程序

    def update_danmaku(self):
        if messages:
            # 从队列中获取消息
            message = messages.pop(0)
            self.label.config(text=message)

            # 重置起始位置
            self.label_x_position = self.winfo_width()

        # 更新文本位置
        direction = config['settings']['scroll_direction']
        if direction == "right-to-left":
            if self.label_x_position > -self.label.winfo_width():
                self.label_x_position -= 5
                self.label.place(x=self.label_x_position, y=self.winfo_height() // 2)
        elif direction == "left-to-right":
            if self.label_x_position < self.winfo_width():
                self.label_x_position += 5
                self.label.place(x=self.label_x_position, y=self.winfo_height() // 2)
        elif direction == "top-to-bottom":
            if self.label.winfo_y() < self.winfo_height():
                self.label.place(x=(self.winfo_width() - self.label.winfo_width()) // 2,
                                 y=self.label.winfo_y() + 5)
            else:
                self.label.place(x=(self.winfo_width() - self.label.winfo_width()) // 2, y=-self.label.winfo_height())
        elif direction == "bottom-to-top":
            if self.label.winfo_y() > -self.label.winfo_height():
                self.label.place(x=(self.winfo_width() - self.label.winfo_width()) // 2,
                                 y=self.label.winfo_y() - 5)
            else:
                self.label.place(x=(self.winfo_width() - self.label.winfo_width()) // 2, y=self.winfo_height())

        # 定时调用自身
        self.after(50, self.update_danmaku)

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
    threading.Thread(target=start_asyncio_loop, daemon=True).start()

    # 运行桌面弹幕应用
    run_danmaku_app()
