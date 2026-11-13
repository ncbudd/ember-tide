import json, subprocess, time, urllib.request, base64, sys, os
from websockets.sync.client import connect

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9333
BASE = "http://localhost:8871"
OUT = os.path.join(os.path.dirname(__file__), "shots")
os.makedirs(OUT, exist_ok=True)


class Browser:
    def __init__(self):
        self.proc = subprocess.Popen([EDGE, "--headless=new", f"--remote-debugging-port={PORT}", "--disable-gpu",
                                      "--user-data-dir=" + os.path.join(os.path.dirname(__file__), "edge-profile"),
                                      "--no-first-run", "--hide-scrollbars", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try:
                # nosemgrep: python.lang.security.audit.insecure-transport.urllib.insecure-urlopen.insecure-urlopen -- local Chrome DevTools endpoint on localhost, no TLS exists there
                tabs = json.load(urllib.request.urlopen(f"http://localhost:{PORT}/json"))
                page = next(t for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.3)
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=None)
        self.n = 0
        self.send("Page.enable")

    def send(self, method, **params):
        self.n += 1
        self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == self.n:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})

    def view(self, w, h, mobile, dpr=1, scheme="dark"):
        self.send("Emulation.setDeviceMetricsOverride", width=w, height=h, deviceScaleFactor=dpr, mobile=mobile)
        self.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": scheme}])
        if mobile:
            self.send("Emulation.setTouchEmulationEnabled", enabled=True)

    def go(self, path, wait=3.5):
        self.send("Page.navigate", url=BASE + path)
        time.sleep(wait)

    def js(self, expr):
        r = self.send("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def click_text(self, text, sel="button, a, [role=tab]"):
        return self.js("""(()=>{const t=%s;const els=[...document.querySelectorAll(%s)];
          const e=els.find(x=>x.textContent.trim()===t)||els.find(x=>x.textContent.trim().includes(t));
          if(e){e.click();return true}return false})()""" % (json.dumps(text), json.dumps(sel)))

    def shot(self, name, full=False):
        params = dict(format="webp", quality=82)
        if full:
            dim = self.js("[document.documentElement.scrollWidth, Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)]")
            params.update(captureBeyondViewport=True, clip={"x": 0, "y": 0, "width": dim[0], "height": dim[1], "scale": 1})
        r = self.send("Page.captureScreenshot", **params)
        p = os.path.join(OUT, name + ".webp")
        open(p, "wb").write(base64.b64decode(r["data"]))
        return p

    def close(self):
        try:
            self.send("Browser.close")
        except Exception:
            pass
        self.proc.kill()
