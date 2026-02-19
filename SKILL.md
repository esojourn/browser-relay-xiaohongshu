---
name: browser-relay
description: 通过 HTTP relay 控制用户本地 Chromium 浏览器（绕过数据中心 IP 封锁），支持截图发送到 Telegram
allowed_tools:
  - exec
  - session_state
  - memory_search
---


# Browser Relay Skill

通过 HTTP relay 控制用户本地 Chromium，用于操作会封锁数据中心 IP 的网站（如小红书）。

## 架构

```
AI → HTTP (port 18792) → relay.py → CDP (port 9222) → 用户本地 Chromium
```

所有请求从用户本地 IP 发出，绕过反爬。

## 文件位置

- Relay 代码: `/home/spot/browser-relay/relay.py`
- 启动脚本: `/home/spot/browser-relay/start.sh`
- Token 文件: `/tmp/browser-relay-token`
- PID 文件: `/tmp/browser-relay.pid`
- 日志: `/tmp/relay.log`
- 截图目录: `/home/spot/browser-relay/screenshots/`

## 启动流程

每次会话开始使用 relay 前，按以下步骤操作：

### 1. 检查 Chromium 是否运行

```bash
curl -s http://127.0.0.1:9222/json/version
```

如果连接失败，说明 Chromium 未启动，需要启动它（见下方"启动 Chromium"）。

### 2. 启动 Chromium（如果未运行）

```bash
DISPLAY=:0 nohup chromium --remote-debugging-port=9222 --remote-allow-origins=* > /dev/null 2>&1 &
```

注意事项：
- 必须加 `--remote-allow-origins=*` 否则 WebSocket 连接会被拒绝
- 必须加 `DISPLAY=:0`（或对应的显示环境变量）以在用户桌面上显示
- Puppy Linux 环境，chromium 命令名可能是 `chromium` 或 `chromium-browser`，按实际情况调整
- 启动后等待几秒再继续操作

### 3. 检查 relay 状态

```bash
curl -s http://127.0.0.1:18792/health
```

如果返回 `{"status":"ok"}`，跳到步骤 5。否则继续。

### 4. 启动 relay

```bash
bash /home/spot/browser-relay/start.sh
```

### 5. 获取并缓存 token

```bash
cat /tmp/browser-relay-token
```

读取后用 `session_state` 缓存到当前会话：

```
session_state set namespace=browser-relay key=token value=<token>
```

后续所有请求从 session_state 读取 token，不重复读文件。

## API 调用模板

所有请求都需要 `Authorization: Bearer <token>` 头。

### 获取标签页列表
```bash
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18792/tabs
```

### 导航
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url":"https://www.xiaohongshu.com"}' \
  http://127.0.0.1:18792/navigate
```

### 截图（带自动保存）
```bash
TOKEN=$(cat /tmp/browser-relay-token)
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"quality":70}' http://127.0.0.1:18792/screenshot \
  | python3 -c "
import sys, json, base64, os
from datetime import datetime
data = json.load(sys.stdin)
if data.get('ok'):
    os.makedirs('/home/spot/browser-relay/screenshots', exist_ok=True)
    fname = datetime.now().strftime('%Y%m%d_%H%M%S') + '.jpg'
    path = f'/home/spot/browser-relay/screenshots/{fname}'
    with open(path, 'wb') as f:
        f.write(base64.b64decode(data['data']))
    print(f'saved:{path} size:{os.path.getsize(path)}')
else:
    print(f'error:{data}')
"
```

### 截图并发送到 Telegram

当用户在 Telegram 上交互时，截图需要发送到 TG 才能看到。流程：

```bash
# 1. 截图并保存
TOKEN=$(cat /tmp/browser-relay-token)
SCREENSHOT_PATH="/tmp/relay_screenshot.png"
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"quality":80}' http://127.0.0.1:18792/screenshot \
  | python3 -c "
import sys, json, base64
data = json.load(sys.stdin)
if data.get('ok'):
    with open('$SCREENSHOT_PATH', 'wb') as f:
        f.write(base64.b64decode(data['data']))
    print('ok')
else:
    print(f'error:{data}')
"

# 2. 通过 Telegram Bot API 发送图片
TG_BOT_TOKEN="<从 session_state 或 memory 获取>"
TG_CHAT_ID="<从 session_state 或 memory 获取>"
curl -s -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendPhoto" \
  -F "chat_id=${TG_CHAT_ID}" \
  -F "photo=@${SCREENSHOT_PATH}"
```

Telegram 配置：
- Bot Token 和 Chat ID 从 memory 或 session_state 获取
- Chat ID 可从 session 信息中提取（`telegram:alzmoltis_bot:<chat_id>`）
- 发送前确保截图文件存在且非空

### 点击（坐标）
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"x":100,"y":200}' http://127.0.0.1:18792/click
```

### 点击（CSS 选择器）
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"selector":"button.submit"}' http://127.0.0.1:18792/click
```

### 输入文字
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text":"要输入的内容"}' http://127.0.0.1:18792/type
```

### 按键
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"key":"Enter"}' http://127.0.0.1:18792/keypress
```

### 滚动
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"y":300}' http://127.0.0.1:18792/scroll
```

### 执行 JS
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"expression":"document.title"}' http://127.0.0.1:18792/evaluate
```

### 等待元素出现
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"selector":".target-element","timeout":5000}' http://127.0.0.1:18792/wait
```

### 标签页管理
```bash
# 新建标签页
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"url":"https://example.com"}' http://127.0.0.1:18792/tab/new

# 切换标签页
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tab_id":"xxx"}' http://127.0.0.1:18792/tab/activate

# 关闭标签页
curl -s -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"tab_id":"xxx"}' http://127.0.0.1:18792/tab/close
```

## 标准操作规范

1. **截图确认制**：任何发布/提交操作前，必须先截图让用户确认
2. **截图自动保存**：所有截图保存到 `/home/spot/browser-relay/screenshots/` 并带时间戳
3. **Telegram 截图发送**：当用户在 TG 上交互时，截图通过 Telegram Bot API 发送，而不是仅保存到本地
4. **Token 会话缓存**：token 只读一次，缓存在 session_state 中
5. **错误重连**：如果请求失败（连接拒绝），自动尝试重启 relay
6. **操作间隔**：点击/输入操作之间加 `sleep 0.5~1` 模拟人类节奏

## 停止 relay

```bash
bash /home/spot/browser-relay/start.sh stop
```
