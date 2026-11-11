r"""Drive the full 3-minute demo story against a running demo server (scripts\demo.ps1).

Run from backend\:  .venv\Scripts\python.exe scripts\smoke_story.py http://localhost:8010
Creates a new heart-failure episode each run; wait 60 s between runs (OTP resend cooldown)."""
import re, sys, time, uuid
from datetime import datetime, timedelta, timezone
import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8011"
H = {"X-Requested-With": "ninety-six"}
log = []


def ok(cond, msg):
    log.append(("PASS" if cond else "FAIL") + "  " + msg)
    print(log[-1], flush=True)
    if not cond:
        raise SystemExit(1)


def c():
    return httpx.Client(base_url=BASE, headers=H, timeout=30)


def wait(fn, what, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(1)
    ok(False, f"timed out waiting for {what}")


def login(cl, user):
    r = cl.post("/api/auth/staff/login", json={"username": user, "password": "demo1234"})
    ok(r.status_code == 200, f"{user} signs in")


nurse, doc, pt = c(), c(), c()
ok(nurse.get("/api/health").json().get("ok"), "server healthy")
login(nurse, "nurse.asha"); login(doc, "dr.mehta")
tools = {t["name"]: t["mode"] for t in nurse.get("/api/demo/state").json()["tools"]}
print("tool modes:", tools)
staff = {u["username"]: u["id"] for u in nurse.get("/api/staff?role=doctor").json()}
sample = next(s for s in nurse.get("/api/samples").json() if s.get("primary"))
now = datetime.fromisoformat(nurse.get("/api/clock").json()["now"].replace("Z", "+00:00"))

# 1. intake -> plan -> memory
r = nurse.post("/api/episodes", json={
    "patient": sample["patient"], "doctor_id": staff["dr.mehta"], "risk_inputs": sample.get("risk_inputs") or {},
    "summary_text": sample["summary_text"], "sample_key": sample["key"],
    "discharge_at": (now - timedelta(hours=float(sample.get("discharge_hours_ago") or 2))).isoformat().replace("+00:00", "Z")})
ok(r.status_code == 201, f"intake '{sample['label']}' created")
eid = r.json()["id"]
ok(len(r.json()["facts"]) > 5, f"extraction produced {len(r.json()['facts'])} facts with sources")
ok(nurse.post(f"/api/episodes/{eid}/submit").status_code == 200, "submitted for review; draft plan generated")

# 2. medication review re-plans
def med_replan():
    plans = doc.get(f"/api/episodes/{eid}/plans").json()
    return plans if len(plans) >= 2 else None
plans = wait(med_replan, "medication-review re-plan")
ok(plans[0]["trigger"] != plans[-1]["trigger"] or plans[0]["version"] > 1,
   f"plan v{plans[0]['version']} from tool result: {plans[0].get('change_summary','')[:90]}")
execs = doc.get(f"/api/episodes/{eid}/executions").json()
med = [e for e in execs if e.get("tool") == "medication_info" or "medication" in str(e.get("tool"))]
ok(bool(med), f"medication tool executed (mode={med[0].get('mode') if med else '?'})")

# activate
detail = doc.get(f"/api/episodes/{eid}").json()
plan = doc.get(f"/api/episodes/{eid}/plans/current").json()
offs = sorted({s["offset_hours"] for s in plan["steps"] if (s["arguments"] or {}).get("template_id") == "checkin_reminder_v1"}) or [12, 24, 48, 72]
r = doc.post(f"/api/episodes/{eid}/activate", json={"plan_version_id": plan["id"], "confirm_fact_ids": [f["id"] for f in detail["facts"]],
    "routine": {"template_ids": ["checkin_reminder_v1"], "recipient_roles": ["patient"], "offsets_hours": offs, "max_per_day": 3}})
ok(r.status_code == 200 and r.json()["status"] == "active", "doctor activates plan with bounded routine check-in scope")
time.sleep(3)
for item in doc.get(f"/api/episodes/{eid}/review-items").json():
    if item["kind"] == "medication_review" and item["status"] != "resolved":
        ok(doc.post(f"/api/review-items/{item['id']}/resolve", json={"note": "Pharmacist reviewed; continue as prescribed"}).status_code == 200,
           "doctor resolves pharmacist/medication review")

# 3. approvals: edit welcome, approve booking
def pending():
    return [a for a in doc.get("/api/approvals").json() if a["episode_id"] == eid and a["approval_state"] == "pending"]
welcome = wait(lambda: next((a for a in pending() if a.get("template_id") == "welcome_v1"), None), "welcome approval")
body = welcome["payload"]["body"] + " Reply STOP to opt out."
r = doc.post(f"/api/actions/{welcome['id']}/edit-approve", json={"base_payload_hash": welcome["payload_hash"], "payload": {**welcome["payload"], "body": body}})
ok(r.status_code == 200, f"doctor edits + approves welcome SMS ({r.status_code})")
for a in pending():
    if a["action_type"] in ("book_followup", "create_calendar_event", "book_appointment") or "book" in a["action_type"]:
        ok(doc.post(f"/api/actions/{a['id']}/approve", json={"payload_hash": a["payload_hash"]}).status_code == 200, f"doctor approves {a['action_type']}")
wait(lambda: [m for m in doc.get(f"/api/episodes/{eid}/messages").json() if m["action_id"] == welcome["id"] and m["status"] in ("simulated", "provider_accepted")], "welcome sent")
sent = next(m for m in doc.get(f"/api/episodes/{eid}/messages").json() if m["action_id"] == welcome["id"])
ok(sent["body"].endswith("Reply STOP to opt out."), "edited welcome text is what was sent")
fus = doc.get(f"/api/episodes/{eid}/followups").json()
print("follow-ups:", [(f["status"], f.get("calendar_mode")) for f in fus])

# patient sign-in
ok(nurse.post(f"/api/episodes/{eid}/invite").status_code == 200, "nurse invites patient")
phone = detail["patient"]["phone_e164"]
ok(pt.post("/api/auth/patient/request-code", json={"phone": phone}).status_code == 202, "patient requests code")
code = next(re.search(r"\b(\d{6})\b", i["body"]).group(1) for i in nurse.get("/api/demo/outbox").json()
            if i["kind"] == "otp" and i["to_masked"].endswith(phone[-4:]))
ok(pt.post("/api/auth/patient/verify", json={"phone": phone, "code": code}).status_code == 200, "patient verifies one-time code")

# 4. +24h -> check-in sends itself -> all good -> auto-ack
def advance_until_due():
    for _ in range(8):
        due = pt.get(f"/api/me/episodes/{eid}/today").json().get("due_checkin")
        if due:
            return due
        nurse.post("/api/demo/advance", json={"hours": 6}); time.sleep(2)
due = advance_until_due(); ok(bool(due), "check-in became due on the simulated clock")
reminders = [m for m in doc.get(f"/api/episodes/{eid}/messages").json() if (m.get("kind") or "").startswith("checkin") or "remind" in (m.get("kind") or "")]
ok(bool(reminders), f"routine check-in reminder sent without a new approval ({len(reminders)})")
r = nurse.post(f"/api/demo/episodes/{eid}/patient-response", json={"preset": "all_good"})
ok(r.status_code in (200, 202), "patient replies 'All good'")
ci = wait(lambda: next((x for x in doc.get(f"/api/episodes/{eid}/checkins").json() if x["id"] == due["id"] and x["processing_state"] == "processed"), None), "triage")
ok(ci["triage_level"] in ("ok", "routine", "normal"), f"triage = {ci['triage_level']}")

# 5. red flag
nurse.post("/api/demo/advance", json={"hours": 24}); time.sleep(2)
due2 = advance_until_due(); ok(bool(due2) and due2["id"] != due["id"], "next check-in due")
nurse.post(f"/api/demo/episodes/{eid}/patient-response", json={"preset": "red_flag"})
ci2 = wait(lambda: next((x for x in doc.get(f"/api/episodes/{eid}/checkins").json() if x["id"] == due2["id"] and x["processing_state"] == "processed"), None), "red-flag triage")
ok(ci2["triage_level"] == "urgent", f"RED FLAG triage = {ci2['triage_level']}")
mem = doc.get(f"/api/episodes/{eid}/memory").json()
ok(any("triage" in (u.get("used_for") or "") or u.get("ref_type") == "checkin" for u in mem["usage"]), f"memory recalled for triage ({len(mem['usage'])} usage rows)")
urgent = wait(lambda: [a for a in pending() if a["priority"] == "urgent"], "urgent approvals")
print("urgent approvals:", [(a["action_type"], a["recipient_role"]) for a in urgent])
for a in urgent:
    ok(doc.post(f"/api/actions/{a['id']}/approve", json={"payload_hash": a["payload_hash"]}).status_code == 200, f"doctor approves URGENT {a['action_type']} -> {a['recipient_role']}")
def urgent_done():
    acts = {a["id"]: a for a in doc.get(f"/api/episodes/{eid}/actions").json()}
    return all(acts[a["id"]]["execution_state"] == "succeeded" for a in urgent)
wait(urgent_done, "urgent actions executed")
ok(True, "care-team alert + patient response executed")
alert_ids = {a["id"] for a in urgent if a["action_type"] == "care_team_alert"}
alert_execs = [e for e in doc.get(f"/api/episodes/{eid}/executions").json() if e["action_id"] in alert_ids]
want = tools.get("care_team_email", "simulated")
ok(bool(alert_execs) and all(e["mode"] == want and e["outcome"] == "success" for e in alert_execs),
   f"URGENT care-team email sent in {want.upper()} mode (ref {alert_execs[0].get('provider_ref') if alert_execs else '-'})")

# 6. 96h summary
for _ in range(6):
    nurse.post("/api/demo/advance", json={"hours": 24}); time.sleep(2)
    if doc.get(f"/api/episodes/{eid}/summary").status_code == 200:
        break
s = doc.get(f"/api/episodes/{eid}/summary")
ok(s.status_code == 200, "96-hour summary generated for the doctor")
print("summary keys:", list(s.json().keys())[:15])
ep = doc.get(f"/api/episodes/{eid}").json(); print("episode status:", ep["status"])
open_items = [i for i in doc.get(f"/api/episodes/{eid}/review-items").json() if i["status"] != "resolved"]
for i in open_items:
    doc.post(f"/api/review-items/{i['id']}/resolve", json={"note": "Reviewed at closure"})
r = doc.post(f"/api/episodes/{eid}/close", json={"note": "96-hour window complete"})
ok(r.status_code == 200, f"doctor closes episode ({r.status_code} {r.text[:120] if r.status_code != 200 else ''})")
print("\nALL STEPS PASSED")
