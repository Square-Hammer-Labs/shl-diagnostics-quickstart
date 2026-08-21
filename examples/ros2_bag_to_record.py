"""Convert a ROS 2 bag into the record that the SHL Diagnostics Service reads.

There is no bridge to install. The service reads JSON over HTTP, and ROS 2 already records
everything it needs. The flow is two commands:

    ros2 bag record /joint_states <your command, gripper, pose, camera topics>   # on the robot
    python ros2_bag_to_record.py <bag_dir> --task my_task -o record.json         # anywhere

This script needs no ROS 2 installation. It reads bag files (both .mcap and .db3 storage) with
the pure-Python 'rosbags' library:

    pip install rosbags

The mapping, topic by topic:

    ROS 2 source                                       ->  record field
    -------------------------------------------------     -------------------------------
    /joint_states       sensor_msgs/JointState             t, qpos, qvel        [per step]
    same message's      effort[]                           residual             [per step]
    command topic       JointTrajectory | Float64MultiArray target, ctrl, joint_track_err
    gripper topic       Float64 | GripperCommand           grip_cmd
    tool pose topic     PoseStamped                        tool_xyz
    perception topic    PoseStamped                        oracle.object_xyz
    camera topic        CompressedImage                    frames for POST /v1/diagnose

The timeline is /joint_states. Every other topic is resampled onto it by last known value. A
topic you do not have is simply left out; the service degrades one channel at a time and tells
you which signals that cost you.

Two notes that save you a day:

* **residual needs a baseline.** Record a second bag of the same trajectory with an EMPTY
  gripper and convert it with --baseline-out. Send it as 'baseline_residual'. The subtraction is
  what turns measured effort into payload load.
* **sigma_min is absent on purpose.** The Jacobian needs your kinematic model, which a bag does
  not carry. See the SVD note in isaac_sim_adapter.py if you want to add it; the diagnosis works
  without it.

Full schema: https://docs.squarehammerlabs.com (TelemetryRecord).
"""
from __future__ import annotations

import argparse
import base64
import json
import statistics
import sys
from pathlib import Path


def _stamp(msg) -> float:
    """header.stamp as float seconds."""
    s = msg.header.stamp
    return float(s.sec) + float(s.nanosec) * 1e-9


def _ensure_type(ts, conn) -> bool:
    """Register a message type the typestore does not know from the bag's own definition.

    A rosbag2 recording carries the definition of every type it holds (custom gripper actions
    included), so an unknown type is recoverable rather than fatal."""
    if conn.msgtype in ts.types:
        return True
    msgdef = getattr(conn, "msgdef", None)
    if not msgdef:
        return False
    try:
        from rosbags.typesys import get_types_from_msg
        ts.register(get_types_from_msg(msgdef.data if hasattr(msgdef, "data") else str(msgdef),
                                       conn.msgtype))
        return conn.msgtype in ts.types
    except Exception:                                # a broken definition: skip the topic
        return False


def read_bag(bag_dir: str, topics: set[str]) -> tuple[dict[str, list], list[str]]:
    """Read the requested topics. Returns ({topic: [(t_bag_s, msg), ...]}, [all topics in bag])."""
    from rosbags.rosbag2 import Reader
    from rosbags.typesys import Stores, get_typestore

    ts = get_typestore(Stores.LATEST)
    out: dict[str, list] = {t: [] for t in topics}
    seen: list[str] = []
    with Reader(bag_dir) as reader:
        conns = [c for c in reader.connections if c.topic in topics and _ensure_type(ts, c)]
        seen = sorted({c.topic for c in reader.connections})
        for conn, timestamp, raw in reader.messages(connections=conns):
            msg = ts.deserialize_cdr(raw, conn.msgtype)
            # prefer the sender's header stamp; fall back to the bag receive time
            t = _stamp(msg) if hasattr(msg, "header") else timestamp * 1e-9
            out[conn.topic].append((t, msg))
    for series in out.values():
        series.sort(key=lambda p: p[0])
    return out, seen


def _hold(series: list, t: float, idx: list[int]):
    """Last message at or before t (zero-order hold; before the first message, the first value).
    idx is a one-element cursor."""
    if not series:
        return None
    i = idx[0]
    while i + 1 < len(series) and series[i + 1][0] <= t:
        i += 1
    idx[0] = i
    return series[i][1]


def _command_positions(msg) -> list[float] | None:
    """The commanded joint positions from either command message shape."""
    if hasattr(msg, "points") and len(msg.points):                 # JointTrajectory
        return [float(v) for v in msg.points[-1].positions]
    if hasattr(msg, "data"):                                       # Float64MultiArray
        return [float(v) for v in msg.data]
    return None


def _grip_value(msg) -> float | None:
    """The gripper command from Float64 (.data) or GripperCommand-like (.position) messages."""
    for attr in ("data", "position"):
        if hasattr(msg, attr):
            try:
                return float(getattr(msg, attr))
            except (TypeError, ValueError):
                return None
    return None


def assemble(bag: dict[str, list], args) -> tuple[dict, list[dict]]:
    """Build (record, frames) from the per-topic series, on the /joint_states timeline."""
    js = bag.get(args.joint_states) or []
    if not js:
        raise SystemExit(f"error: no messages on {args.joint_states!r}.")

    # column selection: --arm-joints names win; otherwise the first N names on the topic
    names = list(js[0][1].name)
    if args.arm_joints:
        wanted = [n.strip() for n in args.arm_joints.split(",")]
        missing = [n for n in wanted if n not in names]
        if missing:
            raise SystemExit(f"error: joints {missing} not on {args.joint_states} "
                             f"(it has {names})")
        cols = [names.index(n) for n in wanted]
    else:
        cols = list(range(min(len(names), 6)))
    load = [c for c in (1, 2) if c < len(cols)]      # shoulder/elbow carry the payload
    top = max(cols)
    # a JointState may carry positions only; absent measurements stay absent, never zeros
    has_vel = len(js[0][1].velocity) > top
    has_eff = len(js[0][1].effort) > top

    t0 = js[0][0]
    rec: dict = {
        "meta": {"task": args.task, "task_type": args.task_type,
                 "robot": {"model": args.robot_model} if args.robot_model else None,
                 "rig": args.rig},
        "t": [], "qpos": [],
    }
    rec["meta"] = {k: v for k, v in rec["meta"].items() if v is not None}
    cmd = bag.get(args.command_topic) or []
    grip = bag.get(args.grip_topic) or []
    tool = bag.get(args.tool_pose_topic) or []
    obj = bag.get(args.object_pose_topic) or []
    cur = {k: [0] for k in ("cmd", "grip", "tool", "obj")}

    for t, m in js:
        if len(m.position) <= top:
            continue
        rt = round(t - t0, 6)
        q = [float(m.position[c]) for c in cols]
        rec["t"].append(rt)
        rec["qpos"].append(q)
        if has_vel and len(m.velocity) > top:
            rec.setdefault("qvel", []).append([float(m.velocity[c]) for c in cols])
        if has_eff and len(m.effort) > top:
            rec.setdefault("residual", []).append(
                sum(abs(float(m.effort[c])) for i, c in enumerate(cols) if i in load))

        if (c := _hold(cmd, t, cur["cmd"])) is not None:
            if (pos := _command_positions(c)) is not None and len(pos) >= len(cols):
                tgt = pos[:len(cols)]
                rec.setdefault("target", []).append(tgt)
                rec.setdefault("ctrl", []).append(tgt)
                rec.setdefault("joint_track_err", []).append(
                    max(abs(a - b) for a, b in zip(tgt, q)))
        if (g := _hold(grip, t, cur["grip"])) is not None:
            if (gv := _grip_value(g)) is not None:
                rec.setdefault("grip_cmd", []).append(gv)
        if (p := _hold(tool, t, cur["tool"])) is not None:
            pp = p.pose.position
            rec.setdefault("tool_xyz", []).append([float(pp.x), float(pp.y), float(pp.z)])
        if (o := _hold(obj, t, cur["obj"])) is not None:
            op = o.pose.position
            rec.setdefault("oracle", {"object_xyz": []})["object_xyz"].append(
                [float(op.x), float(op.y), float(op.z)])

    if len(rec["t"]) > 1:
        rec["meta"]["control_dt"] = round(statistics.median(
            b - a for a, b in zip(rec["t"], rec["t"][1:])), 6)

    # camera: N frames spread across the run, ready for POST /v1/diagnose
    frames = []
    cam = bag.get(args.camera_topic) or []
    if cam and args.frames > 0:
        step = max(1, len(cam) // args.frames)
        for t, m in cam[::step][:args.frames]:
            fmt = (getattr(m, "format", "") or "").lower()
            frames.append({
                "t_s": round(t - t0, 3),
                "image_b64": base64.b64encode(bytes(m.data)).decode(),
                "mime_type": "image/png" if "png" in fmt else "image/jpeg",
            })
    return rec, frames


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(
        description="Convert a ROS 2 bag into an SHL diagnostics record. No ROS install needed.")
    ap.add_argument("bag", help="the bag directory (the one holding metadata.yaml)")
    ap.add_argument("--task", required=True, help="your name for the task")
    ap.add_argument("--task-type", default="pick_and_place")
    ap.add_argument("--robot-model", default=None, help="e.g. ur10e; activates threshold scaling")
    ap.add_argument("--rig", default=None, help="your name for the arm and the tool")
    ap.add_argument("--joint-states", default="/joint_states")
    ap.add_argument("--command-topic", default="/joint_trajectory_controller/joint_trajectory")
    ap.add_argument("--grip-topic", default=None)
    ap.add_argument("--tool-pose-topic", default=None)
    ap.add_argument("--object-pose-topic", default=None)
    ap.add_argument("--camera-topic", default=None)
    ap.add_argument("--frames", type=int, default=5, help="camera frames to extract (max 8 per call)")
    ap.add_argument("--arm-joints", default=None,
                    help="comma-separated joint names, in order; default: first 6 on the topic")
    ap.add_argument("-o", "--out", default="record.json")
    ap.add_argument("--frames-out", default="frames.json")
    args = ap.parse_args(argv)

    topics = {t for t in (args.joint_states, args.command_topic, args.grip_topic,
                          args.tool_pose_topic, args.object_pose_topic, args.camera_topic) if t}
    bag, seen = read_bag(args.bag, topics)
    if not bag.get(args.joint_states):
        raise SystemExit(f"error: {args.joint_states!r} is not in this bag. "
                         f"The bag has: {seen}")

    rec, frames = assemble(bag, args)
    Path(args.out).write_text(json.dumps({"record": rec}))
    print(f"wrote {args.out}: {len(rec['t'])} steps, channels: "
          f"{sorted(k for k in rec if k not in ('meta',))}")
    if frames:
        Path(args.frames_out).write_text(json.dumps(frames))
        print(f"wrote {args.frames_out}: {len(frames)} frames "
              f"(add as 'frames' to POST /v1/diagnose)")


if __name__ == "__main__":
    sys.exit(main())
