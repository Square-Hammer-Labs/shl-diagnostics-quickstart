---
name: shl-repair-loop
description: >-
  Run the SHL diagnose-and-repair loop when a robot policy fails in simulation or on hardware.
  Use whenever a run, pick or task fails and the user wants it diagnosed or fixed: collect the
  telemetry record, POST it to the SHL diagnostics service (/v1/diagnose) with the task YAML and the
  policy code bundle, OBEY THE ACTION AXIS before editing any code (hardware/environment verdicts
  mean stop iterating and escalate — do not thrash), apply or write a minimal fix from the returned
  feedback, re-simulate to verify, and ALWAYS write the outcome back to /v1/lessons (SOLVED,
  ESCALATED, or THRASH) so the next matching failure is cheaper. Triggers: "the robot failed",
  "diagnose this run", "fix this policy", "why did the pick fail", "run the SHL loop".
---

# SHL repair loop — diagnose a robot failure, fix it, verify, write it back

The SHL diagnostics service reads the robot's own physics telemetry (joint torques, tracking error,
Jacobian conditioning, contact forces) and returns the failure's cause, the recommended action, and
a feedback document built for a coding agent. This skill is the loop discipline around that call.
The service never executes your code — verification is YOUR re-simulation step, and reporting the
outcome back is not optional (that write-back is what makes later failures cheaper for everyone).

The customer API is versioned under `/v1`. Endpoint details, curl examples, and the bundle format:
[reference.md](reference.md).

## Your system (fill these in before you use this skill)

The loop below is the same everywhere. These lines are not, so state them here once. An agent that
has to guess any of them will guess wrong.

- **Service:** `SHL_SERVICE_URL` = the endpoint SHL sent you; `SHL_SERVICE_TOKEN` = your bearer
  token. Put both in your environment. Nothing to install — the diagnosis runs on SHL's side.
- **Telemetry:** `<how a failed run becomes a record>` — the command or function that produces the
  per-step JSON described in section 4 of the quickstart README.
- **Task description:** `<the file that says what the robot had to do>` — sent verbatim as
  `task_yaml`. Include `task_type` and `goals` when you have them.
- **Policy edit surface:** `<the file a fix may change>`, plus the modules it imports and must not
  change.
- **Verify:** `<the command that runs the task again and grades it>`. Step 4 depends on this, and a
  fix that no new run has confirmed is not a fix.

## Preconditions (validate before starting)

1. **Service reachable** — `GET $SHL_SERVICE_URL/health` returns `{"status": "ok"}`. If
   `SHL_SERVICE_URL` is unset, ask the user (a local service runs at `http://localhost:8000`).
2. **Telemetry available** — a per-step `record` of the FAILED run. It is the one telemetry input:
   the service calculates the feature set from it. If "Your system" above does not say how a record
   is produced, ask the user before you go on.
3. **Task YAML** — the task's description: intent, step budget, `task_type`, `goals`, and any
   deterministic success criteria. Send it verbatim as `task_yaml`. With no `goals`, the service
   translates the analyzers into them and reports which goals the run reached.
4. **Policy edit surface** — identify the entrypoint file a fix may change, any read-only modules it
   imports, and the interface contract a rewrite must preserve. This becomes the `policy` bundle and
   its `shl-policy.yaml` manifest. Never bundle your whole system — only the edit surface, and never any credentials.

## The loop (max 3 iterations, then stop and report)

### 1. Diagnose
POST `/v1/diagnose` with `record`, `task_yaml`, and the `policy` bundle. Add
`coding_agent` with `mode` = `shl` if you want the service's proposed patch as a starting point.
Corrections that need the robot model (e.g. an FK-derived ready pose) go in `robot_corrections` —
calculate them on your own side.

### 2. Obey the action axis FIRST — the anti-thrash rule
Read `diagnosis.action` before touching any code:

- **`hardware` or `environment`** — there is NO code fix. Do not edit the policy. Present the root
  cause and evidence to the user, recommend the physical/task change, and write back `ESCALATED`
  (step 5). Iterating on an unfixable cause is the exact failure mode this loop exists to prevent.
- **`replan`** — the execution was sound; the target/perception was wrong. Surface that; only touch
  perception/targeting code if the user directs it.
- **`code`** — proceed to step 3.

Also check `feedback.always_escalated`: if prior incidents with this signature only ever escalated,
treat the failure as hardware/environment even when the current diagnosis says `code`.

### 3. Fix (minimal diff)
- If the response carries a `patch` with `action` = `rewrite_policy`, review its `diff`, then apply it.
- Otherwise write the fix yourself from `feedback`:
  - Use `feedback.corrections` **verbatim** — each carries a measured number ("lower the grasp target
    by ~0.063 m") in its `prompt_text`, not a suggestion. A correct category without the concrete
    value is how loops thrash.
  - Honor `feedback.known_dead_ends` — an approach that already THRASHed on this signature must not
    be repeated; pick a structurally different fix.
  - Follow `feedback.known_fix` when present — a matching signature was already solved once.
  - Make the SMALLEST change that addresses the diagnosis (`feedback.instructions`); keep every
    untouched behavior byte-identical (especially gripper commands and phase triggers).
  - Read `diagnosis.alternatives` before you pick the fix. When the main `confidence` is low and the
    first alternative is close behind it, the telemetry does not separate the two causes: prefer the
    smallest change that is correct under BOTH, and name the ambiguity in your write-back. A fix
    that is only correct under the winner is a coin flip you are not being told you took.

### 4. Verify — yours, not the service's
Re-run the task in your own system with the patched policy. The service proposed; only your
re-simulation confirms. Judge success by the task's own deterministic analyzers, not by eyeballing.

### 5. Write back — ALWAYS, whatever happened
POST `/v1/lessons`. The short form is the `response_id` from step 1 and the `outcome`:

```json
{"response_id": "<from the diagnose call>", "outcome": "SOLVED", "solved_at": 2}
```

The service reads the diagnosis and the telemetry from its own record of that call, so do not send
them again. Add `after_src` on a solve. What each outcome means:
- **SOLVED** — include the working policy source (`after_src`) and the iteration count.
- **ESCALATED** — the diagnosis said hardware/environment and you stopped. Still write it back: it
  builds the escalation record that saves the next agent from thrashing.
- **THRASH** — you iterated and never solved it. Write back the fix direction that failed; it becomes
  an anti-lesson. A reported thrash is as valuable as a solve.

### 6. Iterate or stop
If unsolved and the action is still `code`, loop to step 1 with the new run's telemetry
(≤3 total iterations). On the third failure, stop, write back THRASH, and present the full trail
(diagnoses, fixes tried, verification results) to the user.

## Rules

- Never edit code before reading `diagnosis.action` (step 2).
- Never repeat an approach named in `known_dead_ends`.
- Never skip the write-back, including on failure.
- Never send secrets, credentials, or your whole system in the policy bundle — the edit surface only.
- Read `warnings` on every response. A telemetry problem the service recovered from is often why a
  signal you wanted came back `null`.
- Do not look for a prior fix yourself. `/v1/diagnose` already applies the lesson library when you
  send `policy`, and it returns what it found in `feedback.known_fix` and `feedback.known_dead_ends`.
  Read those before you write any code.
