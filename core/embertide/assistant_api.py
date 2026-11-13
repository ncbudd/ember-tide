"""Assistant endpoints: ask (with proposed changes), apply / dismiss a proposal, undo a change, the change log.
The safety model is in assistant.py."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import ai
from . import assistant as A
from . import voice


class AskBody(BaseModel):
    person_id: str = Field(max_length=40)
    question: str = Field(min_length=1, max_length=1000)


class PersonBody(BaseModel):
    person_id: str = Field(max_length=40)


def build_assistant_router(state) -> APIRouter:
    r = APIRouter(prefix="/api/assistant")

    def ctl():
        return state.controller

    def person(pid: str):
        try:
            return ctl().person(pid)
        except KeyError:
            raise HTTPException(404, f"unknown person {pid}")

    def principal(req: Request):
        return getattr(req.state, "principal", None)

    def helper(req: Request, p) -> A.Assistant:
        headers = {k.lower(): v for k, v in req.headers.items()}
        return A.Assistant(ctl(), principal(req), p, A.Internal(req.app, headers))

    def may_see(req: Request, pid: str) -> None:
        who = principal(req)
        if who is not None and who.role != "owner" and who.kind != "open" and who.person != pid:
            raise HTTPException(403, "That's their own sleep data.")

    @r.get("/actions")
    async def actions():
        return A.tool_schemas()

    @r.get("/status")
    async def status(person_id: str):
        p = person(person_id)
        cfg = ai.effective(state.cfg, ctl().store)
        return {"configured": ai.configured(cfg), "model": ai.model_name(cfg) if ai.configured(cfg) else None,
                "provider": ai.provider(cfg), "small_changes": p.settings.assistant_small_changes,
                "limits": {"small_c": A.SMALL_C, "changes_per_hour": A.CHANGES_PER_HOUR, "auto_per_night": A.AUTO_PER_NIGHT}}

    @r.get("/log")
    async def get_log(person_id: str, req: Request):
        person(person_id)
        may_see(req, person_id)
        return [A.card(e) for e in reversed(A.log(ctl().store, person_id))][:50]

    @r.post("/ask")
    async def ask(b: AskBody, req: Request):
        c = ctl()
        p = person(b.person_id)
        who = principal(req)
        now = c.clock.now_ts()
        key = f"assistant_asks:{who.id if who else 'open'}"
        asks = [t for t in (c.store.get(key, []) or []) if now - t < 3600]
        if len(asks) >= A.ASKS_PER_HOUR:
            raise HTTPException(429, "That's a lot of questions this hour. Try again a bit later.")
        c.store.put(key, asks + [now])
        h = helper(req, p)
        # asking about the partner's side (to start a flash they share, say): the model gets their plan and tonight,
        # never their nights, notes, devices or Autopilot (auth.PRIVATE_READS)
        private_ok = who is None or who.role == "owner" or who.kind == "open" or who.person == p.id
        intent = A.wants_change(b.question)
        made: list[dict] = []
        ignored: list[str] = []

        async def on_call(name: str, args: dict) -> str:
            if len(made) + len(ignored) >= 4:
                return "Not done: at most four changes per question."
            try:
                act = A.ACTIONS.get(name)
                parsed = A.validate(name, args)
                if not intent and not act.advice:
                    raise A.ActionError(403, "the person didn't ask for a change")
                why = h.may_target(act)
                if why:
                    raise A.ActionError(403, why)
                plan = await h.plan(name, parsed)
            except A.ActionError as e:
                ignored.append(f"{str(name)[:40]}: {e.detail}")
                return f"Not possible: {e.detail}"
            entry = {"id": A.new_id(), "ts": now, "by": who.id if who else "open", "person": p.id, "action": name,
                     "args": parsed.model_dump(exclude_none=True), "summary": plan.summary, "status": "proposed",
                     "auto": False, "unprompted": not intent, "small": plan.small, "delta_c": plan.delta_c,
                     "reason": None}
            items = A.log(c.store, p.id)
            auto_ok = (p.settings.assistant_small_changes and plan.small and intent and h.own() and act.kind in ("tonight", "flash")
                       and abs(A.tonight_auto_delta(items, now) + plan.delta_c) <= A.SMALL_C + 1e-6
                       and A.recent_count(items, now, ("applied", "undone"), 12 * 3600, auto=True) < A.AUTO_PER_NIGHT
                       and A.recent_count(items, now, ("applied", "undone"), 3600) < A.CHANGES_PER_HOUR)
            if auto_ok:
                try:
                    await h.run_steps(plan.steps)
                    entry.update(status="applied", auto=True, applied_ts=now, undo=plan.undo, after=plan.after)
                    c.store.event(now, "assistant_change", p.id, "assistant", action=name, summary=plan.summary, auto=True, change_id=entry["id"])
                except A.ActionError as e:
                    entry.update(status="failed", error=e.detail)
            made.append(entry)
            A.save_log(c.store, p.id, items + [entry])
            if entry["status"] == "applied":
                return f"Applied right away (the person allowed small changes): {plan.summary}. They can undo it."
            if entry["status"] == "failed":
                return f"Tried, but the app refused: {entry['error']}"
            return f"Proposed: {plan.summary}. The person will see a card and decide."

        if not ai.configured(ai.effective(state.cfg, c.store)) and getattr(req.app.state, "assistant_model", None) is None:
            if not private_ok:
                return {"answer": "No AI model is connected, and their sleep details are theirs to see.", "model": None,
                        "proposals": [], "applied": [], "ignored": []}
            from .engine.insights import insights
            from .engine.summary import recent
            nights = recent(c.store, p, c.clock.tz, now, 7)
            ins = insights(p, nights, c.clock.tz, now)
            text = ("No AI model is connected, so here is what the built-in analysis found:\n\n"
                    + "\n".join(f"• {i['title']}: {i['detail']}" for i in ins))
            prop = next((i["proposal"] for i in ins if i.get("proposal") and i["proposal"].get("phase_id")), None)
            if prop:
                await on_call("change_rhythm_phase", {"rhythm_id": prop["rhythm_id"], "phase_id": prop["phase_id"],
                                                      **({"temp_c": prop["to"]} if prop["field"] == "temp_c" else {"ramp_min": int(prop["to"])})})
            return {"answer": text, "model": None, "proposals": [A.card(e) for e in made if e["status"] == "proposed"],
                    "applied": [], "ignored": ignored}
        model = getattr(req.app.state, "assistant_model", None) or ai.model_client(ai.effective(state.cfg, c.store))
        try:
            reply = await model.run(A.system_prompt(voice.tone_of(p), p.settings), A.user_message(b.question, A.context(c, p, now, private=private_ok)), A.tool_schemas(), on_call)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"The AI model didn't answer ({type(e).__name__}).")
        text = reply.text or (voice.say("assistant_proposed", person=p, store=c.store) if made else "I don't have an answer for that.")
        for e in made:
            e["reason"] = A.clean(text, 300)
        if made:
            items = A.log(c.store, p.id)
            ids = {e["id"]: e for e in made}
            A.save_log(c.store, p.id, [ids.get(x["id"], x) for x in items])
        return {"answer": text, "model": reply.model or ai.model_name(ai.effective(state.cfg, c.store)),
                "proposals": [A.card(e) for e in made if e["status"] == "proposed"],
                "applied": [A.card(e) for e in made if e["status"] == "applied"], "ignored": ignored}

    def find(pid: str, eid: str) -> tuple[list[dict], dict]:
        items = A.log(ctl().store, pid)
        e = next((x for x in items if x["id"] == eid), None)
        if not e:
            raise HTTPException(404, "No such change.")
        return items, e

    def mine(req: Request, e: dict) -> None:
        who = principal(req)
        if who is not None and who.id != e.get("by") and who.person != e.get("person"):
            raise HTTPException(403, "Only the person who asked can do that.")

    @r.post("/proposals/{eid}/apply")
    async def apply(eid: str, b: PersonBody, req: Request):
        c = ctl()
        p = person(b.person_id)
        items, e = find(p.id, eid)
        mine(req, e)
        now = c.clock.now_ts()
        if e["status"] != "proposed":
            raise HTTPException(409, f"That change is already {e['status']}.")
        if now - e["ts"] > A.PROPOSAL_TTL_S:
            e["status"] = "expired"
            A.save_log(c.store, p.id, items)
            raise HTTPException(410, "That suggestion is too old now. Ask again.")
        if A.recent_count(items, now, ("applied", "undone"), 3600) >= A.CHANGES_PER_HOUR:
            raise HTTPException(429, "That's a lot of changes this hour. Try again a bit later.")
        h = helper(req, p)
        try:
            act = A.ACTIONS[e["action"]]
            parsed = A.validate(e["action"], e["args"])
            why = h.may_target(act)
            if why:
                raise A.ActionError(403, why)
            plan = await h.plan(e["action"], parsed)            # fresh: the state may have moved since it was proposed
            await h.run_steps(plan.steps)
        except A.ActionError as err:
            e.update(status="failed", error=err.detail)
            A.save_log(c.store, p.id, items)
            raise HTTPException(err.status if err.status in (403, 404, 409, 422) else 409, err.detail)
        e.update(status="applied", applied_ts=now, summary=plan.summary, undo=plan.undo, after=plan.after, delta_c=plan.delta_c)
        A.save_log(c.store, p.id, items)
        c.store.event(now, "assistant_change", p.id, "assistant", action=e["action"], summary=plan.summary, auto=False, change_id=eid)
        return {"ok": True, "change": A.card(e), "message": f"Done: {plan.summary}."}

    @r.post("/proposals/{eid}/dismiss")
    async def dismiss(eid: str, b: PersonBody, req: Request):
        p = person(b.person_id)
        items, e = find(p.id, eid)
        mine(req, e)
        if e["status"] == "proposed":
            e["status"] = "dismissed"
            A.save_log(ctl().store, p.id, items)
        return {"ok": True, "change": A.card(e)}

    @r.post("/changes/{eid}/undo")
    async def undo(eid: str, b: PersonBody, req: Request):
        c = ctl()
        p = person(b.person_id)
        items, e = find(p.id, eid)
        mine(req, e)
        if e["status"] != "applied":
            raise HTTPException(409, f"That change is {e['status']}, so there's nothing to undo.")
        h = helper(req, p)
        try:
            why = await h.still_there(e)
            if why:
                raise A.ActionError(409, why + " Change it back by hand if you like.")
            await h.run_steps(e.get("undo") or [])
        except A.ActionError as err:
            raise HTTPException(err.status if err.status in (403, 404, 409, 422) else 409, err.detail)
        now = c.clock.now_ts()
        e.update(status="undone", undone_ts=now)
        A.save_log(c.store, p.id, items)
        c.store.event(now, "assistant_undo", p.id, "assistant", action=e["action"], summary=e["summary"], change_id=eid)
        return {"ok": True, "change": A.card(e), "message": f"Undone: {e['summary']}."}

    return r
