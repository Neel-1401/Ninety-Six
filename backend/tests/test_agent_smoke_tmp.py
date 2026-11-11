from sqlalchemy import select

from app import db
from app.models import DischargeEpisode, PlanVersion, ProposedAction, ScheduledTask, PlanStep
from tests.test_agent_helpers import setup_active


def test_smoke(session):
    ids = setup_active()
    with db.session_scope() as s:
        ep = s.get(DischargeEpisode, ids["episode"])
        print("status", ep.status, "risk", ep.risk_category, ep.risk_reasons, ep.extraction_notes)
        for pv in s.scalars(select(PlanVersion).where(PlanVersion.episode_id == ep.id)):
            print("PV", pv.version, pv.status, pv.trigger, [c["change"] + ":" + c["step_key"] for c in pv.change_summary if c["change"] != "kept"])
        for st in s.scalars(select(PlanStep).where(PlanStep.plan_version_id == ep.current_plan_version_id)):
            print("S", st.step_key, st.status, st.depends_on, st.result_summary)
        for a in s.scalars(select(ProposedAction).where(ProposedAction.episode_id == ep.id)):
            print("A", a.id, a.step_key, a.approval_state, a.execution_state, a.expected_mode)
        for t in s.scalars(select(ScheduledTask).where(ScheduledTask.episode_id == ep.id)):
            print("T", t.id, t.kind, t.status, t.status_reason)
