import time, json, urllib.request
from cdp import Browser

def patch(pid, body):
    r = urllib.request.Request(f"http://localhost:8871/api/people/{pid}/settings", data=json.dumps(body).encode(),
                               method="PATCH", headers={"Content-Type": "application/json"})
    # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected -- fixed http://localhost URL built in this script, no external input
    urllib.request.urlopen(r).read()

b = Browser()
try:
    b.view(390, 844, True, 2, "dark")
    b.go("/"); b.js("localStorage.setItem('et-me','p1')")
    patch("p1", {"welcomed": False})
    b.go("/", 3)
    b.shot("welcome")
    b.click_text("Let's start"); time.sleep(1.5)
    b.shot("welcome-step")
    patch("p1", {"welcomed": True})

    b.view(390, 2300, True, 2, "dark")
    b.go("/sleep", 4)
    b.click_text("‹"); time.sleep(3)
    # hide the suggestion cards above the sleep section so the night is the first thing
    b.js("""(()=>{const h=[...document.querySelectorAll('*')].find(e=>e.children.length==0&&e.textContent.trim()==='Sleep'&&e.tagName!=='A'&&!e.closest('nav'));
      if(!h)return 'nohead';let n=h; while(n.parentElement && n.parentElement.children.length<3) n=n.parentElement;
      let s=n.previousElementSibling; while(s){const p=s.previousElementSibling; s.style.display='none'; s=p;} return 'ok'})()""")
    time.sleep(1)
    # trim the page to its content height
    h = b.js("(()=>{const e=document.querySelector('.screen');return e?e.scrollHeight:0})()")
    print("screen height", h)
    b.shot("sleep")
finally:
    b.close()
