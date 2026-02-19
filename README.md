# browser-relay-xiaohongshu

轻量级浏览器中继服务，让 AI 助手通过 HTTP API 控制本地 Chromium 浏览器，绕过数据中心 IP 封锁，实现小红书等中国平台的自动化操作。

## 架构

```
AI Agent → HTTP (port 18792) → Relay Server → CDP (port 9222) → 本地 Chromium
```

## 为什么需要这个？

AI 助手通常运行在数据中心，其 IP 会被小红书等平台的风控系统拦截。通过本地中继，所有请求都从用户本机 IP 发出，绕过封锁。

## 快速开始

1. 启动 Chromium（带远程调试端口）：
```bash
chromium --remote-debugging-port=9222
```

2. 启动 relay 服务：
```bash
bash start.sh
```

3. 使用 API：
```bash
# 获取 token
TOKEN=$(cat .token)

# 健康检查
curl -H "X-Auth-Token: $TOKEN" http://localhost:18792/health

# 获取标签页
curl -H "X-Auth-Token: $TOKEN" http://localhost:18792/tabs

# 导航
curl -X POST -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"url":"https://www.xiaohongshu.com"}' http://localhost:18792/navigate

# 截图
curl -H "X-Auth-Token: $TOKEN" http://localhost:18792/screenshot -o screenshot.jpg
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/tabs` | GET | 获取所有标签页 |
| `/navigate` | POST | 导航到 URL |
| `/click` | POST | 点击元素 |
| `/type` | POST | 输入文本 |
| `/evaluate` | POST | 执行 JavaScript |
| `/scroll` | POST | 滚动页面 |
| `/screenshot` | GET | 截图 |

## 依赖

- Python 3 + aiohttp
- Chromium（带 `--remote-debugging-port=9222`）

## 许可

MIT
