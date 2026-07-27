# Reference — endpoints, payloads, bundle format

Machine-readable contract: `docs/openapi.json` (live Swagger at `/docs`). `$URL` below is
`$SHL_SERVICE_URL` (local default `http://localhost:8000`). The customer API is versioned under
`/v1`; `/health` is unversioned.

**Conventions.** Every `/v1/*` response carries a `response_id` (also the `X-SHL-Response-Id` header)
— quote it to trace a call. The customer's own model keys travel in `vlm_model.api_key` /
`coding_agent.api_key`; they route the call and are never logged or echoed back. When the deployment
has auth on (`SHL_AUTH=1`), send `Authorization: Bearer <your token>` on every `/v1` call; `/health`
is always open.

## Health

```bash
curl -s $URL/health          # -> {"status": "ok", "service": "shl-diagnostics", "version": ...}
```

## Diagnose (with code-aware policy bundle + optional SHL coder)

```bash
curl -s $URL/v1/diagnose -H 'content-type: application/json' -d '{
  "record": {"meta": {"task": "box_lift", "task_type": "pick_and_place",
                      "control_dt": 0.02, "finger_offset": 0.115},
             "t": [0.0, 0.02, 0.04], "joint_track_err": [0.01, 0.02, 0.09],
             "grip_cmd": [0.0, 255.0, 255.0], "ctrl_range": [[-3.14, 3.14]]},
  "task_yaml": "task_spec:\n  inline: Lift the box off the table.\nsteps: 500\ngoals:\n  - object_lifted: {min_rise_m: 0.12}\n",
  "policy": {"files": [{"path": "policy.py", "content": "GRASP_Z = 0.21\n..."}]},
  "coding_agent": {"mode": "shl"}
}'
```

Request fields (`record` is required; everything else is optional):

| field | meaning |
|---|---|
| `record` | the telemetry from the run — the one telemetry input; the service calculates the feature set |
| `baseline` | the telemetry from an empty-gripper run on the same path, to isolate the payload load |
| `task_yaml` | what the robot had to do, as YAML — `task_spec.inline`, `steps`, `task_type`, `goals`, `task_analyzers`; other keys are kept. With no `goals`, the service translates `task_analyzers` into them |
| `task_spec` | freeform intent string; `task_yaml` wins when both present |
| `policy` | the code bundle (below) — makes the diagnosis code-aware, adds `feedback` |
| `coding_agent` | who writes the fix: `mode` = `customer` (default) or `shl`. `shl` (requires `policy`) also returns a proposed `patch`. Optional `provider`/`endpoint`/`model`/`api_key` route the coder |
| `vlm_model` | the diagnosis VLM: `{provider, model, api_key, endpoint}` — `provider` is `anthropic`/`google`/`openai_compatible` |
| `robot_corrections` | caller-computed correction strings needing the robot model (FK values etc.); folded into `feedback.corrections` |
| `tags` | free-form run tags for later log filtering |

Response fields:

| field | meaning |
|---|---|
| `diagnosis` | `category`, `subtype`, `root_cause`, `action` (**code / replan / hardware / environment**), `evidence`, `confidence`, `alternatives` |
| `diagnosis.alternatives` | the other causes that fit the evidence, most likely first — `{category, subtype, confidence, reason}`; empty when one cause fits. `feedback` and `patch` are built for the MAIN diagnosis only |
| `feedback` | for the repair agent (present when `policy` sent): `corrections[]` (each `{type, signal, value, unit, direction, prompt_text}` — measured numbers, use `prompt_text` verbatim), `known_fix` (a matching past SOLVED failure), `known_dead_ends[]` (matching THRASHes to avoid), `always_escalated`, `instructions`, `prompt_text` (the whole briefing assembled) |
| `patch` | when `coding_agent.mode` = `shl`: `action` (`rewrite_policy` \| `escalate`), `reasoning`, `files[]` with `path`, `new_source`, `diff` (unified) |

## The policy bundle

Inline files (≤ ~3 files) or a base64 tar.gz; 2 MB decoded cap, UTF-8 text, no `..`/absolute paths:

```jsonc
{"policy": {"files": [{"path": "policy.py", "content": "..."}]}}
{"policy": {"archive_b64": "<base64 tar.gz>"}}
```

Manifest — `shl-policy.yaml` inside the archive or the inline `manifest` field:

```yaml
entrypoint: policy.py          # the file a fix may rewrite (default: the only .py file)
interface: |                   # contract a rewrite must preserve
  policy(obs, step, env) -> ctrl array of length env.nu; optional reset().
context: [util.py]             # read-only imports, never edited
runtime_deps: [numpy, mujoco]  # declared, not vendored — ambient where the code runs
```

The bundle is a read+edit surface for the model, not a deployable artifact: the service never
executes it (verification is your re-simulation).

## Report the outcome

After you verify: report what happened. ALWAYS — SOLVED, ESCALATED, or THRASH.

```bash
curl -s $URL/v1/lessons -H "$AUTH" -H 'content-type: application/json' -d '{
  "response_id": "<from the diagnose call>",
  "outcome": "SOLVED",
  "after_src": "<the source that worked>",
  "solved_at": 2
}'
```

The service reads the diagnosis and the telemetry from its own record of that call, so you do not
send them a second time. If you have no `response_id`, send `scenario`, `features` and `diag`
instead.

There is no route to read the lessons back. Retrieval is internal: `/v1/diagnose` applies the
library for you when you send `policy`, and returns what it found in `feedback.known_fix` and
`feedback.known_dead_ends`.
