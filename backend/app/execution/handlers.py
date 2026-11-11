"""Task handlers (Agent B). Each handler runs on a CLAIMED task, re-checks preflight inside its
transaction, commits before any provider call, and persists the result in a new transaction."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import db
from ..clock import clock, iso
from ..events import record_event
from ..models import (
    ApprovalState,
    CheckIn,
    CheckInResponse,
    CheckInStatus,
    DischargeEpisode,
    EpisodeStatus,
    ExecutionState,
    FactSourceKind,
    FollowUpEvent,
    FollowUpStatus,
    Message,
    MessageStatus,
    PatientFact,
    PlanStep,
    ProcessingState,
    ProposedAction,
    ReviewItem,
    ReviewStatus,
    ScheduledTask,
    StaffReviewState,
    StepStatus,
    TaskStatus,
    ToolExecution,
)
from ..tools.base import ToolResult
from ..tools.registry import get_adapter
from .worker import WORKER_ID, apply_status, preflight

log = logging.getLogger("ninety_six.handlers")

TOOL_FOR_ACTION = {"send_message": "messaging", "create_calendar_event": "calendar", "care_team_alert": "care_team_email"}
BACKOFF_SECONDS = [2, 10, 30]


def _release(t: ScheduledTask) -> None:
    t.claimed_by = None
    t.lease_expires_at = None


def _finish(s: Session, t: ScheduledTask, status: str, reason: Optional[str] = None, *, summary: Optional[str] = None) -> None:
    from ..agent.plan_store import set_task_status

    set_task_status(s, t, status, reason)
    _release(t)
    if t.step_id:
        st = s.get(PlanStep, t.step_id)
        if st is not None:
            if status == TaskStatus.SUCCEEDED:
                st.executed_at = clock.now()
            if summary is not None:
                st.result_summary = summary


def execute_claimed(task_id: int) -> None:
    with db.session_scope() as s:
        t = s.get(ScheduledTask, task_id)
        if t is None or t.status != TaskStatus.RUNNING or t.claimed_by != WORKER_ID:
            return
        status, reason, override = preflight(s, t, clock.now())
        if status != TaskStatus.READY:
            # state changed between promotion and claim: do nothing external
            t.status = TaskStatus.READY  # let apply_status compute the transition cleanly
            apply_status(s, t, status, reason, override)
            _release(t)
            return
        kind = t.kind
    handler = HANDLERS.get(kind)
    if handler is None:
        with db.session_scope() as s:
            t = s.get(ScheduledTask, task_id)
            _finish(s, t, TaskStatus.FAILED, f"no handler for {kind}")
        return
    handler(task_id)


# ----------------------------------------------------------------------------- external actions
def _request_for(a: ProposedAction) -> dict[str, Any]:
    p = dict(a.payload or {})
    if a.action_type == "send_message":
        return {"to": a.effective_recipient, "body": p.get("body", ""), "channel": a.channel}
    if a.action_type == "care_team_alert":
        return {"subject": p.get("subject"), "body": p.get("body", "")}
    return {
        "title": p.get("title"), "start_at": p.get("start_at"), "end_at": p.get("end_at"),
        "location": p.get("location"),
        "description": f"Created by Ninety-Six after doctor approval. Provider: {p.get('provider_name') or 'n/a'}. "
                       "This is a calendar event, not a confirmed hospital appointment. Availability was not checked.",
    }


def _redacted(a: ProposedAction) -> dict[str, Any]:
    from ..tools.messaging import mask_phone

    d = {"action_type": a.action_type, "channel": a.channel, "template_id": a.template_id,
         "payload_hash": a.payload_hash, "payload_version": a.payload_version}
    if a.action_type == "send_message":
        d["to"] = mask_phone(a.effective_recipient)
        d["redirected"] = a.effective_recipient != a.intended_recipient
    return d


def handle_execute_action(task_id: int) -> None:
    # --- prepare (transaction 1)
    with db.session_scope() as s:
        # Reserve SQLite's writer before reading policy state. Final validation,
        # RUNNING and the execution record must commit atomically with respect
        # to rejection, revocation, contact edits and replanning.
        if s.get_bind().dialect.name == "sqlite":
            s.connection().exec_driver_sql("BEGIN IMMEDIATE")
        t = s.get(ScheduledTask, task_id)
        if t is None or t.status != TaskStatus.RUNNING or t.claimed_by != WORKER_ID:
            return
        status, reason, override = preflight(s, t, clock.now())
        if status != TaskStatus.READY:
            t.status = TaskStatus.READY
            apply_status(s, t, status, reason, override)
            _release(t)
            return
        a = s.get(ProposedAction, t.action_id)
        if a.execution_state != ExecutionState.NOT_STARTED:
            return
        tool = TOOL_FOR_ACTION[a.action_type]
        a.execution_state = ExecutionState.RUNNING
        ex = ToolExecution(
            episode_id=a.episode_id, task_id=t.id, action_id=a.id, tool=tool,
            provider={"messaging": "twilio", "calendar": "google_calendar", "care_team_email": "smtp"}[tool]
            if a.expected_mode == "live" else "local",
            mode=a.expected_mode, attempt=t.attempts, idempotency_key=a.idempotency_key,
            request_summary=_redacted(a), outcome="pending",
        )
        s.add(ex)
        if a.action_type in ("send_message", "care_team_alert"):
            msg = s.scalars(select(Message).where(Message.action_id == a.id).order_by(Message.id.desc())).first()
            if msg is None:
                from ..agent.message_templates import MESSAGE_KIND

                msg = Message(
                    episode_id=a.episode_id, action_id=a.id, direction="outbound",
                    kind=MESSAGE_KIND.get(a.template_id or "", "clinical"),
                    audience=a.recipient_role, channel=a.channel, subject=(a.payload or {}).get("subject"),
                    body=(a.payload or {}).get("body", ""), patient_visible=a.recipient_role == "patient",
                    intended_recipient=a.intended_recipient, effective_recipient=a.effective_recipient,
                    mode=a.expected_mode, status=MessageStatus.QUEUED,
                )
                s.add(msg)
            else:
                msg.status, msg.body, msg.mode = MessageStatus.QUEUED, (a.payload or {}).get("body", ""), a.expected_mode
        s.flush()
        ex_id = ex.id
        request = _request_for(a)
        idem = a.idempotency_key
        action_id = a.id
    # --- provider call (no transaction open)
    adapter = get_adapter(tool, for_execution=True)
    try:
        result: ToolResult = adapter.execute(request, idempotency_key=idem)
    except Exception as e:  # adapter bug: we cannot know whether the action happened
        log.exception("adapter %s crashed", tool)
        result = ToolResult("unknown", "live" if getattr(adapter, "mode", lambda: "simulated")() == "live" else "simulated",
                            "local", safe_error=f"adapter error ({type(e).__name__}); outcome unknown")
    # --- persist (transaction 2)
    with db.session_scope() as s:
        t = s.get(ScheduledTask, task_id)
        a = s.get(ProposedAction, action_id)
        ex = s.get(ToolExecution, ex_id)
        now = clock.now()
        ex.outcome, ex.mode, ex.provider = result.outcome, result.mode, result.provider
        ex.provider_ref, ex.provider_status = result.provider_ref, result.provider_status
        ex.delivery_status, ex.safe_error = result.delivery_status, result.safe_error
        ex.data, ex.finished_at = dict(result.data or {}), now
        msg = s.scalars(select(Message).where(Message.action_id == a.id).order_by(Message.id.desc())).first()
        fu = s.scalars(select(FollowUpEvent).where(FollowUpEvent.action_id == a.id)).first()
        if msg is not None:
            msg.mode, msg.provider_ref, msg.status_updated_at = result.mode, result.provider_ref, now
        retry_safe = result.outcome == "failure" and result.retryable or (
            result.outcome == "unknown" and (result.data or {}).get("idempotent_retry_safe"))
        title = a.step_key
        if result.outcome == "success":
            a.execution_state = ExecutionState.SUCCEEDED
            if msg is not None:
                msg.status = MessageStatus.SIMULATED if result.mode == "simulated" else MessageStatus.PROVIDER_ACCEPTED
                msg.sent_at, msg.error = now, None
                if msg.patient_visible:
                    step = s.get(PlanStep, t.step_id)
                    checkin_id = (step.arguments or {}).get("checkin_id") if step else None
                    checkin = s.get(CheckIn, checkin_id) if checkin_id else None
                    if checkin is not None and checkin.episode_id == a.episode_id:
                        checkin.response_message_id = msg.id
            if fu is not None:
                fu.status = FollowUpStatus.CALENDAR_EVENT_CREATED
                fu.calendar_mode, fu.external_ref = result.mode, result.provider_ref
                fu.external_link = (result.data or {}).get("html_link")
                fu.availability_checked = False
            label = "simulated (not sent)" if result.mode == "simulated" else (
                "accepted by provider" if a.action_type != "create_calendar_event" else "event created")
            _finish(s, t, TaskStatus.SUCCEEDED, None, summary=f"{label}; ref {result.provider_ref}")
            kind = "task.succeeded"
        elif retry_safe and t.attempts < t.max_attempts:
            a.execution_state = ExecutionState.NOT_STARTED
            delay = BACKOFF_SECONDS[min(t.attempts - 1, len(BACKOFF_SECONDS) - 1)]
            t.next_attempt_at = clock.real_now() + timedelta(seconds=delay)
            _finish(s, t, TaskStatus.SCHEDULED, f"attempt {t.attempts} failed ({result.safe_error}); retrying")
            kind = "task.failed"
        elif result.outcome == "unknown":
            a.execution_state = ExecutionState.UNKNOWN
            if msg is not None:
                msg.status, msg.error = MessageStatus.UNKNOWN, result.safe_error
            _finish(s, t, TaskStatus.UNKNOWN, result.safe_error, summary=f"outcome unknown: {result.safe_error}")
            s.add(ReviewItem(
                episode_id=a.episode_id, kind="unknown_outcome", priority="high" if a.priority != "urgent" else "urgent",
                title=f"Outcome unknown: {title}", summary=result.safe_error or "",
                detail={"action_id": a.id, "execution_id": ex.id, "task_id": t.id, "mode": result.mode},
                source_type="execution", source_id=ex.id,
            ))
            kind = "task.unknown"
        else:
            a.execution_state = ExecutionState.FAILED
            if msg is not None:
                msg.status, msg.error = MessageStatus.FAILED, result.safe_error
            if fu is not None:
                fu.status = FollowUpStatus.FAILED
            _finish(s, t, TaskStatus.FAILED, result.safe_error, summary=f"failed: {result.safe_error}")
            s.add(ReviewItem(
                episode_id=a.episode_id, kind="failed_action", priority="high" if a.priority != "urgent" else "urgent",
                title=f"Action failed: {title}", summary=result.safe_error or "",
                detail={"action_id": a.id, "execution_id": ex.id, "task_id": t.id, "mode": result.mode},
                source_type="execution", source_id=ex.id,
            ))
            kind = "task.failed"
        record_event(
            s, "tool.executed",
            f"{tool} ({result.mode}) -> {result.outcome}: {title}" + (f" [{result.safe_error}]" if result.safe_error else ""),
            episode_id=a.episode_id, actor_type="worker", actor_label="Ninety-Six executor",
            detail={"execution_id": ex.id, "action_id": a.id, "outcome": result.outcome, "mode": result.mode,
                    "provider_ref": result.provider_ref, "delivery_status": result.delivery_status},
            scopes=["executions", "plan", "actions", "approvals", "messages", "followups", "overview", "reviews", "today", "checkins"],
            patient_visible=a.recipient_role in ("patient", "calendar"),
        )
        record_event(s, kind, f"{kind.split('.')[1]}: {title}", episode_id=a.episode_id, actor_type="worker",
                     actor_label="Ninety-Six executor", detail={"task_id": t.id, "status": t.status},
                     scopes=["plan", "overview"])


# ----------------------------------------------------------------------------- check-ins
def handle_open_checkin(task_id: int) -> None:
    from ..agent.checkin_templates import TEMPLATES, questions_for

    with db.session_scope() as s:
        t = s.get(ScheduledTask, task_id)
        st = s.get(PlanStep, t.step_id)
        ep = s.get(DischargeEpisode, t.episode_id)
        args = dict(st.arguments or {})
        ci = s.scalars(select(CheckIn).where(CheckIn.episode_id == ep.id, CheckIn.step_id == st.id)).first()
        if ci is None:
            tpl = args["template_id"]
            until = t.expires_at or min(st.due_at + timedelta(hours=12), ep.window_ends_at)
            ci = CheckIn(
                episode_id=ep.id, step_id=st.id, kind="scheduled", title=args.get("title", st.title),
                template_id=tpl, template_version=TEMPLATES[tpl]["version"], questions=questions_for(tpl),
                due_at=st.due_at, available_until=until, status=CheckInStatus.DUE,
            )
            s.add(ci)
            s.flush()
            s.add(ScheduledTask(
                episode_id=ep.id, checkin_id=ci.id, kind="close_checkin", due_at=until, status=TaskStatus.SCHEDULED,
                idempotency_key=f"ep{ep.id}-close-ci{ci.id}",
            ))
        _finish(s, t, TaskStatus.SUCCEEDED, None, summary=f"check-in #{ci.id} open until {iso(ci.available_until)}")
        record_event(s, "checkin.opened", f"Check-in opened: {ci.title}", episode_id=ep.id, actor_type="worker",
                     actor_label="Ninety-Six worker", detail={"checkin_id": ci.id},
                     scopes=["checkins", "today", "plan", "overview"], patient_visible=True)


def handle_close_checkin(task_id: int) -> None:
    with db.session_scope() as s:
        t = s.get(ScheduledTask, task_id)
        ci = s.get(CheckIn, t.checkin_id)
        if ci is not None and ci.status == CheckInStatus.DUE:
            ci.status = CheckInStatus.MISSED
            s.add(ReviewItem(
                episode_id=ci.episode_id, kind="missed_checkin", priority="normal",
                title=f"Overdue check-in: {ci.title}", summary="The patient did not submit this check-in in its window.",
                detail={"checkin_id": ci.id}, source_type="checkin", source_id=ci.id,
            ))
            record_event(s, "checkin.missed", f"Check-in missed: {ci.title}", episode_id=ci.episode_id,
                         actor_type="worker", actor_label="Ninety-Six worker", detail={"checkin_id": ci.id},
                         scopes=["checkins", "today", "reviews", "overview"], patient_visible=True)
        _finish(s, t, TaskStatus.SUCCEEDED)


def handle_process_checkin(task_id: int) -> None:
    from ..agent import triage as tr
    from ..agent.memory import active_facts

    with db.session_scope() as s:
        t = s.get(ScheduledTask, task_id)
        ci = s.get(CheckIn, t.checkin_id)
        if ci is None or ci.processing_state == ProcessingState.PROCESSED:
            _finish(s, t, TaskStatus.SUCCEEDED, "already processed")
            return
        ci.processing_state = ProcessingState.PROCESSING
        answers = {r.question_id: (r.answer or {}).get("value")
                   for r in s.scalars(select(CheckInResponse).where(CheckInResponse.checkin_id == ci.id))}
        flags = [(f.id, (f.value or {}).get("text", f.label))
                 for f in active_facts(s, ci.episode_id, ["red_flag"]) if not f.is_missing]
        template_id, free_text, ci_id, ep_id = ci.template_id, ci.free_text, ci.id, ci.episode_id
        qtext = {q["id"]: q["text"] for q in (ci.questions or [])}
    try:
        from ..tools.registry import read_demo_faults

        if read_demo_faults().get("triage") in ("fail", "timeout"):
            raise RuntimeError("Injected demo fault: triage processing failed")
        rules = tr.rules_triage(template_id, answers, free_text, flags)
        early_item_id = None
        if rules["level"] == "urgent":
            # A-07: rules found a red flag -> make it visible NOW, before any (possibly slow) LLM call
            with db.session_scope() as s:
                early_item_id = _urgent_item(s, ci_id, ep_id, rules["reasons"], "rules",
                                             rules.get("matched_fact_ids", []))
        context = "Discharge warning signs:\n" + "\n".join(f"- {x[1]}" for x in flags) + "\n\nAnswers:\n" + "\n".join(
            f"- {qtext.get(k, k)}: {v}" for k, v in answers.items()) + f"\n\nFree text: {free_text or '(none)'}"
        # Hybrid routing: a rules RED FLAG is final (AI never consulted); simple check-ins stay rule-only;
        # longer / unrecognised free text is read by the AI, which can only RAISE the level.
        from ..agent import drafting
        from ..llm import router

        route = router.decide("triage", tr.route_context(rules, free_text))
        out: list = []
        verdict = tr.combine(rules, tr.llm_triage(context, out) if route.use else None)
        triage_res = out[0] if out else None
        verdict["ai"] = router.tag(route, triage_res)
        drafts = []
        if verdict["level"] in ("concern", "urgent"):  # draft the reply BEFORE the write transaction
            tpl = "urgent_safety_v1" if verdict["level"] == "urgent" else "concern_reply_v1"
            try:
                drafts = drafting.prewarm(ep_id, [tpl], checkin_id=ci_id, patient_words=free_text)
            except Exception:  # noqa: BLE001 - drafting is optional; the template is the fallback
                log.exception("reply drafting failed; template will be used")
        with db.session_scope() as s:
            router.trace(s, ep_id, route, triage_res, scopes=["checkins"])
            drafting.trace_all(s, ep_id, drafts)
            _persist_triage(s, task_id, ci_id, ep_id, verdict, answers, early_item_id=early_item_id)
    except Exception as e:  # answers are already saved; record failure, retry safely
        log.exception("check-in processing failed")
        with db.session_scope() as s:
            t = s.get(ScheduledTask, task_id)
            ci = s.get(CheckIn, ci_id)
            ci.processing_state = ProcessingState.FAILED
            ci.processing_error = f"{type(e).__name__}"
            if t.attempts < t.max_attempts:
                t.next_attempt_at = clock.real_now() + timedelta(seconds=BACKOFF_SECONDS[0])
                _finish(s, t, TaskStatus.SCHEDULED, "processing failed; will retry (answers are saved)")
            else:
                _finish(s, t, TaskStatus.FAILED, "processing failed (answers are saved)")
            s.add(ReviewItem(
                episode_id=ep_id, kind="processing_failure", priority="high",
                title="Check-in could not be processed automatically",
                summary="The patient's answers are saved. Please review them directly.",
                detail={"checkin_id": ci_id, "error": type(e).__name__}, source_type="checkin", source_id=ci_id,
            ))
            record_event(s, "checkin.processing_failed", "Check-in processing failed (answers saved)",
                         episode_id=ep_id, actor_type="worker", actor_label="Ninety-Six worker",
                         detail={"checkin_id": ci_id}, scopes=["checkins", "today", "reviews"], patient_visible=True)


def _urgent_item(s: Session, ci_id: int, ep_id: int, reasons: list[str], source: str, matched: list[int]) -> int:
    ci = s.get(CheckIn, ci_id)
    existing = s.scalars(select(ReviewItem).where(ReviewItem.source_type == "checkin", ReviewItem.source_id == ci_id,
                                                  ReviewItem.kind == "urgent_concern")).first()
    if existing is not None:
        return existing.id
    item = ReviewItem(
        episode_id=ep_id, kind="urgent_concern", priority="urgent", requires_role="doctor",
        title="URGENT: check-in matches discharge red flags", summary="; ".join(reasons),
        detail={"checkin_id": ci_id, "reasons": reasons, "source": source, "matched_fact_ids": matched},
        source_type="checkin", source_id=ci_id,
    )
    s.add(item)
    s.flush()
    ci.staff_review_state = StaffReviewState.AWAITING
    record_event(s, "review.created", item.title, episode_id=ep_id, actor_type="agent", actor_label="Ninety-Six triage",
                 detail={"review_item_id": item.id, "priority": item.priority, "checkin_id": ci_id},
                 scopes=["reviews", "overview", "episode", "episodes", "checkins"])
    return item.id


def _persist_triage(s: Session, task_id: int, ci_id: int, ep_id: int, verdict: dict[str, Any], answers: dict,
                    early_item_id: Optional[int] = None) -> None:
    from ..agent.memory import active_facts, add_fact, log_usage
    from ..agent.replanner import patient_response_replan

    t = s.get(ScheduledTask, task_id)
    ci = s.get(CheckIn, ci_id)
    ep = s.get(DischargeEpisode, ep_id)
    level, reasons = verdict["level"], list(verdict["reasons"])
    ci.triage_level, ci.triage_reasons, ci.triage_source = level, reasons, verdict["source"]
    ci.processing_state, ci.processed_at, ci.processing_error = ProcessingState.PROCESSED, clock.now(), None
    new_facts = []
    for r in reasons:
        cat = "adherence" if "medicine" in r.lower() or "insulin" in r.lower() or "dose" in r.lower() else "symptom"
        new_facts.append(add_fact(s, ep_id, category=cat, label=r[:160], value={"text": r, "checkin_id": ci_id, "level": level},
                                  source_kind=FactSourceKind.PATIENT_REPORTED, source_ref=f"checkin:{ci_id}",
                                  source_excerpt=None, created_by="patient"))
    if ci.free_text:
        new_facts.append(add_fact(s, ep_id, category="note", label="Patient's own words", value={"text": ci.free_text, "checkin_id": ci_id},
                                  source_kind=FactSourceKind.PATIENT_REPORTED, source_ref=f"checkin:{ci_id}",
                                  source_excerpt=ci.free_text, created_by="patient"))
    flags = [f for f in active_facts(s, ep_id, ["red_flag"]) if not f.is_missing]
    log_usage(s, ep_id, flags, used_for="triage", ref_type="checkin", ref_id=ci_id, note=f"triage {level}")
    item = None
    if early_item_id is not None:
        early = s.get(ReviewItem, early_item_id)
        early.summary = "; ".join(reasons)
        early.detail = {**dict(early.detail or {}), "reasons": reasons, "source": verdict["source"]}
        ci.staff_review_state = StaffReviewState.AWAITING
    elif level == "urgent":
        item = ReviewItem(
            episode_id=ep_id, kind="urgent_concern", priority="urgent", requires_role="doctor",
            title="URGENT: check-in matches discharge red flags", summary="; ".join(reasons),
            detail={"checkin_id": ci_id, "reasons": reasons, "source": verdict["source"],
                    "matched_fact_ids": verdict.get("matched_fact_ids", [])},
            source_type="checkin", source_id=ci_id,
        )
    elif level == "concern":
        item = ReviewItem(
            episode_id=ep_id, kind="concern", priority="high", requires_role="any",
            title="Check-in needs nurse follow-up", summary="; ".join(reasons),
            detail={"checkin_id": ci_id, "reasons": reasons, "source": verdict["source"]},
            source_type="checkin", source_id=ci_id,
        )
    if item is not None:
        s.add(item)
        s.flush()  # visible immediately, in the same transaction as the triage result
        ci.staff_review_state = StaffReviewState.AWAITING
        record_event(s, "review.created", item.title, episode_id=ep_id, actor_type="agent", actor_label="Ninety-Six triage",
                     detail={"review_item_id": item.id, "priority": item.priority, "checkin_id": ci_id},
                     scopes=["reviews", "overview", "episode", "episodes", "checkins"])
    elif early_item_id is None:
        ci.staff_review_state = StaffReviewState.NOT_REQUESTED
    record_event(s, "checkin.processed", f"Check-in processed by agent: {level} ({verdict['source']})",
                 episode_id=ep_id, actor_type="agent", actor_label="Ninety-Six triage",
                 detail={"checkin_id": ci_id, "level": level, "reasons": reasons, "source": verdict["source"]},
                 scopes=["checkins", "today", "memory", "episode", "overview"], patient_visible=True)
    _finish(s, t, TaskStatus.SUCCEEDED, None)
    if level != "ok":
        fact_ids = [f.id for f in new_facts] + list(verdict.get("matched_fact_ids", []))
        patient_response_replan(s, ep, checkin_id=ci_id, level=level, reasons=reasons, fact_ids=fact_ids)


# ----------------------------------------------------------------------------- internal steps
def handle_medication_review(task_id: int) -> None:
    from ..agent.planner import medication_names

    with db.session_scope() as s:
        t = s.get(ScheduledTask, task_id)
        ep = s.get(DischargeEpisode, t.episode_id)
        names = medication_names(s, ep)
        adapter = get_adapter("medication_info", for_execution=True)
        ex = ToolExecution(episode_id=ep.id, task_id=t.id, tool="medication_info",
                           provider="openfda" if adapter.mode() == "live" else "local", mode=adapter.mode(),
                           attempt=t.attempts, idempotency_key=f"ep{ep.id}-medreview-{t.id}",
                           request_summary={"medications": names}, outcome="pending")
        s.add(ex)
        s.flush()
        ex_id, ep_id = ex.id, ep.id
    try:
        result = adapter.execute({"medications": names}, idempotency_key=f"ep{ep_id}-medreview-{task_id}")
    except Exception as e:  # noqa: BLE001
        result = ToolResult("failure", "simulated", "local", safe_error=f"medication lookup error ({type(e).__name__})",
                            retryable=True)
    with db.session_scope() as s:
        t = s.get(ScheduledTask, task_id)
        _persist_med_review(s, t, s.get(DischargeEpisode, ep_id), s.get(ToolExecution, ex_id), result)


def _persist_med_review(s: Session, t: ScheduledTask, ep: DischargeEpisode, ex: ToolExecution, result: ToolResult) -> None:
    from ..agent.memory import add_fact
    from ..agent.replanner import tool_finding_replan

    ex.outcome, ex.mode, ex.provider = result.outcome, result.mode, result.provider
    ex.provider_status, ex.delivery_status, ex.safe_error = result.provider_status, result.delivery_status, result.safe_error
    ex.data, ex.finished_at = dict(result.data or {}), clock.now()
    if result.outcome != "success":
        if t.attempts < t.max_attempts:
            t.next_attempt_at = clock.real_now() + timedelta(seconds=BACKOFF_SECONDS[0])
            _finish(s, t, TaskStatus.SCHEDULED, f"medication lookup failed ({result.safe_error}); retrying")
        else:
            _finish(s, t, TaskStatus.FAILED, result.safe_error, summary=f"failed: {result.safe_error}")
        return
    for old in s.scalars(select(PatientFact).where(PatientFact.episode_id == ep.id, PatientFact.category == "medication_info",
                                                    PatientFact.active.is_(True))):
        old.active = False
    data = result.data or {}
    lim = data.get("limitations", "")
    for d in data.get("drugs", []):
        src = "tool:openfda" if d.get("source") == "openFDA drug label" else "tool:local_reference"
        add_fact(s, ep.id, category="medication_info", label=f"Label information: {d['name']}",
                 value={"text": d.get("excerpt") or d.get("note") or "No label text retrieved.", **d, "limitations": lim},
                 source_kind=FactSourceKind.TOOL, source_ref=src, source_excerpt=d.get("excerpt"))
    finding_ids, findings = [], []
    for f in data.get("interactions", []):
        fact = add_fact(s, ep.id, category="medication_info", label="Flagged combination: " + " + ".join(f["drugs"]),
                        value={"text": f["finding"], "drugs": f["drugs"], "source": f["source"], "limitations": lim},
                        source_kind=FactSourceKind.TOOL, source_ref="tool:local_interaction_rules")
        finding_ids.append(fact.id)
        findings.append(f["finding"])
    found = sum(1 for d in data.get("drugs", []) if d.get("label_status") == "found")
    src = (f"openFDA: {found} labels found, {data.get('openfda_failures', 0)} lookups failed" if result.mode == "live"
           else "openFDA not queried; local reference notes only")
    summary = (f"{len(data.get('drugs', []))} medicines checked ({src}); "
               f"{len(findings)} combination flag(s) for clinician review")
    _finish(s, t, TaskStatus.SUCCEEDED, None, summary=summary)
    record_event(s, "tool.executed", f"medication_info ({result.mode}) -> {summary}", episode_id=ep.id,
                 actor_type="worker", actor_label="Ninety-Six executor",
                 detail={"execution_id": ex.id, "findings": findings, "limitations": lim,
                         "openfda_calls": data.get("openfda_calls"), "openfda_failures": data.get("openfda_failures")},
                 scopes=["executions", "memory", "plan"])
    s.flush()
    if findings:
        tool_finding_replan(s, ep, findings=findings, fact_ids=finding_ids)


def complete_medication_review_locally(session: Session, ep: DischargeEpisode) -> None:
    """Demo seeding only: run the pending draft-stage medication review in the caller's session using
    the local reference table (no network call, mode=simulated)."""
    from ..agent.planner import medication_names
    from ..tools.medication_info import MedicationInfoAdapter

    t = session.scalars(select(ScheduledTask).where(ScheduledTask.episode_id == ep.id, ScheduledTask.kind == "medication_review",
                                                    ScheduledTask.status.in_([TaskStatus.SCHEDULED, TaskStatus.READY]))).first()
    if t is None:
        return
    names = medication_names(session, ep)
    t.attempts += 1
    ex = ToolExecution(episode_id=ep.id, task_id=t.id, tool="medication_info", provider="local", mode="simulated",
                       attempt=t.attempts, idempotency_key=f"ep{ep.id}-medreview-{t.id}",
                       request_summary={"medications": names, "local_only": True}, outcome="pending")
    session.add(ex)
    session.flush()
    result = MedicationInfoAdapter().execute({"medications": names, "local_only": True}, idempotency_key=ex.idempotency_key)
    _persist_med_review(session, t, ep, ex, result)
    session.flush()


def handle_staff_review(task_id: int) -> None:
    with db.session_scope() as s:
        t = s.get(ScheduledTask, task_id)
        st = s.get(PlanStep, t.step_id)
        args = dict(st.arguments or {})
        items = s.scalars(select(ReviewItem).where(ReviewItem.episode_id == t.episode_id, ReviewItem.kind == args.get("kind"))).all()
        mine = [i for i in items if (i.detail or {}).get("step_key") == st.step_key]
        if any(i.status == ReviewStatus.RESOLVED for i in mine):
            _finish(s, t, TaskStatus.SUCCEEDED, None, summary="review already completed")
            return
        item = next((i for i in mine if i.status != ReviewStatus.RESOLVED), None)
        if item is None:
            item = ReviewItem(
                episode_id=t.episode_id, kind=args.get("kind", "medication_review"), priority="high",
                requires_role=args.get("requires_role", "doctor"), title=args.get("title", st.title),
                summary=args.get("summary", ""), detail={"step_key": st.step_key, "finding_fact_ids": args.get("finding_fact_ids", [])},
                source_type="step", source_id=st.id,
            )
            s.add(item)
            s.flush()
            record_event(s, "review.created", item.title, episode_id=t.episode_id, actor_type="agent",
                         actor_label="Ninety-Six agent", detail={"review_item_id": item.id, "step_key": st.step_key},
                         scopes=["reviews", "overview", "plan"])
        from ..agent.plan_store import set_task_status

        set_task_status(s, t, TaskStatus.AWAITING_APPROVAL, f"waiting for doctor review (review item #{item.id})")
        _release(t)


def handle_generate_summary(task_id: int) -> None:
    from ..agent.plan_store import set_task_status
    from ..agent.summary import write_summary

    with db.session_scope() as s:
        t = s.get(ScheduledTask, task_id)
        ep = s.get(DischargeEpisode, t.episode_id)
        _finish(s, t, TaskStatus.SUCCEEDED, None, summary="summary generated; awaiting doctor closure review")
        s.flush()
        cancelled_approved, urgent_open = [], []
        for other in s.scalars(select(ScheduledTask).where(
                ScheduledTask.episode_id == ep.id, ScheduledTask.id != t.id,
                ScheduledTask.status.in_([TaskStatus.SCHEDULED, TaskStatus.AWAITING_APPROVAL, TaskStatus.READY]))):
            a = s.get(ProposedAction, other.action_id) if other.action_id else None
            if a is not None and a.priority == "urgent" and a.execution_state == ExecutionState.NOT_STARTED                     and a.approval_state in (ApprovalState.PENDING, ApprovalState.APPROVED):
                urgent_open.append(a)  # A-11: stays live; surfaced below, never silently skipped
                continue
            set_task_status(s, other, TaskStatus.SKIPPED, "monitoring window ended before this ran")
            if a is not None and a.execution_state == ExecutionState.NOT_STARTED:
                a.execution_state = ExecutionState.CANCELLED
                if a.approval_state in (ApprovalState.APPROVED, ApprovalState.ROUTINE):
                    cancelled_approved.append(a)
                    a.approval_state = ApprovalState.INVALIDATED
                    a.invalidated_reason = "monitoring window ended before it ran; not sent"
        if urgent_open:
            s.add(ReviewItem(
                episode_id=ep.id, kind="failed_action", priority="urgent", requires_role="doctor",
                title=f"{len(urgent_open)} urgent action(s) still open after the monitoring window ended",
                summary="They remain available to approve/send: " + ", ".join(a.step_key for a in urgent_open),
                detail={"action_ids": [a.id for a in urgent_open]}, source_type="episode", source_id=ep.id))
        if cancelled_approved:
            s.add(ReviewItem(
                episode_id=ep.id, kind="failed_action", priority="normal",
                title=f"{len(cancelled_approved)} approved action(s) were not sent before the window ended",
                summary=", ".join(a.step_key for a in cancelled_approved),
                detail={"action_ids": [a.id for a in cancelled_approved]}, source_type="episode", source_id=ep.id))
            if other.checkin_id:
                ci = s.get(CheckIn, other.checkin_id)
                if ci is not None and ci.status == CheckInStatus.DUE:
                    ci.status = CheckInStatus.MISSED
        s.flush()
        write_summary(s, ep)
        ep.status = EpisodeStatus.AWAITING_CLOSURE_REVIEW
        ep.monitoring_ended_at = clock.now()
        s.add(ReviewItem(
            episode_id=ep.id, kind="closure_review", priority="normal", requires_role="doctor",
            title="96-hour summary ready for closure review",
            summary="Monitoring window ended. Review outcomes and unresolved items. This is not a medical clearance.",
            detail={}, source_type="episode", source_id=ep.id,
        ))
        record_event(s, "episode.updated", "Monitoring window ended - awaiting closure review", episode_id=ep.id,
                     actor_type="worker", actor_label="Ninety-Six worker", detail={"status": ep.status},
                     scopes=["episode", "episodes", "overview", "summary", "reviews", "today", "plan"], patient_visible=True)
        ep_id = ep.id
    try:
        from ..agent.summary import ai_clinical_note

        ai_clinical_note(ep_id)
    except Exception:  # noqa: BLE001 - the template note is already saved
        log.exception("AI clinical note failed; template summary kept")


HANDLERS = {
    "execute_action": handle_execute_action,
    "open_checkin": handle_open_checkin,
    "close_checkin": handle_close_checkin,
    "process_checkin": handle_process_checkin,
    "medication_review": handle_medication_review,
    "staff_review": handle_staff_review,
    "generate_summary": handle_generate_summary,
}

__all__ = ["execute_claimed", "HANDLERS", "datetime"]
