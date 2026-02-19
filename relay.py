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
