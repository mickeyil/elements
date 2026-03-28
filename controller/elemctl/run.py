"""elemctl entry point — config-driven controller with NetworkDevice wiring."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path

from .config import (
    DEFAULT_CONFIG_PATH, Config, ConfigError, load_config, resolve_config_path,
)
from .controller import Controller, ControllerState, StripConfig
from .network_device import NetworkDevice
from .slogger import configure_logger
from .udp_receiver import UdpFrameReceiver

log = logging.getLogger(__name__)


DEFAULT_STALL_TIMEOUT = 5.0  # seconds with no frames before aborting


def run_controller(
    config: Config,
    manifest,
    loop: bool = False,
    stop_event: threading.Event | None = None,
    stall_timeout: float = DEFAULT_STALL_TIMEOUT,
    receiver_factory=UdpFrameReceiver,
    device_factory=NetworkDevice,
) -> ControllerState:
    """Run the playback loop. Returns final ControllerState.

    stop_event: optional threading.Event for external shutdown (e.g. from
    a non-main thread where signal handlers can't be installed).
    stall_timeout: seconds with no frames received before aborting.
    receiver_factory / device_factory: for testing injection.
    """
    if not config.devices:
        log.error("no configured devices")
        return ControllerState.IDLE

    receiver = receiver_factory(config.frame_port)
    devices = []

    try:
        # Create devices
        for dc in config.devices:
            dev = device_factory(
                device_id=dc.device_id,
                host=dc.host,
                tcp_port=dc.tcp_port,
                device_type=dc.device_type,
                strip_length=dc.length,
                frame_port=config.frame_port,
                udp_receiver=receiver,
            )
            devices.append(dev)

        # Build strip configs
        strips = [
            StripConfig(
                strip_id=dc.strip_id,
                length=dc.length,
                device=dev,
            )
            for dc, dev in zip(config.devices, devices)
        ]

        controller = Controller(strips)

        if not controller.load(manifest, loop):
            for ev in controller.drain_events():
                log.error("load error: %s", ev.message)
            return ControllerState.IDLE

        controller.play()

        for ev in controller.drain_events():
            log.info("event: %s %s", ev.kind.name, ev.message)

        # SIGINT handler — only on main thread (signal.signal raises
        # ValueError from non-main threads)
        shutdown = False
        prev_handler = None

        if threading.current_thread() is threading.main_thread():
            def _on_sigint(signum, frame):
                nonlocal shutdown
                shutdown = True

            prev_handler = signal.signal(signal.SIGINT, _on_sigint)

        # Stall detection: track last time a ProgramFrame was emitted.
        # This measures actual end-to-end transport progress — catches
        # "device never started", "UDP broken", "device silent".
        last_frame_time = time.monotonic()

        try:
            while not shutdown:
                if stop_event is not None and stop_event.is_set():
                    break

                receiver.poll()
                controller.tick_once()

                frames = controller.drain_program_frames()
                if frames:
                    last_frame_time = time.monotonic()

                disconnected = any(
                    controller.uses_device(dev)
                    and not getattr(dev, "is_connected", True)
                    for dev in devices
                )
                if disconnected:
                    controller.abort("active device disconnected")

                for ev in controller.drain_events():
                    log.info("event: %s %s", ev.kind.name, ev.message)

                if disconnected:
                    break

                if controller.state == ControllerState.ENDED:
                    break

                if controller.program_frame_stream_enabled:
                    # If no frames arrive for stall_timeout, abort.
                    now_mono = time.monotonic()
                    if now_mono - last_frame_time > stall_timeout:
                        reason = f"no frames received for {stall_timeout:.1f}s"
                        log.error("%s, aborting", reason)
                        controller.abort(reason)
                        for ev in controller.drain_events():
                            log.info("event: %s %s", ev.kind.name, ev.message)
                        break

                time.sleep(0.020)
        finally:
            if prev_handler is not None:
                signal.signal(signal.SIGINT, prev_handler)

        return controller.state

    finally:
        for dev in devices:
            dev.close()
        receiver.close()


def main() -> None:
    """CLI entry point for python -m elemctl."""
    parser = argparse.ArgumentParser(
        prog="elemctl",
        description="Elements LED controller",
    )
    parser.add_argument(
        "--config", default=DEFAULT_CONFIG_PATH,
        help="config JSON path (default: %(default)s)",
    )
    parser.add_argument("--beat", type=float, required=True, help="beat duration (seconds)")
    parser.add_argument("--duration", type=float, required=True, help="program duration (seconds)")
    parser.add_argument("--loop", action="store_true", help="restart on end")
    parser.add_argument("program", help="DSL program (.py)")

    args = parser.parse_args()
    configure_logger(level="INFO")

    try:
        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
    except (ConfigError, json.JSONDecodeError) as e:
        log.error("config error: %s", e)
        sys.exit(1)

    try:
        from elements.compiler import CompileError
    except ImportError:
        CompileError = None

    try:
        from elements.dsl import _builder, build_manifest

        _builder.reset()
        _builder.configured_strip_lengths = {
            dc.strip_id: dc.length for dc in config.devices
        }
        try:
            source = Path(args.program).read_text()
            exec(source, {"__builtins__": __builtins__})
            manifest = build_manifest(beat=args.beat, duration=args.duration)
        finally:
            _builder.reset()
    except FileNotFoundError as e:
        log.error("program file not found: %s", e)
        sys.exit(1)
    except SyntaxError as e:
        log.error("program syntax error: %s", e)
        sys.exit(1)
    except Exception as e:
        if CompileError is not None and isinstance(e, CompileError):
            log.error("compile error: %s", e)
        else:
            log.error("program execution error: %s", e)
        sys.exit(1)

    log.info(
        "compiled: %d strip(s), duration=%.2fs, %d safe interval(s)",
        len(manifest.strips),
        manifest.duration,
        len(manifest.safe_intervals),
    )

    state = run_controller(config, manifest, loop=args.loop)
    log.info("finished: %s", state.name)

    if state != ControllerState.ENDED:
        sys.exit(1)
