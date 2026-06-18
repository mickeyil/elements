"""Deprecated v2 controller code, kept for reference only.

These modules implement the v2 device protocol (controller dials out,
numeric device_id, gen counter, controller-driven clock sync). v3
inverts that: devices connect in, UID is the only identity, and the
clock controller is a stateless responder. The live code lives in
elemctl proper (wire.py, hub.py, session.py, config.py); see
drafts/controller_v3.md.

Nothing in the active package imports this subpackage. It is not on the
test path and is not built. Read it to recover v2 behavior or intent;
do not extend it.
"""
