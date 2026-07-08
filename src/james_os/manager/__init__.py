"""The Brand Manager layer — bm2.0 ported natively onto the james-os
substrate (docs/unification-plan.md, docs/feature-ledger.md).

P1 modules: contracts (shared value sets + D2 confidence rubric), profile
(append-only profile_fields envelope), actions (action_items follow-up
cards + daily digest), state_machine (D5 lifecycle edges on the actions
queue), runs (job_runs bookkeeping + token accounting), and providers/
(the D8 mock/live provider layer).

Everything here is tenant-scoped through db.acquire()'s RLS binding and
gated behind settings.manager_v2 at the API/scheduler layer.
"""
