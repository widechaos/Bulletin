import tkinter as tk
from tkinter import ttk, colorchooser, messagebox, font
import sys
from config_manager import config, save_config
from shared_data import messages  # Import the shared messages list

class DanmakuWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Telegram Danmaku")
        self.geometry("800x200")
        self.configure(bg="black")
        self.attributes("-topmost", True)  # Window always on top
        self.attributes('-alpha', float(config['settings']['opacity']))  # Set transparency

        # Hide the title bar and enable resizing
        self.overrideredirect(True)
        self.resizable(True, True)  # Allow resizing

        # Enable window dragging and resizing
        self.bind('<Button-1>', self.start_move)
        self.bind('<B1-Motion>', self.do_move)
        self.bind('<Button-3>', self.show_context_menu)  # Right-click context menu

        # Create a label to display messages
        self.label = tk.Label(self, text="", fg=config['settings']['font_color'],
                              bg="black", font=(config['settings']['font_family'], int(config['settings']['font_size'])))
        self.label.pack(fill=tk.BOTH, expand=True)

        # Initial position
        self.label_x_position = self.winfo_width()

        # Start the update thread
        self.update_danmaku()

    def start_move(self, event):
        self.x = event.x
        self.y = event.y

    def do_move(self, event):
        x = self.winfo_x() + event.x - self.x
        y = self.winfo_y() + event.y - self.y
        self.geometry(f"+{x}+{y}")

    def show_context_menu(self, event):
        # Create a right-click menu
        context_menu = tk.Menu(self, tearoff=0)
        context_menu.add_command(label="配置", command=self.open_settings_window)
        context_menu.add_command(label="退出", command=self.quit_application)
        context_menu.post(event.x_root, event.y_root)

    def open_settings_window(self):
        # Create a settings window
        settings_window = tk.Toplevel(self)
        settings_window.title("配置")
        settings_window.geometry("400x400")
        settings_window.configure(bg="black")

        # Font size
        font_size_var = tk.IntVar(value=int(config['settings']['font_size']))
        tk.Label(settings_window, text="字体大小:", fg="white", bg="black").pack(anchor=tk.W)
        tk.Entry(settings_window, textvariable=font_size_var, width=5).pack(anchor=tk.W, padx=10, pady=5)

        # Font color
        font_color_var = tk.StringVar(value=config['settings']['font_color'])
        tk.Label(settings_window, text="字体颜色:", fg="white", bg="black").pack(anchor=tk.W)

        def choose_color():
            color_code = colorchooser.askcolor(title="选择颜色")[1]
            if color_code:
                font_color_var.set(color_code)

        tk.Button(settings_window, text="选择颜色", command=choose_color).pack(anchor=tk.W, padx=10, pady=5)

        # Opacity
        opacity_var = tk.DoubleVar(value=float(config['settings']['opacity']))
        tk.Label(settings_window, text="透明度:", fg="white", bg="black").pack(anchor=tk.W)
        tk.Scale(settings_window, variable=opacity_var, from_=0.1, to=1.0, resolution=0.1, orient=tk.HORIZONTAL,
                 bg="black", fg="white").pack(anchor=tk.W, padx=10, pady=5)

        # Scroll direction
        scroll_direction_var = tk.StringVar(value=config['settings']['scroll_direction'])
        tk.Label(settings_window, text="滚动方向:", fg="white", bg="black").pack(anchor=tk.W)
        ttk.Combobox(settings_window, textvariable=scroll_direction_var, values=[
                     "right-to-left", "left-to-right", "top-to-bottom", "bottom-to-top"]).pack(anchor=tk.W, padx=10, pady=5)

        # Font family
        font_family_var = tk.StringVar(value=config['settings']['font_family'])
        tk.Label(settings_window, text="字体系列:", fg="white", bg="black").pack(anchor=tk.W)
        font_families = list(font.families())
        font_family_combo = ttk.Combobox(settings_window, textvariable=font_family_var, values=font_families)
        font_family_combo.pack(anchor=tk.W, padx=10, pady=5)

        # Save settings button
        def save_settings():
            config['settings']['font_size'] = str(font_size_var.get())
            config['settings']['font_color'] = font_color_var.get()
            config['settings']['opacity'] = str(opacity_var.get())
            config['settings']['scroll_direction'] = scroll_direction_var.get()
            config['settings']['font_family'] = font_family_var.get()
            save_config(config)
            messagebox.showinfo("信息", "设置已保存。")
            self.update_settings()
            settings_window.destroy()

        tk.Button(settings_window, text="保存设置", command=save_settings).pack(anchor=tk.W, padx=10, pady=10)

    def update_settings(self):
        # Update window settings
        self.label.config(font=(config['settings']['font_family'], int(config['settings']['font_size'])),
                          fg=config['settings']['font_color'])
        self.attributes('-alpha', float(config['settings']['opacity']))

    def quit_application(self):
        self.destroy()
        sys.exit(0)  # Ensure application exits

    def update_danmaku(self):
        if messages:
            # Get message from queue
            message = messages.pop(0)
            self.label.config(text=message)

            # Reset start position
            self.label_x_position = self.winfo_width()

        # Update text position
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

        # Schedule self-calling
        self.after(50, self.update_danmaku)
