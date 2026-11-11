import asyncio, sys, random
from pathlib import Path
sys.path.insert(0, "tests")
from lab import Lab, Sleeper, Wearable, ts

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)

async def main():
    sl = {"left": Sleeper(pref_c={"*": -0.6, "deep": -1.2}, watch=False, tap_prob=0.12, flash_phase="late", flash_minute=25,
                          wearables=[Wearable("Pixel Watch", skin=True, hr=True, every_s=240)]),
          "right": Sleeper(pref_c={"*": 0.3}, restless=0.03, tap_prob=0.08,
                           wearables=[Wearable("Oura Ring", skin=True, hr=True, every_s=300)])}
    lab = Lab(OUT, "2026-10-30T20:00", sleepers=sl, room_c=20.5, humidity=48)
    lab.p1.name, lab.p2.name = "Alex", "Sam"
    await lab.nights(10)
    rnd = random.Random(5)
    for pid, label, src in (("p1", "Pixel Watch · Health Connect", "health-connect"), ("p2", "Oura Ring · Oura", "oura")):
        for d in range(6, 10):
            base = ts(f"2026-11-{d:02d}T22:40")
            segs, t = [], base
            cyc = ["light","deep","deep","light","rem","light","deep","light","rem","light","awake","light","rem","light","rem","light"]
            for st in cyc:
                m = {"deep": rnd.randint(18, 35), "rem": rnd.randint(12, 25), "light": rnd.randint(20, 40), "awake": rnd.randint(4, 9)}[st]
                segs.append({"start": t, "end": t + m * 60, "stage": st}); t += m * 60
            lab.store.put_stages(pid, src, label, segs, base - 3600, t + 3600)
    lab.store.put("household", lab.hh.model_dump())
    print("end", lab.clock.now().isoformat())

asyncio.run(main())
