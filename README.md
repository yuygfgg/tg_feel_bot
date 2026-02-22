# tg_feel_bot

一个 Telegram 表情包机器人：把 `pic.jpeg` 的第一行文字替换成你输入的内容，第二行保留底图原字。

## 功能

- 群聊/私聊命令：`/feel 吃牛排` 或 `/feel@bot_name 吃牛排`
- Inline：在任意聊天输入 `@bot_name 吃牛排` 直接选择并发送图片

## 环境变量

复制一份示例配置：

```bash
cp .env.example .env
```

必须：

- `TELEGRAM_BOT_TOKEN`：BotFather 给的 token

可选：

- `FEEL_STICKER_SALT`：用于生成匿名的贴纸包名。建议设置为一段足够长的随机字符串，默认 `TELEGRAM_BOT_TOKEN` 的前 16 位
- `FEEL_FONT_PATH`：字体文件路径（需要支持中文）。默认 `./SweiGoticCJKsc-Bold.ttf`
- `FEEL_BASE_IMAGE`：底图路径，默认 `./pic.jpeg`
- `FEEL_MAX_TEXT_LEN`：输入文字长度上限，默认 `80`
- `FEEL_RENDER_CONCURRENCY`：同时渲染的并发数，默认 `2`

## 安装依赖

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

## 运行

```bash
source .venv/bin/activate
python3 -m feel_bot
```
