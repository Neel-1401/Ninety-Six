# Demo video — 3:00 shot list

Record at 1920×1080 with the browser at 110% zoom. Start `scripts\dev.ps1` (live keys) or `scripts\demo.ps1` (simulated + live openFDA) on a **reset** database: Demo drawer → Reset.
Open two windows side by side: hospital (nurse/doctor) and patient (phone-sized, 390 px wide). If Twilio is live, keep the phone in shot for the SMS moments.

Rehearse once at full length before recording. The OTP resend cooldown is 60 seconds.

| Time | Screen | Do | Say (roughly) |
|---|---|---|---|
| 0:00–0:15 | Deck cover | — | “The first 96 hours after discharge are when early problems show up, and when patients are least watched. Ninety-Six is an agent that runs those 96 hours.” |
| 0:15–0:40 | Nurse → New discharge | Sign in as `nurse.asha`, load **Heart failure, 68 M**, show extracted facts with their source lines, submit | “The nurse pastes a discharge summary. The agent extracts every fact with the line it came from, scores the risk and drafts a timed plan from 0 to 96 hours.” |
| 0:40–1:00 | Episode → Plan timeline | Show plan v1 → **v2**, the medication finding (openFDA, LIVE badge) | “It checks the medicines against openFDA, live. Lisinopril with spironolactone is flagged for potassium, so the agent re-plans on its own: version two adds a doctor review before the welcome message.” |
| 1:00–1:35 | Doctor → Approvals | Sign in as `dr.mehta`: confirm facts, activate with routine check-ins, resolve the medicine review, **edit** the welcome SMS then approve, approve both bookings | “Nothing irreversible happens without a doctor. Each approval is bound to the exact text, recipient and mode; edit it and the old approval is void. One routine sign-off covers only template reminders at the hours chosen.” (Show the SMS arriving / the calendar event if live.) |
| 1:35–1:55 | Demo drawer +24 h, patient window | Advance +24 h; patient signs in with the code, answers the check-in “All good” | “Twenty-four hours later the check-in sends itself. The patient answers in a guided form. All good: the agent acknowledges it and keeps the plan.” |
| 1:55–2:30 | +24 h, patient reports breathlessness lying flat | Submit red-flag answers; switch to the hospital: **URGENT** review item, plan **v3**, three urgent approvals | “Next day: breathless lying flat. It recalls this patient has heart failure, marks it a red flag and re-plans. Rules can only raise a triage level, never lower it. The patient reply, the care-team email and the caregiver message each wait for the doctor, urgent first.” Approve all three; show the email if live. |
| 2:30–2:45 | +24 h ×2 → Summary | Fast-forward to 96 h, open the summary, close the episode | “At 96 hours it writes a summary for the doctor: what happened, what was escalated, what’s still open.” |
| 2:45–3:00 | Deck slide 6 | — | “Ninety automated tests, the full story scripted end to end, live tool calls with fallbacks everywhere. Ninety-Six: an agent that follows up so nurses don’t have to chase.” |

Cut points if running long: skip the second booking approval, and show the summary without scrolling.
