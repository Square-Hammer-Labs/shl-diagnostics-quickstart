"""Send Isaac Sim telemetry to the SHL Diagnostics Service.

This file is a MAPPING GUIDE you can run pieces of, not a plug-in. It shows where each field of
the service's record comes from in an Isaac Sim (isaacsim.core / omni.isaac.core) scene:

    Isaac Sim source                                  ->  record field
    ------------------------------------------------     -------------------------------
    Articulation.get_joint_positions()                ->  qpos          [per step]
    Articulation.get_joint_velocities()               ->  qvel          [per step]
    Articulation.get_measured_joint_efforts()         ->  residual      [per step, see note]
    ArticulationController applied actions            ->  ctrl / target [per step]
    Articulation.get_jacobians() -> numpy SVD         ->  sigma_min / cond
    end-effector prim world pose                      ->  tool_xyz
    gripper command you send                          ->  grip_cmd
    ANY USD prim's world transform (the "prim oracle")->  oracle.object_xyz

The service reads telemetry only. It does not need Isaac Sim, USD, or this file — it needs the
JSON record. Full schema: https://docs.squarehammerlabs.com (TelemetryRecord).

Two notes that save you a day:

* **residual**: the service wants the LOAD torque above an empty-gripper baseline. Record one run
  with the object present and one on the same trajectory with an empty gripper; send the second
  as 'baseline_residual'. `get_measured_joint_efforts()` on the load-bearing joints is the right
  source for both.
* **the prim oracle**: your ground truth is a USD prim pose, not our harness. One XformCache read
  per step gives the service the outcome channels (did the part lift, where did it end).
"""
from __future__ import annotations

import json
from typing import Any

import numpy as np

CONTROL_DT = 1.0 / 60.0            # your physics step; send the real one in meta.control_dt


def new_record(task: str, robot_model: str = "ur10e") -> dict[str, Any]:
    """The record skeleton. meta.robot activates per-arm threshold scaling on the service; known
    models (ur5e, ur10e, franka_fr3, franka_panda, xarm6, xarm7) fill omitted fields."""
    return {
        "meta": {
            "task": task,
            "task_type": "pick_and_place",           # or the type your task declares
            "control_dt": CONTROL_DT,
            "robot": {"model": robot_model},         # add jnt_range etc. AS BUILT (see docs)
        },
        "t": [], "qpos": [], "qvel": [], "ctrl": [], "target": [],
        "joint_track_err": [], "residual": [], "sigma_min": [], "cond": [],
        "grip_cmd": [], "tool_xyz": [], "tool_target_err": [],
        "oracle": {"object_xyz": []},
    }


def record_step(rec: dict, *, step: int, robot, ee_prim, object_prim, xform_cache,
                applied_targets: np.ndarray, grip_cmd: float,
                commanded_tool_xyz: np.ndarray, arm_dofs: slice = slice(0, 6),
                load_dofs: tuple = (1, 2)) -> None:
    """Append one control step. `robot` is an isaacsim.core Articulation; `ee_prim` is the USD prim
    of the tool flange or fingertip frame; `object_prim` is the USD prim of the part (your oracle);
    `xform_cache` is a pxr.UsdGeom.XformCache made fresh this frame."""
    q = np.asarray(robot.get_joint_positions())[arm_dofs]
    dq = np.asarray(robot.get_joint_velocities())[arm_dofs]
    efforts = np.asarray(robot.get_measured_joint_efforts())[arm_dofs]

    rec["t"].append(round(step * CONTROL_DT, 5))
    rec["qpos"].append(q.tolist())
    rec["qvel"].append(dq.tolist())
    rec["ctrl"].append(np.asarray(applied_targets).tolist())
    rec["target"].append(np.asarray(applied_targets).tolist())
    rec["joint_track_err"].append(float(np.max(np.abs(np.asarray(applied_targets)[arm_dofs] - q))))
    rec["residual"].append(float(sum(abs(efforts[i]) for i in load_dofs)))
    rec["grip_cmd"].append(float(grip_cmd))

    # Jacobian conditioning: Isaac returns [num_envs, num_bodies, 6, num_dofs]; take the
    # end-effector body's 6 x n_arm block and SVD it.
    jac = np.asarray(robot.get_jacobians())[0, -1, :, arm_dofs]
    s = np.linalg.svd(jac, compute_uv=False)
    rec["sigma_min"].append(float(s[-1]))
    rec["cond"].append(float(s[0] / s[-1]) if s[-1] > 1e-12 else 1e12)

    # end-effector world pose (the tool frame PRIM, not the articulation root), and the
    # commanded Cartesian target
    ee = xform_cache.GetLocalToWorldTransform(ee_prim).ExtractTranslation()
    rec["tool_xyz"].append([float(ee[0]), float(ee[1]), float(ee[2])])
    rec["tool_target_err"].append(float(np.linalg.norm(np.asarray(ee) - commanded_tool_xyz)))

    # THE PRIM ORACLE: the part's ground-truth world position, straight from USD. This is what
    # grades the outcome goals (object_lifted / object_still_held / object_at).
    pos = xform_cache.GetLocalToWorldTransform(object_prim).ExtractTranslation()
    rec["oracle"]["object_xyz"].append([float(pos[0]), float(pos[1]), float(pos[2])])


def diagnose(rec: dict, baseline_rec: dict | None, url: str, token: str,
             task_yaml: str | None = None, policy_source: str | None = None) -> dict:
    """POST the record. Plain HTTP + JSON — the same call works from Rust or anything else; the
    contract is https://docs.squarehammerlabs.com/openapi.json."""
    import urllib.request

    payload: dict[str, Any] = {"record": rec}
    if baseline_rec is not None:
        rec["baseline_residual"] = baseline_rec["residual"][:len(rec["residual"])]
    if task_yaml:
        payload["task_yaml"] = task_yaml
    if policy_source:
        payload["policy"] = {"files": [{"path": "policy.py", "content": policy_source}]}
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/diagnose", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())
