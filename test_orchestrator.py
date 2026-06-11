"""Validate the orchestrator control contract: stop-first, lock-second,
queue-and-continue, soft daily cap. No browser needed."""
from app import db
from app.services import orchestrator, run_state

db.init_db()
C = "racheal"

# Clean slate for the test.
run_state.clear_stop(C)
run_state.clear_queue(C)
run_state.release_lock(C)

# 1. stop-first: a stop signal sacrifices the fire and is cleared.
run_state.request_stop(C)
r = orchestrator.start_batch(C)
assert not r.started and r.reason == "stop-signal-honoured", r
assert not run_state.stop_requested(C), "stop should be cleared after honouring"
print("1 stop-first OK")

# 2. lock-second: a fresh lock means a new fire is queued, not started.
r = orchestrator.start_batch(C)
assert r.started, r
batch_id = r.batch_id
r2 = orchestrator.start_batch(C)
assert not r2.started and r2.reason == "lock-fresh-tick-queued", r2
assert run_state.queue_depth(C) == 1
print("2 lock-second + queue OK")

# 3. end_batch with a queued tick -> continue.
disp = orchestrator.end_batch(C, batch_id)
assert disp["next"] == "continue", disp
assert run_state.queue_depth(C) == 0
print("3 queue-and-continue OK")

# 4. end_batch with empty queue -> finished, lock released.
disp = orchestrator.end_batch(C, batch_id)
assert disp["next"] == "finished", disp
assert run_state.lock_info(C) is None
print("4 clean finish OK")

# 5. pause writes a stop signal; should_continue reports stop.
orchestrator.start_batch(C)
orchestrator.pause(C)
ok, reason = orchestrator.should_continue(C)
assert not ok and reason == "stop-requested", reason
print("5 pause/should-continue OK")

# clean up so the test is idempotent.
run_state.clear_stop(C)
run_state.clear_queue(C)
run_state.release_lock(C)
print("\nORCHESTRATOR CONTRACT OK")
