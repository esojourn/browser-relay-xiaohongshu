<<<<<<< HEAD
from pathlib import Path
#!/usr/bin/env python3
"""
Browser Relay - HTTP API to control Chromium via CDP (Chrome DevTools Protocol)
Connects to Chromium's remote debugging port and exposes simple HTTP endpoints.
"""

import asyncio
import base64
import json
import secrets
import sys
from urllib.parse import urljoin

import aiohttp
from aiohttp import web, ClientSession, WSMsgType

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222
RELAY_HOST = "127.0.0.1"
RELAY_PORT = 18792
AUTH_TOKEN = secrets.token_urlsafe(32)

# Active CDP websocket connections per tab
_ws_connections: dict[str, aiohttp.ClientWebSocketResponse] = {}
_http_session: ClientSession | None = None
_cmd_id = 0


def next_id():
    global _cmd_id
    _cmd_id += 1
    return _cmd_id


async def get_http_session():
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = ClientSession()
    return _http_session


async def get_tabs():
    session = await get_http_session()
    async with session.get(f"http://{CDP_HOST}:{CDP_PORT}/json") as resp:
        tabs = await resp.json()
    return [t for t in tabs if t.get("type") == "page"]


async def get_ws(tab_id: str | None = None):
    """Get or create a CDP websocket for a tab. If tab_id is None, use first tab."""
    if tab_id and tab_id in _ws_connections:
        ws = _ws_connections[tab_id]
        if not ws.closed:
            return ws, tab_id

    tabs = await get_tabs()
    if not tabs:
        raise Exception("No browser tabs found")

    if tab_id:
        tab = next((t for t in tabs if t["id"] == tab_id), None)
        if not tab:
            raise Exception(f"Tab {tab_id} not found")
    else:
        tab = tabs[0]
        tab_id = tab["id"]

    ws_url = tab.get("webSocketDebuggerUrl")
    if not ws_url:
        raise Exception(f"No websocket URL for tab {tab_id}")

    session = await get_http_session()
    ws = await session.ws_connect(ws_url, max_msg_size=50 * 1024 * 1024)
    _ws_connections[tab_id] = ws
    return ws, tab_id


async def cdp_send(method: str, params: dict = None, tab_id: str = None, timeout: float = 30):
    """Send a CDP command and wait for result."""
    ws, tab_id = await get_ws(tab_id)
    msg_id = next_id()
    payload = {"id": msg_id, "method": method}
    if params:
        payload["params"] = params

    await ws.send_json(payload)

    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"CDP command {method} timed out")
        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=remaining)
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(f"CDP command {method} timed out")

        if msg.type == WSMsgType.TEXT:
            data = json.loads(msg.data)
            if data.get("id") == msg_id:
                if "error" in data:
                    raise Exception(data["error"].get("message", str(data["error"])))
                return data.get("result", {})
        elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
            _ws_connections.pop(tab_id, None)
            raise Exception("WebSocket closed")


# --- Auth middleware ---

@web.middleware
async def auth_middleware(request, handler):
    if request.path == "/health":
        return await handler(request)
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if token != AUTH_TOKEN:
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


# --- Route handlers ---

async def handle_health(request):
    try:
        tabs = await get_tabs()
        return web.json_response({"status": "ok", "tabs": len(tabs)})
    except Exception:
        return web.json_response({"status": "no_browser"}, status=503)


async def handle_tabs(request):
    tabs = await get_tabs()
    return web.json_response([
        {"id": t["id"], "title": t.get("title", ""), "url": t.get("url", "")}
        for t in tabs
    ])


async def handle_navigate(request):
    body = await request.json()
    url = body["url"]
    tab_id = body.get("tab_id")
    result = await cdp_send("Page.navigate", {"url": url}, tab_id)
    # Wait for load
    await cdp_send("Page.enable", tab_id=tab_id)
    try:
        await cdp_send("Runtime.evaluate", {
            "expression": "new Promise(r => { if (document.readyState === 'complete') r(); else window.addEventListener('load', r); })",
            "awaitPromise": True
        }, tab_id, timeout=15)
    except Exception:
        pass
    return web.json_response({"ok": True, "frameId": result.get("frameId")})


async def handle_screenshot(request):
    body = await request.json() if request.can_read_body else {}
    tab_id = body.get("tab_id") if body else None
    quality = body.get("quality", 70) if body else 70
    fmt = body.get("format", "jpeg") if body else "jpeg"

    params = {"format": fmt}
    if fmt == "jpeg":
        params["quality"] = quality

    result = await cdp_send("Page.captureScreenshot", params, tab_id)
    return web.json_response({"ok": True, "data": result["data"], "format": fmt})


async def handle_click(request):
    body = await request.json()
    tab_id = body.get("tab_id")

    if "selector" in body:
        # Find element by selector, get its center coordinates
        js = f"""
        (() => {{
            const el = document.querySelector({json.dumps(body['selector'])});
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return {{x: r.x + r.width/2, y: r.y + r.height/2}};
        }})()
        """
        res = await cdp_send("Runtime.evaluate", {"expression": js, "returnByValue": True}, tab_id)
        val = res.get("result", {}).get("value")
        if not val:
            return web.json_response({"error": "element not found"}, status=404)
        x, y = val["x"], val["y"]
    else:
        x, y = body["x"], body["y"]

    for etype in ["mousePressed", "mouseReleased"]:
        await cdp_send("Input.dispatchMouseEvent", {
            "type": etype, "x": x, "y": y, "button": "left", "clickCount": 1
        }, tab_id)

    return web.json_response({"ok": True, "x": x, "y": y})


async def handle_type(request):
    body = await request.json()
    text = body["text"]
    tab_id = body.get("tab_id")

    if body.get("selector"):
        await handle_click(request)
        await asyncio.sleep(0.1)

    for char in text:
        await cdp_send("Input.dispatchKeyEvent", {
            "type": "keyDown", "text": char, "key": char, "unmodifiedText": char
        }, tab_id)
        await cdp_send("Input.dispatchKeyEvent", {"type": "keyUp", "key": char}, tab_id)

    return web.json_response({"ok": True, "length": len(text)})


async def handle_evaluate(request):
    body = await request.json()
    expression = body["expression"]
    tab_id = body.get("tab_id")
    await_promise = body.get("await_promise", False)

    params = {"expression": expression, "returnByValue": True}
    if await_promise:
        params["awaitPromise"] = True

    result = await cdp_send("Runtime.evaluate", params, tab_id)
    r = result.get("result", {})

    if r.get("subtype") == "error" or result.get("exceptionDetails"):
        return web.json_response({
            "error": result.get("exceptionDetails", {}).get("text", str(r))
        }, status=400)

    return web.json_response({"ok": True, "value": r.get("value"), "type": r.get("type")})


async def handle_scroll(request):
    body = await request.json()
    x = body.get("x", 0)
    y = body.get("y", 0)
    tab_id = body.get("tab_id")

    await cdp_send("Input.dispatchMouseEvent", {
        "type": "mouseWheel", "x": 100, "y": 100, "deltaX": x, "deltaY": y
    }, tab_id)

    return web.json_response({"ok": True})


async def handle_keypress(request):
    body = await request.json()
    key = body["key"]
    tab_id = body.get("tab_id")

    key_map = {
        "Enter": {"key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13},
        "Tab": {"key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9},
        "Escape": {"key": "Escape", "code": "Escape", "windowsVirtualKeyCode": 27},
        "Backspace": {"key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8},
    }

    kinfo = key_map.get(key, {"key": key})
    await cdp_send("Input.dispatchKeyEvent", {"type": "keyDown", **kinfo}, tab_id)
    await cdp_send("Input.dispatchKeyEvent", {"type": "keyUp", **kinfo}, tab_id)

    return web.json_response({"ok": True})


async def handle_tab_activate(request):
    body = await request.json()
    tab_id = body["tab_id"]
    session = await get_http_session()
    async with session.get(f"http://{CDP_HOST}:{CDP_PORT}/json/activate/{tab_id}") as resp:
        text = await resp.text()
    return web.json_response({"ok": True, "response": text})


async def handle_tab_new(request):
    body = await request.json() if request.can_read_body else {}
    url = body.get("url", "about:blank") if body else "about:blank"
    session = await get_http_session()
    async with session.get(f"http://{CDP_HOST}:{CDP_PORT}/json/new?{url}") as resp:
        tab = await resp.json()
    return web.json_response({"id": tab["id"], "url": tab.get("url", "")})


async def handle_tab_close(request):
    body = await request.json()
    tab_id = body["tab_id"]
    _ws_connections.pop(tab_id, None)
    session = await get_http_session()
    async with session.get(f"http://{CDP_HOST}:{CDP_PORT}/json/close/{tab_id}") as resp:
        text = await resp.text()
    return web.json_response({"ok": True})


async def handle_wait(request):
    body = await request.json()
    selector = body.get("selector")
    timeout_ms = body.get("timeout", 5000)
    tab_id = body.get("tab_id")

    js = f"""
    new Promise((resolve, reject) => {{
        const sel = {json.dumps(selector)};
        if (document.querySelector(sel)) return resolve(true);
        const obs = new MutationObserver(() => {{
            if (document.querySelector(sel)) {{ obs.disconnect(); resolve(true); }}
        }});
        obs.observe(document.body, {{childList: true, subtree: true}});
        setTimeout(() => {{ obs.disconnect(); reject(new Error('timeout')); }}, {timeout_ms});
    }})
    """
    try:
        await cdp_send("Runtime.evaluate", {
            "expression": js, "awaitPromise": True, "returnByValue": True
        }, tab_id, timeout=timeout_ms / 1000 + 5)
        return web.json_response({"ok": True})
    except Exception as e:
        return web.json_response({"ok": False, "error": str(e)}, status=408)


async def on_cleanup(app):
    for ws in _ws_connections.values():
        await ws.close()
    if _http_session and not _http_session.closed:
        await _http_session.close()


def create_app():
    app = web.Application(middlewares=[auth_middleware])
    app.on_cleanup.append(on_cleanup)

    app.router.add_get("/health", handle_health)
    app.router.add_get("/tabs", handle_tabs)
    app.router.add_post("/navigate", handle_navigate)
    app.router.add_post("/screenshot", handle_screenshot)
    app.router.add_post("/click", handle_click)
    app.router.add_post("/type", handle_type)
    app.router.add_post("/evaluate", handle_evaluate)
    app.router.add_post("/scroll", handle_scroll)
    app.router.add_post("/keypress", handle_keypress)
    app.router.add_post("/tab/activate", handle_tab_activate)
    app.router.add_post("/tab/new", handle_tab_new)
    app.router.add_post("/tab/close", handle_tab_close)
    app.router.add_post("/wait", handle_wait)

    return app


if __name__ == "__main__":
    print(f"\n{'='*50}")
    print(f"  Browser Relay starting")
    print(f"  Listening: http://{RELAY_HOST}:{RELAY_PORT}")
    print(f"  CDP target: {CDP_HOST}:{CDP_PORT}")
    print(f"  Auth token: {AUTH_TOKEN}")
    Path("/tmp/browser-relay-token").write_text(AUTH_TOKEN)
    print(f"{'='*50}\n")
    web.run_app(create_app(), host=RELAY_HOST, port=RELAY_PORT, print=None)
=======
#!/usr/bin/env python3
"""
Browser Relay - HTTP API -> CDP (Chrome DevTools Protocol)
Connects to Chromium via CDP WebSocket, exposes HTTP endpoints for AI to control the browser.
"""
import asyncio, json, base64, sys, signal
from http import HTTPStatus
from urllib.request import urlopen
import websockets

CDP_HOST = "localhost"
CDP_PORT = 9222
RELAY_PORT = 8787
msg_id = 0

class CDPSession:
    def __init__(self):
        self.ws = None
        self.pending = {}
        self.target_id = None

    async def connect(self, target_ws_url=None):
        if not target_ws_url:
            # Get first page target
            resp = urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json")
            targets = json.loads(resp.read())
            page_targets = [t for t in targets if t.get("type") == "page"]
            if not page_targets:
                raise Exception("No page targets found")
            target_ws_url = page_targets[0]["webSocketDebuggerUrl"]
            self.target_id = page_targets[0]["id"]
        self.ws = await websockets.connect(target_ws_url, max_size=50*1024*1024)
        asyncio.create_task(self._recv_loop())
        # Enable necessary domains
        await self.send("Page.enable")
        await self.send("Runtime.enable")
        return self

    async def _recv_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in self.pending:
                    self.pending[msg["id"]].set_result(msg)
        except websockets.ConnectionClosed:
            pass

    async def send(self, method, params=None, timeout=30):
        global msg_id
        msg_id += 1
        mid = msg_id
        fut = asyncio.get_event_loop().create_future()
        self.pending[mid] = fut
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        try:
            result = await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(mid, None)
        if "error" in result:
            raise Exception(result["error"])
        return result.get("result", {})

cdp = CDPSession()

async def handle_request(reader, writer):
    try:
        # Parse HTTP request
        request_line = (await reader.readline()).decode().strip()
        if not request_line:
            writer.close()
            return
        method, path, _ = request_line.split(" ", 2)
        headers = {}
        while True:
            line = (await reader.readline()).decode().strip()
            if not line:
                break
            k, v = line.split(": ", 1)
            headers[k.lower()] = v

        body = b""
        if "content-length" in headers:
            body = await reader.readexactly(int(headers["content-length"]))

        # Route
        resp = await route(method, path, json.loads(body) if body else {})
        resp_bytes = json.dumps(resp, ensure_ascii=False).encode()

        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(resp_bytes)}\r\n\r\n".encode())
        writer.write(resp_bytes)
        await writer.drain()
    except Exception as e:
        err = json.dumps({"error": str(e)}).encode()
        writer.write(f"HTTP/1.1 500 Error\r\nContent-Type: application/json\r\nContent-Length: {len(err)}\r\n\r\n".encode())
        writer.write(err)
        await writer.drain()
    finally:
        writer.close()

async def route(method, path, body):
    if path == "/status":
        return {"ok": True, "connected": cdp.ws is not None, "target": cdp.target_id}

    if path == "/targets":
        resp = urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json")
        return json.loads(resp.read())

    if path == "/connect":
        ws_url = body.get("wsUrl")
        await cdp.connect(ws_url)
        return {"ok": True, "target": cdp.target_id}

    if path == "/navigate":
        url = body["url"]
        r = await cdp.send("Page.navigate", {"url": url})
        await asyncio.sleep(1)
        return {"ok": True, "frameId": r.get("frameId")}

    if path == "/screenshot":
        fmt = body.get("format", "jpeg")
        quality = body.get("quality", 60)
        params = {"format": fmt}
        if fmt == "jpeg":
            params["quality"] = quality
        if body.get("fullPage"):
            # Get full page metrics
            metrics = await cdp.send("Page.getLayoutMetrics")
            content = metrics.get("contentSize", metrics.get("cssContentSize", {}))
            w, h = content.get("width", 1280), content.get("height", 800)
            params["clip"] = {"x": 0, "y": 0, "width": w, "height": h, "scale": 1}
        r = await cdp.send("Page.captureScreenshot", params)
        return {"ok": True, "data": r["data"][:200] + "...(truncated)", "length": len(r["data"]), "dataFull": r["data"]}

    if path == "/eval":
        expr = body["expression"]
        r = await cdp.send("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": body.get("awaitPromise", True)
        })
        return {"ok": True, "result": r.get("result", {})}

    if path == "/click":
        x, y = body["x"], body["y"]
        for etype in ["mousePressed", "mouseReleased"]:
            await cdp.send("Input.dispatchMouseEvent", {
                "type": etype, "x": x, "y": y, "button": "left", "clickCount": 1
            })
        return {"ok": True}

    if path == "/type":
        text = body["text"]
        for ch in text:
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "text": ch})
            await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "text": ch})
        return {"ok": True}

    if path == "/scroll":
        x = body.get("x", 400)
        y = body.get("y", 300)
        dx = body.get("deltaX", 0)
        dy = body.get("deltaY", 0)
        await cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": x, "y": y, "deltaX": dx, "deltaY": dy
        })
        return {"ok": True}

    if path == "/key":
        key = body["key"]
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": key, "text": key})
        await cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": key})
        return {"ok": True}

    if path == "/dom":
        # Get document and query selector
        doc = await cdp.send("DOM.getDocument", {"depth": 0})
        root_id = doc["root"]["nodeId"]
        sel = body.get("selector", "body")
        node = await cdp.send("DOM.querySelector", {"nodeId": root_id, "selector": sel})
        if node.get("nodeId", 0) == 0:
            return {"error": "selector not found"}
        # Get box model for coordinates
        box = await cdp.send("DOM.getBoxModel", {"nodeId": node["nodeId"]})
        content = box["model"]["content"]
        cx = (content[0] + content[2]) / 2
        cy = (content[1] + content[5]) / 2
        return {"ok": True, "nodeId": node["nodeId"], "center": {"x": cx, "y": cy}}

    if path == "/html":
        doc = await cdp.send("DOM.getDocument", {"depth": 0})
        root_id = doc["root"]["nodeId"]
        sel = body.get("selector", "body")
        node = await cdp.send("DOM.querySelector", {"nodeId": root_id, "selector": sel})
        if node.get("nodeId", 0) == 0:
            return {"error": "selector not found"}
        html = await cdp.send("DOM.getOuterHTML", {"nodeId": node["nodeId"]})
        return {"ok": True, "html": html.get("outerHTML", "")}

    return {"error": f"unknown route: {path}"}

async def main():
    await cdp.connect()
    print(f"✓ Connected to Chromium CDP (target: {cdp.target_id})")
    server = await asyncio.start_server(handle_request, "127.0.0.1", RELAY_PORT)
    print(f"✓ Relay listening on http://127.0.0.1:{RELAY_PORT}")
    print(f"  Endpoints: /status /targets /navigate /screenshot /eval /click /type /scroll /key /dom /html")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
>>>>>>> d27e1894c4c81c4b347be4dac4f6a86d55709cd7
