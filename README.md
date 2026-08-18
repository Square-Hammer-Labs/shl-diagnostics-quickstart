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
| **task type** | The kind of task the robot did. The service measures each kind differently. |
| **goal** | One condition that the run had to reach for the task to be a success. |
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

Telemetry goes in one field: `record`. Copy this exactly.

```bash
curl -s $SHL_URL/v1/diagnose \
  -H "Authorization: Bearer $SHL_TOKEN" \
  -H 'content-type: application/json' \
  -d '{
    "record": {
      "meta": {"task": "box_lift", "task_type": "pick_and_place", "control_dt": 0.02},
      "t":               [0.0, 0.02, 0.04],
      "joint_track_err": [0.01, 0.02, 0.09],
      "grip_cmd":        [0.0, 255.0, 255.0]
    },
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
    "evidence": [{"signal": "fingertip_gap_above_object_m", "reading": "0.063 m"}],
    "confidence": 0.95,
    "alternatives": []
  },
  "warnings": [],
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

Keep the `response_id`. You send it back in step 8.

### More than one cause

Sometimes the telemetry does not separate two causes. The service then puts the most likely cause in
the main fields and lists the others in `alternatives`, most likely first:

```jsonc
"confidence": 0.55,
"alternatives": [
  {"category": "grasp", "subtype": "geometry_miss", "confidence": 0.4,
   "reason": "the target may have been correct and the fingers still closed above the object; a value for fingertip_gap_above_object_m would show which"}
]
```

Read this before you change any code. If the main confidence is low and the first alternative is
near to it, make the change that is correct for **both** causes. The `reason` also tells you which
channel to add, so that the next failure of this type is not ambiguous.

The service builds `feedback` and `patch` for the main diagnosis only. An empty list means that one
cause agrees with the evidence.

## 4. Send your own telemetry

A record holds one array for each measurement, with one value at each step. **This is every field
that the service accepts.** Send the ones that your robot produces and leave out the rest.

```jsonc
{
  "record": {
    "meta": {
      "task":             "box_lift",          // [CORE] your name for this task. Any text.
      "task_type":        "pick_and_place",    // [CORE] the KIND of task. See section 5.
      "rig":              "UR5e + Robotiq 2F-85",  // [CORE] your name for the arm and the tool
      "control_dt":       0.02,                // [CORE] seconds for each step. More than zero.
      "n_arm_joints":     6,                   // [CORE] for information only
      "rated_torque":     [150, 150, 150, 28, 28, 28],  // [DERIVED] N·m for each joint
      "workspace_radius": 0.85,                // [DERIVED] metres. 'out_of_reach' needs it.
      "finger_offset":    0.115,               // [DERIVED] tool frame to fingertips, metres
      "object_half_z":    0.025,               // [INSTRUMENTED] half the object height, metres

      // [CORE] describe your robot, and the service scales its thresholds to YOUR arm.
      // Known models (ur5e, ur10e, franka_fr3, franka_panda, xarm6, xarm7) fill the fields
      // that you omit. Send the limits of the robot AS IT RUNS NOW, not the datasheet:
      // a changed joint limit is itself a diagnostic signal.
      "robot": {
        "model":           "ur5e",
        "n_arm_joints":    6,
        "rated_torque_nm": [150, 150, 150, 28, 28, 28],
        "jnt_range":       [[-6.28, 6.28], [-6.28, 6.28], [-3.14, 3.14],
                            [-6.28, 6.28], [-6.28, 6.28], [-6.28, 6.28]],
        "gripper":         {"model": "robotiq_2f85"}   // or cmd_range + close_direction
      }
    },

    // --- CORE: what every robot reports ------------------------------------------------
    "t":               [0.0, 0.02, 0.04],      // seconds. It must increase at each step.
    "qpos":            [[0,0,0,0,0,0], "..."], // measured joint positions, radians
    "qvel":            [[0,0,0,0,0,0], "..."], // joint speeds, radians a second
    "ctrl":            [[0,0,0,0,0,0], "..."], // the command you APPLIED. One row for each STEP.
    "target":          [[0,0,0,0,0,0], "..."], // the command you ASKED for, radians
    "ctrl_range":      [[-3.14, 3.14], "..."], // actuator limits. One row for each ACTUATOR.
    "joint_track_err": [0.01, 0.02, 0.28],     // radians. ONE value for each step, not each joint.
    "grip_cmd":        [0.0, 0.0, 255.0],      // gripper close command, 0 to 255. 128 or more = close.

    // --- DERIVED: what you calculate with your model of the robot ----------------------
    "qacc":            [[0,0,0,0,0,0], "..."], // joint accelerations
    "residual":        [1.2, 1.3, 41.0],       // load torque, N·m. Read the note below.
    "baseline_residual": [1.2, 1.3, 1.4],      // the same run with an EMPTY gripper
    "sigma_min":       [0.11, 0.09, 0.002],    // smallest singular value of the tool Jacobian
    "cond":            [12.0, 18.0, 340.0],    // condition number of the same Jacobian
    "dq_pinned":       [0.0, 0.0, 1.0],        // 0 or 1: was the commanded joint step at its limit
    "finger_gap":      [0.085, 0.085, 0.004],  // metres between the fingers
    "tool_xyz":        [[0.4, 0.0, 0.30], "..."],  // tool frame position, metres, from forward kinematics
    "tool_target_err": [0.31, 0.12, 0.004],    // metres from the tool to its commanded position

    // --- INSTRUMENTED: a force sensor, a known payload mass, or a simulator ------------
    "torque_ratio": 0.41,                      // ONE number, not one for each step
    "contact": {
      "ncon":           [2, 2, 4],             // number of contacts. Accepted, not used.
      "pad_normal":     [0.0, 0.0, 180.0],     // newtons at the gripper pads
      "pad_tangential": [0.0, 0.0, 20.0],      // newtons across the pads. With pad_normal: slip.
      "object_support": [30.0, 30.0, 0.0],     // newtons between the object and the surface below it
      "unexpected_pairs": [[], [], ["gripper|wall"]]  // your own list, one row for each step
    },
    "oracle": {
      "object_xyz":  [[0.4, 0.0, 0.025], "..."],  // TRUE object position, metres
      "detected_xy": [[0.4, 0.0], "..."],         // what YOUR perception reported
      "object_quat": [[1, 0, 0, 0], "..."]        // true orientation, wxyz. Accepted, not used.
    }
  }
}
```

Every field is optional, including `meta`. The service does not need a fixed set: it calculates the
signals that your data supports and reports the rest as `null`.

Three worked examples, ready to download:

| file | what it is |
|---|---|
| [`record.min.json`](https://docs.squarehammerlabs.com/examples/record.min.json) | The least that still gives a diagnosis. 12 steps, CORE tier only. |
| [`record.full.json`](https://docs.squarehammerlabs.com/examples/record.full.json) | A real 645-step failure: a payload that lifts, then slips. All tiers. |
| [`bundle.example.json`](https://docs.squarehammerlabs.com/examples/bundle.example.json) | The feature set that the service calculated from that record. Read it to see what the service gets from what you send. |

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

## 5. Say what the task was, and what success meant

### The task type

A robot arm does many things, and the service measures each kind differently. "The object left the
table" means nothing for a screwdriver that drives a screw. Thus the task says which kind it is, in
`meta.task_type`:

| `task_type` | what you get |
|---|---|
| `pick_and_place` | Everything. The phases, the grasp geometry, the lift, and the movement of the object. This is the one kind that the service measures fully today. |
| `insert`, `press`, `pour`, `wipe` | The service accepts these names but does not measure them yet. You get the signals that describe the ARM: tracking error, actuator saturation, Jacobian conditioning, load torque, and timing. The response lists the signals that are absent. |
| any other text | The same as the row above, with a warning that says the name is not known. |
| you send nothing | The service reads the run as `pick_and_place` and tells you so in `warnings`. |

The signals that describe the arm are calculated for **every** task type. A screwdriver saturates an
actuator in the same way as a gripper.

### The goals

A goal is one condition that the run had to reach. Send the goals in `task_yaml` and the service
tells you which ones the robot reached, with the value that decided each one:

```yaml
task_spec:
  inline: Lift the box off the table and put it in the bin.
task_type: pick_and_place
goals:
  - object_lifted: {min_rise_m: 0.12}
  - object_at: {xy: [0.5, 0.1], tol_m: 0.03}
```

```jsonc
"goals": {"source": "task", "achieved": "1/2", "items": [
  {"name": "object_lifted", "achieved": true,
   "reading": "the object rose 0.200 m; the goal needs 0.120 m"},
  {"name": "object_at", "achieved": false,
   "reading": "the object stopped 0.412 m from the target; the goal allows 0.030 m"}
]}
```

The four goals for `pick_and_place`:

| goal | parameters | it is reached when |
|---|---|---|
| `object_lifted` | `min_rise_m` | the object rose that far above where it started |
| `object_at` | `xy`, `tol_m` | the object stopped inside that distance of that point |
| `object_moved` | `min_m` | the object moved that far across the surface |
| `object_released` | none | the gripper opened again after it closed |

Three answers, not two. `achieved: false` means the robot did not reach the goal.
`achieved: null` means that your record does not carry the channel the goal needs, thus nobody
measured it. These must never look the same to you.

**If you send no goals, the service reads them from `task_analyzers`.** Your deterministic analyzers
already ARE your success criteria: they are what your own tests grade the run against. Thus a task
file that you have today gives you real goals, with no change. `lift_height.min_lift_z` becomes
`object_lifted`. `place_success.target_xy` and `place_tol` become `object_at`.

If there are no goals and no analyzers, the service uses one default: `object_lifted` with
`min_rise_m: 0.05`.

## 6. The schema

### What the service accepts

Section 4 shows every field. The machine-readable contract is the OpenAPI document:

```bash
curl -s https://docs.squarehammerlabs.com/openapi.json
```

Generate a client from it. Do not write the request shapes by hand.

### How the service enforces it

The service refuses a record that it cannot read, and it names the field. It does not guess.

| what you send | what you get |
|---|---|
| `ctrl_range` with one row for each STEP | **422.** It has one row for each ACTUATOR. Every other list of lists has one row for each step. This is the most frequent mistake. |
| a row that is too short (`tool_xyz` with x and y only) | **422** naming the field and the row |
| `NaN` or `Infinity` in any channel | **422** naming the channel. These are not measurements, and one of them makes every value calculated from that channel meaningless. |
| `meta.control_dt` of zero or less | **422**. The service divides by it. |
| more than 200000 steps in one channel | **422** |
| a body of more than 32 MB | **413**. Send one run for each call. |
| a record with no per-step channel | **422**. There is nothing to diagnose. |

Some conditions are recoverable. The service reports these in `warnings` and still gives you a
diagnosis:

- **Channels with different lengths.** Some signals are then calculated over fewer steps than you
  sent.
- **A `grip_cmd` that never reaches 128.** Your gripper is probably on a scale of 0 to 1. Multiply
  it by 255: without this, the service sees a gripper that never closed and every phase statistic
  becomes unavailable.
- **A `t` that does not increase, or that has a gap.** Steps are missing, thus each duration is
  measured across them.
- **A run of one step.** No trend, phase or rate can be calculated from it.

Read `warnings` on every response. An empty list means that your record is correct.

### How the schema changes

Inside `/v1`, changes are **additive only**:

- New optional fields can appear. Your integration continues to work.
- A name is never used again with a different meaning.
- A field is never removed, and an optional field never becomes necessary.
- Unknown fields that you send are accepted and ignored. They are not stored and not read, thus do
  not use them to carry your own data.
- A change that breaks any of the rules above becomes `/v2`. Your `/v1` calls continue to work.

We tell you before a new optional field appears in a response. Write your client to ignore fields
that it does not know.

## 7. Get the cause in terms of your code

Add your source code as `policy` and the service reads it:

```jsonc
{
  "record": { "...": "your telemetry" },
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

## 8. Report what happened — always

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

**Report `THRASH` too.** It tells the service which fix does NOT work. That is as useful as a fix
that does, because the next agent that meets this failure is told not to try it.

You cannot read the lessons back, and you do not need to. The service applies them inside
`POST /v1/diagnose` and returns what it found in `feedback`.

## 9. Timeouts

Most calls answer in seconds. One path is slow, because it runs a language model while you wait:
`coding_agent.mode="shl"`, which writes the patch.

Set your client timeout to 180 seconds or more. If you put a proxy in front of the service, raise
its timeout as well. This is the most frequent problem in a first integration.

## Isaac Sim

The service reads telemetry, not simulators. Isaac Sim already produces every field the record
needs. [`examples/isaac_sim_adapter.py`](examples/isaac_sim_adapter.py) shows the mapping, source
by source:

| Isaac Sim source | record field |
|---|---|
| `Articulation.get_joint_positions()` / `get_joint_velocities()` | `qpos`, `qvel` |
| `Articulation.get_measured_joint_efforts()` | `residual` (send `baseline_residual` from an empty-gripper run on the same path) |
| the joint targets you apply each step | `ctrl`, `target`, `joint_track_err` |
| `Articulation.get_jacobians()` → numpy SVD | `sigma_min`, `cond` |
| the tool frame prim's world transform | `tool_xyz` |
| **any USD prim's world transform** | `oracle.object_xyz` |

The last row is the important one. Your ground truth is a prim value: one `XformCache` read for
each step gives the service the true position of the part, and that grades the outcome goals
(`object_lifted`, `object_still_held`, `object_at`). You do not need our harness for this.

[`examples/isaac_record.json`](examples/isaac_record.json) is a record in exactly that shape — a
60-step pick that descends, closes, and lifts a part 0.18 m. It is synthetic and marked as such in
`meta.note`; send it to `/v1/diagnose` to see the full response before you wire your own scene.

## Rust and other languages

The API is plain HTTP and JSON. There is no client library to install. Call it from Rust with
`reqwest` and `serde_json` the same way the Python examples call it with `urllib`: one POST with a
bearer token. To generate typed bindings, point your generator at the machine-readable contract:
[`https://docs.squarehammerlabs.com/openapi.json`](https://docs.squarehammerlabs.com/openapi.json).

## Verify a real run without a simulator

On real hardware there is no simulator to say where the object went. What still works, and what
needs a source you supply:

**Works with no oracle at all.** Every signal in the CORE and DERIVED tiers comes from the robot
itself: tracking error, actuator saturation, Jacobian conditioning, timing, tool convergence, and —
with `residual` + `baseline_residual` — the load story: did a load appear when the gripper closed,
did it persist, did it vanish mid-move. "The gripper closed on nothing" and "the payload slipped at
step 310" are proprioceptive verdicts. They need no camera and no simulator.

**Needs a position source you choose.** The outcome goals (`object_lifted`, `object_at`,
`object_still_held`) grade the true object position. `oracle.object_xyz` is the field; who fills it
is your call: your perception stack (send what it reports, in metres, world frame), a motion-capture
system, or a fixed overhead camera with a one-time calibration. The service treats a customer
oracle and a simulator oracle identically.

**When you cannot fill it**, the goal comes back `achieved: null` with a reading that says the
quantity was never measured, not `false`. The diagnosis still runs on everything else.

**With a camera and nothing else**, use `POST /v1.1/diagnose`. It accepts the same body plus
`frames`, up to 8 camera images from the run, and `verification: {"mode": "vlm"}`. A model then
grades the goals that the telemetry could not grade, from your frames. Each goal in the response
names what decided it in `graded_by`: `telemetry` or `vlm`. A model verdict never replaces a
telemetry verdict. The two new fields are experimental and can change.

## For your coding agent

If an agent does the repair for you, give it [`skill/shl-repair-loop/`](skill/shl-repair-loop/). It
is the loop discipline around this API. Read the action before you touch any code. Use the measured
correction. Never repeat a fix that already failed. Verify with a new run. Always report the
outcome.

```bash
# Claude Code
cp -r skill/shl-repair-loop .claude/skills/
# Cursor
cp -r skill/shl-repair-loop .cursor/skills/
```

Then open [`SKILL.md`](skill/shl-repair-loop/SKILL.md) and complete the four lines in "Your system".
They tell the agent how to get telemetry from your robot and how to run the task again.

## Try it

```bash
SHL_URL=... SHL_TOKEN=... ./try-it.sh
```

The script checks the service, sends the minimal example, shows you a 422, and reports an outcome.

## Questions

bradford@squarehammerlabs.com
