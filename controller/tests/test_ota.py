"""FirmwareUpdate against a scripted ArduinoOTA device on loopback.

FakeOtaDevice plays the device side of espota the way ArduinoOTA.cpp
does: parse the UDP invite, optionally challenge with AUTH, connect back
to the advertised port, answer every read with its byte count, then
verify the md5 and send "OK" or an error text. Real sockets, short
timeouts, so a protocol slip fails fast instead of hanging the suite.
"""

import hashlib
import socket
import threading

import pytest

from elemctl import ota
from elemctl.ota import FirmwareUpdate, auth_response

from .sim_helpers import find_free_tcp_port

UID = 'esp-aabbccddeeff'


class FakeOtaDevice:
    """One OTA session on 127.0.0.1. Knobs:
      password      require AUTH with this password (None: reply OK)
      answer        False: never answer the invite
      corrupt       flip a received byte, so the md5 check fails
      close_after   drop the connection once this many bytes arrived
    """

    def __init__(self, *, password=None, answer=True, corrupt=False, close_after=None):
        self.password = password
        self.answer = answer
        self.corrupt = corrupt
        self.close_after = close_after
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp.bind(('127.0.0.1', 0))
        self.udp.settimeout(5.0)
        self.port = self.udp.getsockname()[1]
        self.invite = None
        self.received = b''
        self.error = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def close(self):
        self._thread.join(5.0)
        self.udp.close()

    def _run(self):
        try:
            self._session()
        except Exception as e:     # surfaced through .error in the test
            self.error = e

    def _session(self):
        data, client = self.udp.recvfrom(128)
        self.invite = data.decode()
        if not self.answer:
            return
        cmd, port, size, md5 = self.invite.split()
        assert cmd == '0' and self.invite.endswith('\n')

        if self.password is not None:
            nonce = hashlib.md5(b'nonce').hexdigest()
            self.udp.sendto(f'AUTH {nonce}'.encode(), client)
            self.udp.settimeout(1.0)
            try:
                data, client = self.udp.recvfrom(128)
            except socket.timeout:
                return             # the client gave up; so does the device
            auth_cmd, cnonce, result = data.decode().split()
            assert auth_cmd == '200' and len(cnonce) == 32
            if result != auth_response(self.password, nonce, cnonce):
                self.udp.sendto(b'Authentication Failed', client)
                return
        self.udp.sendto(b'OK', client)

        with socket.create_connection((client[0], int(port)), timeout=5.0) as conn:
            while len(self.received) < int(size):
                data = conn.recv(1460)
                if not data:
                    return
                self.received += data
                if self.close_after is not None and len(self.received) >= self.close_after:
                    return
                conn.sendall(str(len(data)).encode())
            image = bytearray(self.received)
            if self.corrupt:
                image[0] ^= 0xFF
            if hashlib.md5(image).hexdigest() == md5:
                conn.sendall(b'OK')
            else:
                conn.sendall(b'Error[9]: MD5 Check Failed')


@pytest.fixture
def image(tmp_path):
    path = tmp_path / 'firmware.bin'
    # Not a multiple of the 1024-byte chunk, so the short tail is exercised.
    path.write_bytes(bytes(range(256)) * 12 + b'tail')
    return path


def run_update(device, image, password=None, **timeouts):
    kwargs = dict(invite_timeout_s=1.0, invite_attempts=3, connect_timeout_s=2.0,
                  chunk_timeout_s=2.0, result_timeout_s=2.0)
    kwargs.update(timeouts)
    update = FirmwareUpdate(UID, '127.0.0.1', image, find_free_tcp_port(),
                            password, device_port=device.port, **kwargs)
    update.start()
    update.join(15.0)
    device.close()
    assert device.error is None
    return update.snapshot()


def test_flash_succeeds(image):
    device = FakeOtaDevice()
    snap = run_update(device, image)

    assert snap['phase'] == ota.PHASE_DONE, snap['error']
    assert snap['error'] is None
    assert snap['uid'] == UID
    assert snap['bytes_sent'] == snap['total_bytes'] == image.stat().st_size
    assert snap['started_at'] <= snap['finished_at']
    assert device.received == image.read_bytes()
    # The invite advertises our listen port, the size and the image md5.
    _, port, size, md5 = device.invite.split()
    assert int(size) == image.stat().st_size
    assert md5 == hashlib.md5(image.read_bytes()).hexdigest()


def test_flash_with_password(image):
    device = FakeOtaDevice(password='hunter2')
    snap = run_update(device, image, password='hunter2')
    assert snap['phase'] == ota.PHASE_DONE, snap['error']
    assert device.received == image.read_bytes()


def test_wrong_password_is_refused(image):
    device = FakeOtaDevice(password='hunter2')
    snap = run_update(device, image, password='nope')
    assert snap['phase'] == ota.PHASE_FAILED
    assert 'Authentication Failed' in snap['error']
    assert snap['bytes_sent'] == 0


def test_password_required_but_not_configured(image):
    device = FakeOtaDevice(password='hunter2')
    snap = run_update(device, image, password=None)
    assert snap['phase'] == ota.PHASE_FAILED
    assert 'ota_password' in snap['error']


def test_silent_device_fails_after_the_invite_attempts(image):
    device = FakeOtaDevice(answer=False)
    snap = run_update(device, image, invite_timeout_s=0.1, invite_attempts=2)
    assert snap['phase'] == ota.PHASE_FAILED
    assert 'no answer' in snap['error']
    assert device.invite is not None


def test_md5_rejection_reports_the_device_error(image):
    device = FakeOtaDevice(corrupt=True)
    snap = run_update(device, image)
    assert snap['phase'] == ota.PHASE_FAILED
    assert 'MD5 Check Failed' in snap['error']
    assert snap['bytes_sent'] == snap['total_bytes']


def test_device_dropping_mid_transfer_fails(image):
    device = FakeOtaDevice(close_after=1024)
    snap = run_update(device, image)
    assert snap['phase'] == ota.PHASE_FAILED
    assert snap['bytes_sent'] < snap['total_bytes']


def test_missing_image_fails_without_touching_the_network(tmp_path):
    update = FirmwareUpdate(UID, '127.0.0.1', tmp_path / 'absent.bin',
                            find_free_tcp_port(), device_port=9)
    update.start()
    update.join(5.0)
    snap = update.snapshot()
    assert snap['phase'] == ota.PHASE_FAILED
    assert 'cannot read image' in snap['error']


def test_snapshot_is_a_copy(image):
    update = FirmwareUpdate(UID, '127.0.0.1', image, find_free_tcp_port())
    snap = update.snapshot()
    snap['phase'] = 'tampered'
    assert update.snapshot()['phase'] == ota.PHASE_INVITING
