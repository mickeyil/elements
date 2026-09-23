"""Periodic QueryDeviceStatus polling: the latest status report per device.

The device's status reply carries what only it knows, notably its clock
sync state (synced, and the skew measured at its last sync round). This
poller asks each attached device on a fixed interval and keeps the latest
report so the service can publish it. It sits beside the session, not in
it: the query is read-only and never touches the reconciler's
one-command-in-flight ladder; the hub matches each ACK to its callback in
order, so the two interleave safely on one link.
"""

import logging

from . import wire

log = logging.getLogger(__name__)

# The device's clock sync measures skew once per 15 s steady round
# (src/controller/clock_sync_client.cpp), so a 5 s poll shows each new
# value within a few seconds of it landing.
STATUS_QUERY_INTERVAL_US = 5 * 1_000_000


class _Poll:
    """Per-device polling state; replaced wholesale when the device's link
    changes, which orphans any query still in flight on the old one."""

    def __init__(self):
        self.report = None       # latest wire.DeviceStatusReport, or None
        self.inflight = False
        self.next_query_us = 0   # 0: query on the next tick


class DeviceStatusPoller:
    """Construct with the hub and the monotonic microsecond clock; call
    tick(attached_uids) once per loop and forget(uid) whenever a device's
    link comes up or goes down."""

    def __init__(self, hub, clock_us, interval_us=STATUS_QUERY_INTERVAL_US):
        self._hub = hub
        self._clock_us = clock_us
        self._interval_us = interval_us
        self._polls = {}   # uid -> _Poll, attached devices only

    def report(self, uid):
        """The latest status report from uid, or None if it has not answered
        since its link came up."""
        poll = self._polls.get(uid)
        return poll.report if poll is not None else None

    def forget(self, uid):
        """Drop uid's state: its link changed, so the old report and any
        query in flight belong to a connection that is gone."""
        self._polls.pop(uid, None)

    def tick(self, attached_uids):
        for uid in set(self._polls) - attached_uids:
            del self._polls[uid]
        now_us = self._clock_us()
        for uid in attached_uids:
            poll = self._polls.setdefault(uid, _Poll())
            if poll.inflight or now_us < poll.next_query_us:
                continue
            poll.inflight = True
            poll.next_query_us = now_us + self._interval_us
            sent = self._hub.send(uid, wire.encode_query_device_status(),
                                  lambda ack, uid=uid, poll=poll:
                                      self._on_ack(uid, poll, ack))
            if not sent:
                poll.inflight = False

    def _on_ack(self, uid, poll, ack):
        if self._polls.get(uid) is not poll:
            return   # the link changed since this query went out
        poll.inflight = False
        if ack.status != wire.ACK_OK:
            log.warning('%s: status query refused (%s)', uid,
                        wire.ACK_STATUS_NAMES.get(ack.status, ack.status))
            return
        try:
            poll.report = wire.parse_device_status(ack.payload)
        except wire.WireError as e:
            log.warning('%s: bad status reply: %s', uid, e)
