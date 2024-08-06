# Telegram 弹幕机器人

Telegram 弹幕机器人是一个 Python 项目，允许用户通过 Telegram 发送消息，这些消息会在桌面应用程序上以弹幕的形式显示。项目使用 `Tkinter` 作为 GUI 框架，并集成了 Telegram Bot API。

## 项目功能

- **消息弹幕**：通过 Telegram 机器人接收消息，并在桌面应用程序中滚动显示。
- **可配置界面**：用户可以自定义弹幕字体、颜色、透明度等设置。
- **无标题栏窗口**：提供可调整大小和拖动的无标题栏窗口。
- **跨平台支持**：支持 Windows、MacOS 和 Linux。

## 目录

- [安装说明](#安装说明)
- [使用方法](#使用方法)
- [配置文件](#配置文件)
- [常见问题](#常见问题)
- [打包为可执行文件](#打包为可执行文件)
- [贡献](#贡献)
- [许可](#许可)

## 安装说明

### 环境要求

- Python 3.7+
- `pip` 包管理器

### 安装步骤

1. **克隆项目**

   ```bash
   git clone https://github.com/yourusername/danmaku-bot.git
   cd danmaku-bot
   ```

2. **安装依赖**

   使用 `pip` 安装项目所需的依赖：

   ```bash
   pip install -r requirements.txt
   ```

   `requirements.txt` 文件内容示例：

   ```plaintext
   python-telegram-bot
   nest_asyncio
   ```

3. **配置 Telegram 机器人**

   - 前往 [Telegram](https://telegram.org/) 创建一个新的 Bot 并获取 API Token。
   - 在项目根目录下创建 `config.ini` 文件，并将你的 Telegram Bot Token 填入：

     ```ini
     [telegram]
     bot_token = YOUR_TELEGRAM_BOT_TOKEN

     [settings]
     font_size = 24
     font_color = white
     font_family = Arial
     opacity = 0.8
     scroll_direction = right-to-left
     ```

## 使用方法

### 启动程序

在项目目录下运行以下命令启动弹幕机器人：

```bash
python main.py
```

### 使用 Telegram 发送弹幕

1. 打开 Telegram，找到你的 Bot。
2. 发送任意文本消息至 Bot，消息将以弹幕的形式显示在桌面应用程序中。

### 界面操作

- **移动窗口**：按住鼠标左键可以拖动窗口。
- **调整大小**：拖动窗口边缘可调整窗口大小。
- **右键菜单**：右键点击窗口可以打开配置菜单或退出程序。

## 配置文件

配置文件 `config.ini` 用于存储界面设置：

- **font_size**：弹幕字体大小。
- **font_color**：弹幕字体颜色。
- **font_family**：弹幕字体系列。
- **opacity**：窗口透明度（0.1 到 1.0）。
- **scroll_direction**：弹幕滚动方向。

## 常见问题

### 1. 消息不显示？

- 确保你已经在 `config.ini` 文件中正确配置了 Telegram Bot Token。
- 确保机器人已启动并能够接收消息。

### 2. 如何调整窗口大小？

- 确保窗口边缘能够响应鼠标事件。如果调整大小功能异常，请检查代码中 `start_resize` 和 `do_resize` 方法的实现。

### 3. PyInstaller 打包后的程序无法运行？

- 确保所有依赖项已正确安装，并在打包时添加 `--onefile --windowed` 选项。
- 检查是否需要手动添加 DLL 或其他动态链接库。

## 打包为可执行文件

### 使用 PyInstaller 打包

在项目目录下运行以下命令：

```bash
pyinstaller --onefile --windowed main.py
```

生成的可执行文件位于 `dist/` 目录中，直接运行该文件即可启动程序。

## 贡献

欢迎提出问题、建议或贡献代码！请创建 Issue 或提交 Pull Request。

## 许可

此项目基于 MIT 许可证发布。有关详细信息，请参阅 [LICENSE](LICENSE) 文件。

