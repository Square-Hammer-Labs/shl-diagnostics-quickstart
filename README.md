# SHL Diagnostics API — getting started

Your robot fails a task. You send the telemetry from that run. The service tells you the cause, the
action to take, and the evidence for both.

The service never touches your robot, and it never runs your code. It reads telemetry only.

- **API reference:** https://docs.squarehammerlabs.com
- **OpenAPI document:** https://docs.squarehammerlabs.com/openapi.json
- **To get an endpoint and a token:** bradford@squarehammerlabs.com

> The links above go live when the documentation site is published. Until then, use the OpenAPI
> document that we send you with your token.

---

## 1. Terms

Read these four first. The rest of this page uses them.

| term | meaning |
|---|---|
| **run** | One execution of a task, from start to stop. |
| **step** | One control cycle in a run. A run of 645 steps at 0.02 s is 12.9 seconds long. |
| **channel** | One array with one value for each step. `joint_track_err` is a channel. |
| **feature set** | The compact summary that the service calculates from your telemetry. |

Two more that you meet later:

| term | meaning |
|---|---|
| **policy** | Your source code that controls the robot. You can send it, and then the service gives the cause in terms of your actual code. |
| **lesson** | What the service learned from one failure that somebody resolved. The service applies lessons for you. You do not manage them. |

## 2. Set your endpoint and token

```bash
export SHL_URL=https://api.squarehammerlabs.com
export SHL_TOKEN=<the token we sent you>
```

Test the service:

```bash
curl -s $SHL_URL/health
```

`/health` is open, thus a good answer here does **not** prove that your token is correct. To test
your token, call a `/v1` route. A 401 or a 403 is a problem with your token, not with the service.

## 3. Your first diagnosis

This example sends a small feature set. Copy it exactly.

```bash
curl -s $SHL_URL/v1/diagnose \
  -H "Authorization: Bearer $SHL_TOKEN" \
  -H 'content-type: application/json' \
  -d '{
    "features": {"global": {
      "tool_converged": true, "converged_but_no_load": true, "load_appeared": false,
      "fingertip_gap_above_box_m": 0.063
    }},
    "task_spec": "Lift the box off the table."
  }'
```

You get a `diagnosis` object:

```json
{
  "diagnosis": {
    "category": "grasp",
    "subtype": "geometry_miss_high",
    "root_cause": "The tool went to its commanded position, but no load appeared...",
    "action": "code",
    "fix_direction": "Lower the grasp target by approximately 0.063 m.",
    "evidence": [{"signal": "fingertip_gap_above_box_m", "reading": "0.063 m"}],
    "confidence": 0.95
  },
  "response_id": "0f3c9a1b2d4e5f60"
}
```

**Read `action` before you change any code.**

| `action` | what it means |
|---|---|
| `code` | A change to your program can correct the failure. |
| `replan` | The movement plan or the target is wrong, not the code that executes it. |
| `hardware` | NO change to your program will correct this. Stop. Tell a person. |
| `environment` | NO change to your program will correct this. Stop. Tell a person. |

Keep the `response_id`. You send it back in step 6.

## 4. Send your own telemetry

In step 3 you sent a feature set. Usually you send the raw telemetry as `record` instead, and the
service calculates the feature set for you.

A record holds one array for each measurement, with one value at each step:

```jsonc
{
  "record": {
    "meta": {"task": "box_lift", "control_dt": 0.02},
    "t":               [0.0, 0.02, 0.04],       // one value for each step
    "grip_cmd":        [0.0, 0.0, 255.0],       // gripper close command, 0 to 255
    "joint_track_err": [0.01, 0.02, 0.28],      // radians
    "ctrl":            [[0,0,0,0,0,0], "..."],  // one row for each STEP
    "ctrl_range":      [[-3.14, 3.14], "..."]   // one row for each ACTUATOR
  }
}
```

Two worked examples, ready to download:

| file | what it is |
|---|---|
| [`record.min.json`](https://docs.squarehammerlabs.com/examples/record.min.json) | The least that still gives a diagnosis. 12 steps, CORE tier only. |
| [`record.full.json`](https://docs.squarehammerlabs.com/examples/record.full.json) | A real 645-step failure: a payload that lifts, then slips. All tiers. |
| [`bundle.example.json`](https://docs.squarehammerlabs.com/examples/bundle.example.json) | The feature set for that record, if you prefer to send `features`. |

Every field is optional. Send the data that you have. An absent channel is not an error: the signals
that use it become `null`, and the failure categories that need those signals become unavailable.

### What each channel costs you

Approximately one third of the signals need equipment that a production robot does not have. Each
field in the reference shows its tier, thus you can see what you get for what you send.

| tier | what it takes | it gives you |
|---|---|---|
| **CORE** | Data that every robot reports: joint positions and speeds, the command you applied, tracking error, gripper command. | kinematics, planning |
| **DERIVED** | Data that you calculate with your model of the robot: the smallest singular value of the Jacobian, the tool position from forward kinematics, load torque. | + grasp, perception |
| **INSTRUMENTED** | Data that needs a force sensor, a known payload mass, or a simulator. | + physics, environment |

Three of these need a clear statement:

- **`residual` is not the measured torque.** It is the applied joint torque plus the gravity and
  Coriolis terms. Send `baseline_residual` with it — the same measurement from a run on the same
  path with an empty gripper. The subtraction of the two gives the load of the payload.
- **`torque_ratio` needs a known payload mass.** Calculate it with inverse dynamics. It is the one
  signal that separates "too heavy for this arm" from "the grasp was wrong".
- **`oracle.*` is ground truth.** A production robot usually cannot supply it. Send it from a
  simulator or a motion-capture system, or do not send it.

### Two mistakes that the service rejects

The service answers 422 and names the field. It does not guess.

1. **`ctrl_range` has one row for each ACTUATOR.** Every other list of lists in a record has one row
   for each STEP. This is the most frequent mistake.
2. **Rows must be complete.** `tool_xyz` needs all three of x, y and z in each row.

Different channel lengths are **not** an error. The service tells you in `warnings` and still gives
you a diagnosis. Some signals are then calculated over fewer steps than you sent.

## 5. Get the cause in terms of your code

Add your source code as `policy` and the service reads it:

```jsonc
{
  "features": { "global": {"...": "..."} },
  "policy": {"files": [{"path": "policy.py", "content": "GRASP_Z = 0.21\n..."}]},
  "coding_agent": {"mode": "shl"}
}
```

You then also get:

- **`feedback`** — a briefing for a repair agent. It holds measured corrections, and, if the service
  has seen this failure before, the fix that worked and the fixes that did not.
- **`patch`** — with `coding_agent.mode = "shl"`, a proposed change: the new source and a diff.

The service never runs your code. You apply the change, you run it, and you report what happened.

Send only the files that a fix can change, and the files it reads. Do not send your whole system,
and never send credentials.

## 6. Report what happened — always

This is the step that makes the service improve. Send the `response_id` from step 3 and what you did:

```bash
curl -s $SHL_URL/v1/lessons \
  -H "Authorization: Bearer $SHL_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"response_id": "0f3c9a1b2d4e5f60", "outcome": "SOLVED", "solved_at": 1}'
```

The service reads the diagnosis and the telemetry from its own record of that call, thus you do not
send them again.

| `outcome` | when to send it |
|---|---|
| `SOLVED` | Your fix worked. Add `after_src` to keep the working source with the lesson. |
| `ESCALATED` | The action was `hardware` or `environment`, and a person had to act. |
| `THRASH` | You tried a fix and it did not work. |

**Report `THRASH` too.** It tells the service which fix does not work, which is as useful as a fix
that does: the next agent that meets this failure is told not to try it.

You cannot read the lessons back, and you do not need to. The service applies them inside
`POST /v1/diagnose` and returns what it found in `feedback`.

## 7. Timeouts

Most calls answer in seconds. Two paths are slow, because they run a language model while you wait:
`coding_agent.mode="shl"`, and `multi_vlm_analysis` with several models.

Set your client timeout to 180 seconds or more. If you put a proxy in front of the service, raise
its timeout as well. This is the most frequent problem in a first integration.

## Try it

```bash
SHL_URL=... SHL_TOKEN=... ./try-it.sh
```

The script checks the service, sends the minimal example, shows you a 422, and reports an outcome.

## Questions

bradford@squarehammerlabs.com
