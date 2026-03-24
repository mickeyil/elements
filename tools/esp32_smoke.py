#!/usr/bin/env python3
"""Minimal remote smoke/recovery probe for one ESP32 device.

Uses the controller UDS API directly:
  status -> publish_program -> load_program -> play -> stop

Optional reboot-first mode uses the ESP32 maintenance command, then waits for
the device to reconnect before running the normal smoke cycle.
"""

from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "controller"))

from elemctl.uds_client import UdsClient
from elemctl.uds_wire import KIND_JSON, parse_json_payload


PROGRAM_DURATION_S = 4.0
REBOOT_MIN_WAIT_TIMEOUT_S = 90.0


def _program_source(strip_id: str) -> str:
    return f"""\
BEAT = 1.0
DURATION = {PROGRAM_DURATION_S:.1f}

from elements.dsl import PI, sec, strip, wave

main = strip("{strip_id}")
n = main.length
pixel_step = 0.0 if n <= 1 else -(2 * PI / (n - 1))

blue_wave = wave(
    channel="V",
    h=220,
    s=1.0,
    v=0.0,
    min_val=0.05,
    max_val=0.80,
    period=4.0,
    phase0=0.0,
    pixel_step=pixel_step,
)

blue_wave.schedule(main.pixels(f"0-{{n - 1}}"), at=0, duration=sec(DURATION))
"""


def log_step(message: str) -> None:
    print(f"[smoke] {message}", flush=True)


def send_cmd(client: UdsClient, cmd: dict, timeout_s: float = 10.0) -> dict:
    cmd_id = client.next_id()
    payload = dict(cmd)
    payload["id"] = cmd_id
    client.send_cmd(payload)

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            messages = client.recv_once()
        except socket.timeout:
            continue
        for kind, raw in messages:
            if kind != KIND_JSON:
                continue
            msg = parse_json_payload(raw)
            if msg.get("type") == "reply" and msg.get("id") == cmd_id:
                return msg
    raise TimeoutError(f"timed out waiting for reply to {cmd.get('cmd')!r}")


def require_ok(reply: dict, action: str) -> dict:
    if not reply.get("ok"):
        raise RuntimeError(f"{action} failed: {reply.get('error', 'unknown error')}")
    return reply.get("result", {})


def fetch_status(client: UdsClient) -> dict:
    return require_ok(send_cmd(client, {"cmd": "status"}), "status")


def find_device(snapshot: dict, device_uid: str) -> dict | None:
    for device in snapshot.get("devices", []):
        if device.get("device_uid") == device_uid:
            return device
    return None


def wait_until(name: str, predicate, timeout_s: float, interval_s: float = 0.25):
    deadline = time.monotonic() + timeout_s
    last_value = None
    while time.monotonic() < deadline:
        last_value = predicate()
        if last_value:
            return last_value
        time.sleep(interval_s)
    raise TimeoutError(f"timed out waiting for {name}")


def wait_for_connected(client: UdsClient, device_uid: str, timeout_s: float) -> dict:
    def _probe():
        snapshot = fetch_status(client)
        device = find_device(snapshot, device_uid)
        if device is None:
            return None
        if device.get("connected"):
            return snapshot
        return None

    return wait_until(f"{device_uid} connected", _probe, timeout_s)


def wait_for_playing(client: UdsClient, timeout_s: float) -> dict:
    def _probe():
        snapshot = fetch_status(client)
        session = snapshot.get("session")
        if session and session.get("playback_state") == "playing":
            return snapshot
        return None

    return wait_until("playback_state=playing", _probe, timeout_s)


def wait_for_stopped(client: UdsClient, timeout_s: float) -> dict:
    def _probe():
        snapshot = fetch_status(client)
        session = snapshot.get("session")
        if session is None:
            return snapshot
        if session.get("playback_state") in {"stopped", "loaded", "ended"}:
            return snapshot
        return None

    return wait_until("playback_state=stopped", _probe, timeout_s)


def reboot_and_wait(client: UdsClient, device_uid: str, timeout_s: float) -> None:
    reconnect_timeout_s = max(timeout_s, REBOOT_MIN_WAIT_TIMEOUT_S)
    log_step(f"requesting reboot for {device_uid}")
    require_ok(
        send_cmd(client, {"cmd": "reboot_device", "device_uid": device_uid}),
        "reboot_device",
    )
    time.sleep(1.0)
    wait_for_connected(client, device_uid, reconnect_timeout_s)
    log_step("device reconnected after reboot")


def run_cycle(
    client: UdsClient,
    *,
    device_uid: str,
    strip_id: str,
    program_id: str,
    play_seconds: float,
    timeout_s: float,
    cycle_index: int,
) -> None:
    log_step(f"cycle {cycle_index}: waiting for device connected")
    snapshot = wait_for_connected(client, device_uid, timeout_s)
    device = find_device(snapshot, device_uid)
    if device is None:
        raise RuntimeError(f"device missing from snapshot: {device_uid}")

    log_step(f"cycle {cycle_index}: publishing {program_id}")
    require_ok(
        send_cmd(
            client,
            {
                "cmd": "publish_program",
                "program_id": program_id,
                "source": _program_source(strip_id),
            },
        ),
        "publish_program",
    )

    log_step(f"cycle {cycle_index}: loading {program_id}")
    require_ok(
        send_cmd(
            client,
            {
                "cmd": "load_program",
                "program_id": program_id,
                "targets": [device_uid],
            },
        ),
        "load_program",
    )

    log_step(f"cycle {cycle_index}: starting playback")
    require_ok(send_cmd(client, {"cmd": "play"}), "play")
    wait_for_playing(client, timeout_s)

    log_step(f"cycle {cycle_index}: playing for {play_seconds:.1f}s")
    time.sleep(play_seconds)

    log_step(f"cycle {cycle_index}: stopping playback")
    require_ok(send_cmd(client, {"cmd": "stop"}), "stop")
    wait_for_stopped(client, timeout_s)
    log_step(f"cycle {cycle_index}: ok")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="esp32_smoke.py",
        description="Minimal remote smoke probe for one ESP32 device",
    )
    parser.add_argument("--socket", default="/tmp/elemctl.sock", help="controller UDS path")
    parser.add_argument("--device", required=True, help="canonical target device uid")
    parser.add_argument("--strip", default="test50", help="target strip id in the controller config")
    parser.add_argument("--program-id", default="esp32_smoke", help="published program id")
    parser.add_argument("--play-seconds", type=float, default=1.0, help="how long to play before stop")
    parser.add_argument("--repeat", type=int, default=1, help="number of smoke cycles to run")
    parser.add_argument("--wait-timeout", type=float, default=30.0, help="wait timeout in seconds")
    parser.add_argument(
        "--reboot-first",
        action="store_true",
        help="reboot the ESP32 first, wait for reconnect, then run the smoke cycle(s)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repeat < 1:
        raise ValueError("--repeat must be >= 1")
    if args.play_seconds <= 0:
        raise ValueError("--play-seconds must be > 0")

    client = UdsClient(args.socket)
    try:
        if args.reboot_first:
            reboot_and_wait(client, args.device, args.wait_timeout)

        for index in range(1, args.repeat + 1):
            run_cycle(
                client,
                device_uid=args.device,
                strip_id=args.strip,
                program_id=args.program_id,
                play_seconds=args.play_seconds,
                timeout_s=args.wait_timeout,
                cycle_index=index,
            )
    finally:
        client.close()

    log_step("all cycles passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - script UX
        print(f"[smoke] failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
