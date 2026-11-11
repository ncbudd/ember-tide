# Regenerating the documentation screenshots

The pictures in `docs/images/` are made from the simulator with two made-up sleepers (Alex and Sam). Nothing real is involved.

1. Seed a demo database with ten nights of history (from `core/`, with the project's Python):

   ```bash
   cd core
   ET_TZ=America/New_York PYTHONPATH=. python ../scripts/screenshots/seed.py ./demo-data
   mv demo-data/lab.db demo-data/embertide.db
   ```

2. Build the web app (`cd web && npm ci && npm run build`) and start a demo server on a spare port with sign-in off:

   ```bash
   ET_TZ=America/New_York ET_AUTH=off ET_BED=sim ET_PORT=8871 ET_DATA_DIR=./demo-data ET_WEB_DIR=../web/dist python -m embertide
   ```

3. Put the night in progress (jump the clock, start both nights, let it run, back to real time):

   ```bash
   curl -X POST localhost:8871/api/sim -H 'content-type: application/json' -d '{"jump_to":"2026-11-09T22:30","scripted":false}'
   curl -X POST localhost:8871/api/sim -H 'content-type: application/json' -d '{"presence":{"left":true,"right":true}}'
   curl -X POST localhost:8871/api/people/p1/tonight -H 'content-type: application/json' -d '{"action":"start_now"}'
   curl -X POST localhost:8871/api/people/p2/tonight -H 'content-type: application/json' -d '{"action":"start_now"}'
   curl -X POST localhost:8871/api/sim -H 'content-type: application/json' -d '{"speed":600}'    # about 17 s here
   curl -X POST localhost:8871/api/sim -H 'content-type: application/json' -d '{"speed":1}'
   ```

   Mark setup done and keep the full Tonight screen visible at night:
   `PATCH /api/people/p1/settings {"welcomed": true, "simple_at_night": false}` (and `p2`).

4. Capture with Edge or Chrome over the DevTools protocol (needs `websockets`, which the server already depends on):

   ```bash
   python scripts/screenshots/shoot.py                 # phone shots, Studio shots
   python scripts/screenshots/shoot_sleep_welcome.py   # welcome and sleep
   ```

   Images land in `scripts/screenshots/shots/` (git-ignored); copy the ones you want into `docs/images/` and look at every one: no
   real names, addresses or errors. Edit `EDGE` in `cdp.py` if your browser lives elsewhere. Fonts come from the system when the
   app's web fonts aren't reachable.
