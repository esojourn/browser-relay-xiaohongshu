# Browser Relay for Xiaohongshu

[![GitHub](https://img.shields.io/badge/GitHub-browser--relay--xiaohongshu-blue)](https://github.com/esojourn/browser-relay-xiaohongshu)

Lightweight HTTP relay that lets AI assistants control a local Chromium browser via Chrome DevTools Protocol (CDP), bypassing data center IP blocks from Chinese platforms like Xiaohongshu (小红书).

## Why This Exists

AI assistants typically run on cloud servers. Chinese platforms aggressively block data center IPs. This relay bridges the gap — the AI sends HTTP commands to a local relay server, which forwards them to your Chromium via CDP. All web requests originate from your local IP.

## Architecture

```
AI Agent → HTTP (port 8787) → relay.py → CDP WebSocket (port 9222) → Local Chromium
```

## Quick Start

1. Launch Chromium with remote debugging:
```bash
chromium --remote-debugging-port=9222
```

2. Install dependencies and start relay:
```bash
cd browser-relay
python3 -m venv venv && source venv/bin/activate
pip install websockets
python3 relay.py
```

Or use the launcher script:
```bash
bash start.sh          # start
bash start.sh restart  # restart
bash start.sh stop     # stop
```

3. Use the API:
```bash
# Status check
curl http://localhost:8787/status

# List browser targets
curl http://localhost:8787/targets

# Navigate
curl -X POST -H "Content-Type: application/json" \
  -d '{"url":"https://www.xiaohongshu.com"}' http://localhost:8787/navigate

# Screenshot
curl -X POST http://localhost:8787/screenshot

# Click at coordinates
curl -X POST -H "Content-Type: application/json" \
  -d '{"x":400,"y":300}' http://localhost:8787/click

# Type text
curl -X POST -H "Content-Type: application/json" \
  -d '{"text":"hello"}' http://localhost:8787/type

# Execute JavaScript
curl -X POST -H "Content-Type: application/json" \
  -d '{"expression":"document.title"}' http://localhost:8787/eval

# Scroll
curl -X POST -H "Content-Type: application/json" \
  -d '{"x":400,"y":300,"deltaY":500}' http://localhost:8787/scroll
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/status` | GET/POST | Health check, connection status |
| `/targets` | GET/POST | List all browser targets/tabs |
| `/connect` | POST | Connect to a specific target by `wsUrl` |
| `/navigate` | POST | Navigate to URL (`{"url":"..."}`) |
| `/screenshot` | POST | Capture screenshot (jpeg/png, optional `fullPage`) |
| `/click` | POST | Click at coordinates (`{"x":N,"y":N}`) |
| `/type` | POST | Type text (`{"text":"..."}`) |
| `/key` | POST | Send key event (`{"key":"Enter"}`) |
| `/scroll` | POST | Scroll page (`{"deltaX":N,"deltaY":N}`) |
| `/eval` | POST | Execute JavaScript (`{"expression":"..."}`) |
| `/dom` | POST | Query DOM element (`{"selector":"..."}`) → returns center coordinates |
| `/html` | POST | Get element outer HTML (`{"selector":"..."}`) |

## Requirements

- Python 3.8+
- `websockets` (pip install websockets)
- Chromium / Chrome with `--remote-debugging-port=9222`

## File Structure

```
browser-relay/
├── relay.py       # Main relay server (asyncio + raw HTTP + CDP WebSocket)
├── start.sh       # Launcher script (start/stop/restart, CDP check)
├── venv/          # Python virtual environment
└── screenshots/   # Auto-saved screenshots (gitignored)
```

## Disclaimer

This tool is for personal automation and educational purposes. Respect platform terms of service. The authors are not responsible for any misuse.

---

# Browser Relay 小红书自动化中继（中文说明）

轻量级 HTTP 中继服务，让 AI 助手通过本地 Chromium 浏览器操作小红书等中国平台，绕过数据中心 IP 封锁。

## 为什么需要这个？

AI 助手通常运行在云服务器上，其 IP 会被小红书等平台的风控系统拦截（"网络环境异常"）。本中继让 AI 通过 HTTP 发送指令到本地 relay 服务，再通过 CDP 协议控制你的 Chromium 浏览器。所有网络请求都从你的本机 IP 发出，绕过封锁。

## 架构

```
AI 助手 → HTTP (端口 8787) → relay.py → CDP WebSocket (端口 9222) → 本地 Chromium
```

## 快速开始

1. 启动 Chromium（带远程调试端口）：
```bash
chromium --remote-debugging-port=9222
```

2. 安装依赖并启动：
```bash
cd browser-relay
python3 -m venv venv && source venv/bin/activate
pip install websockets
python3 relay.py
```

或使用启动脚本：
```bash
bash start.sh          # 启动
bash start.sh restart  # 重启
bash start.sh stop     # 停止
```

## API 端点

| 端点 | 说明 |
|------|------|
| `/status` | 健康检查 |
| `/targets` | 获取所有标签页 |
| `/navigate` | 导航到 URL |
| `/screenshot` | 截图 |
| `/click` | 点击坐标 |
| `/type` | 输入文本 |
| `/key` | 发送按键 |
| `/scroll` | 滚动页面 |
| `/eval` | 执行 JavaScript |
| `/dom` | 查询 DOM 元素坐标 |
| `/html` | 获取元素 HTML |

## 依赖

- Python 3.8+
- `websockets`
- Chromium（需启用 `--remote-debugging-port=9222`）

## 免责声明

本工具仅用于个人自动化和学习目的。请遵守平台使用条款。作者不对任何滥用行为负责。
