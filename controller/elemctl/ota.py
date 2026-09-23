"""Firmware update over the air: the espota FLASH client, in-process.

Speaks the protocol ArduinoOTA serves on the device (the reference client
is espota.py in the arduino-esp32 framework; the device side is
ArduinoOTA.cpp). Reimplemented here so the controller needs neither
PlatformIO nor a subprocess, and so progress is observable:

  1. Listen on TCP listen_port (all interfaces; the device connects back).
  2. UDP invite to device:3232, `"0 <listen_port> <size> <md5>\\n"`,
     retried until the device answers `OK` or `AUTH <nonce>`.
  3. On AUTH, answer `"200 <cnonce> <md5(md5(pw):nonce:cnonce)>\\n"`;
     the device replies `OK` or an error text.
  4. The device connects to listen_port. Stream the image in 1024-byte
     chunks, reading the device's ASCII byte-count reply after each.
  5. The device verifies the md5, then sends `OK` (it reboots into the
     new image) or an error text, and closes.

The wire strings and the chunk/ack rhythm follow the reference exactly;
the timeouts are ours and finite everywhere, so a device that vanishes
mid-way fails the update instead of pinning the thread.

One FirmwareUpdate is one attempt, run on its own daemon thread. The
owner polls snapshot() (thread-safe, a fresh dict each call); nothing
here decides whether an update should run or which one may run next.
"""

from __future__ import annotations

import hashlib
import logging
import socket
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# ArduinoOTA's default UDP port on the device.
OTA_DEVICE_PORT = 3232

# espota command codes.
_CMD_FLASH = 0
_CMD_AUTH = 200

# The reference streams 1024-byte chunks and reads one reply per chunk.
_CHUNK_SIZE = 1024

# The reference reads the final result at most this many times.
_RESULT_READS = 5

PHASE_INVITING = 'inviting'
PHASE_SENDING = 'sending'
PHASE_DONE = 'done'
PHASE_FAILED = 'failed'
TERMINAL_PHASES = (PHASE_DONE, PHASE_FAILED)


class OtaError(Exception):
    """A step of the transfer failed; the message is shown to the operator."""


def auth_response(password, nonce, cnonce):
    """The digest ArduinoOTA expects for AUTH: md5(md5(pw):nonce:cnonce)."""
    passmd5 = hashlib.md5(password.encode()).hexdigest()
    return hashlib.md5(f'{passmd5}:{nonce}:{cnonce}'.encode()).hexdigest()


class FirmwareUpdate:
    """One espota FLASH transfer of image_path to the device at device_ip.

    uid only labels the snapshot. password is the device's OTA password,
    or None for a device built without one. Timeouts are seconds:
      invite_timeout_s   per invite attempt, invite_attempts of them
      connect_timeout_s  for the device to connect back after accepting
      chunk_timeout_s    for each chunk's send and byte-count reply
      result_timeout_s   for the final verdict after the last chunk
                         (the device checks the md5 before answering)
    """

    def __init__(self, uid, device_ip, image_path, listen_port, password=None, *,
                 device_port=OTA_DEVICE_PORT, invite_timeout_s=3.0,
                 invite_attempts=10, connect_timeout_s=10.0,
                 chunk_timeout_s=10.0, result_timeout_s=60.0):
        self._uid = uid
        self._device_ip = device_ip
        self._device_port = device_port
        self._image_path = Path(image_path)
        self._listen_port = listen_port
        self._password = password
        self._invite_timeout_s = invite_timeout_s
        self._invite_attempts = invite_attempts
        self._connect_timeout_s = connect_timeout_s
        self._chunk_timeout_s = chunk_timeout_s
        self._result_timeout_s = result_timeout_s

        self._lock = threading.Lock()
        self._state = {
            'uid': uid,
            'phase': PHASE_INVITING,
            'bytes_sent': 0,
            'total_bytes': 0,
            'error': None,
            'started_at': None,     # wall-clock seconds
            'finished_at': None,
        }
        self._thread = threading.Thread(
            target=self._run, name=f'ota-{uid}', daemon=True)

    def start(self):
        self._set(started_at=time.time())
        self._thread.start()

    def join(self, timeout=None):
        self._thread.join(timeout)

    def snapshot(self):
        with self._lock:
            return dict(self._state)

    # -- transfer (worker thread) -------------------------------------------

    def _set(self, **fields):
        with self._lock:
            self._state.update(fields)

    def _run(self):
        try:
            self._transfer()
        except OtaError as e:
            self._finish(PHASE_FAILED, str(e))
        except Exception as e:   # never let the thread die with a live phase
            log.exception('ota: %s: unexpected failure', self._uid)
            self._finish(PHASE_FAILED, f'unexpected error: {e}')
        else:
            self._finish(PHASE_DONE, None)

    def _finish(self, phase, error):
        self._set(phase=phase, error=error, finished_at=time.time())

    def _transfer(self):
        try:
            image = self._image_path.read_bytes()
        except OSError as e:
            raise OtaError(f'cannot read image {self._image_path}: {e}') from e
        md5 = hashlib.md5(image).hexdigest()
        self._set(total_bytes=len(image))

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind(('0.0.0.0', self._listen_port))
                listener.listen(1)
            except OSError as e:
                raise OtaError(f'cannot listen on TCP port {self._listen_port}: {e}') from e

            self._invite(len(image), md5)

            listener.settimeout(self._connect_timeout_s)
            try:
                conn, _ = listener.accept()
            except socket.timeout as e:
                raise OtaError('device accepted the update but never connected back') from e
        finally:
            listener.close()

        with conn:
            self._set(phase=PHASE_SENDING)
            self._stream(conn, image)

    def _invite(self, size, md5):
        """Steps 2-3: invite until the device accepts; authenticate if asked."""
        invite = f'{_CMD_FLASH} {self._listen_port} {size} {md5}\n'.encode()
        remote = (self._device_ip, self._device_port)
        # One socket for every attempt, unlike the reference's socket per
        # try, so a reply that lands just after its attempt timed out is
        # still read by the next one.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            reply = None
            for _ in range(self._invite_attempts):
                try:
                    udp.sendto(invite, remote)
                except OSError as e:
                    raise OtaError(f'cannot reach {self._device_ip}: {e}') from e
                reply = self._recv_from_device(udp, self._invite_timeout_s)
                if reply is not None:
                    break
            if reply is None:
                raise OtaError(f'no answer from {self._device_ip}:{self._device_port} '
                               f'after {self._invite_attempts} invitations')
            if reply == 'OK':
                return
            if not reply.startswith('AUTH'):
                raise OtaError(f'unexpected invitation reply: {reply!r}')

            parts = reply.split()
            if len(parts) < 2:
                raise OtaError(f'malformed AUTH request: {reply!r}')
            if self._password is None:
                raise OtaError('device requires an OTA password '
                               '(set controller.ota_password)')
            nonce = parts[1]
            # The device takes the cnonce as given; the reference derives
            # it from the file name, size, md5 and address, and so do we.
            cnonce = hashlib.md5(
                f'{self._image_path}{size}{md5}{self._device_ip}'.encode()).hexdigest()
            result = auth_response(self._password, nonce, cnonce)
            try:
                udp.sendto(f'{_CMD_AUTH} {cnonce} {result}\n'.encode(), remote)
            except OSError as e:
                raise OtaError(f'cannot reach {self._device_ip}: {e}') from e
            reply = self._recv_from_device(udp, self._chunk_timeout_s)
            if reply is None:
                raise OtaError('no answer to authentication')
            if reply != 'OK':
                raise OtaError(f'authentication refused: {reply}')

    def _recv_from_device(self, udp, timeout_s):
        """The next datagram from the device's address within timeout_s, as
        text, or None. Datagrams from anyone else are skipped."""
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            udp.settimeout(remaining)
            try:
                data, src = udp.recvfrom(64)
            except socket.timeout:
                return None
            except OSError as e:
                # e.g. ICMP port unreachable surfacing as ECONNREFUSED:
                # nothing listens there yet. Pace the retry so the attempts
                # span a Wi-Fi hiccup instead of burning out in milliseconds.
                log.debug('ota: invite recv from %s: %s', self._device_ip, e)
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))
                return None
            if src[0] == self._device_ip:
                return data.decode(errors='replace').strip()

    def _stream(self, conn, image):
        """Steps 4-5: chunks out, byte counts back, then the verdict."""
        conn.settimeout(self._chunk_timeout_s)
        reply = ''
        offset = 0
        while offset < len(image):
            chunk = image[offset:offset + _CHUNK_SIZE]
            try:
                conn.sendall(chunk)
                data = conn.recv(10)
            except OSError as e:
                raise OtaError(f'upload failed after {offset} bytes: {e}') from e
            if not data:
                raise OtaError(f'device closed the connection after {offset} bytes')
            offset += len(chunk)
            reply = data.decode(errors='replace')
            self._set(bytes_sent=offset)

        # The count for the last chunk and the verdict can share a read.
        if 'OK' in reply:
            return
        received = ''
        deadline = time.monotonic() + self._result_timeout_s
        for _ in range(_RESULT_READS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            conn.settimeout(remaining)
            try:
                data = conn.recv(32)
            except OSError as e:
                raise OtaError(f'no result from device: {e}') from e
            if not data:
                break
            received += data.decode(errors='replace')
            if 'OK' in received:
                return
        # Whatever follows the last byte count is the device's error text
        # (Update.printError), e.g. an md5 mismatch; its start may have
        # shared a read with that count.
        detail = (reply + received).lstrip('0123456789').strip()
        raise OtaError(f'device rejected the image: {detail}' if detail
                       else 'device did not confirm the update')
