"""Tests for the firmware update-offer rule (elemctl.firmware)."""

import pytest

from elemctl.firmware import is_dirty, parse_version, update_available


@pytest.mark.parametrize('s, expected', [
    ('0.0', (0, 0)),
    ('1.7', (1, 7)),
    ('0.10', (0, 10)),
    ('12.345', (12, 345)),
])
def test_parse_version_clean(s, expected):
    assert parse_version(s) == expected


@pytest.mark.parametrize('s', [
    None, '', 'unknown', '1.7+d', '1', '1.', '.7', '1.7.0', '1.x', '-1.7',
    ' 1.7', '1.7 ', 'abc1234', 'abc1234+d', '١.7', 17,
])
def test_parse_version_rejects_everything_else(s):
    assert parse_version(s) is None


@pytest.mark.parametrize('s, expected', [
    ('1.7+d', True),
    ('abc1234+d', True),
    ('1.7', False),
    ('abc1234', False),
    ('', False),
    (None, False),
])
def test_is_dirty(s, expected):
    assert is_dirty(s) is expected


# -- update_available ---------------------------------------------------------

@pytest.mark.parametrize('device', [None, '', '1.7', '1.7+d', 'abc1234', '9.9'])
@pytest.mark.parametrize('image', [None, 'unknown', '1.7', '1.8+d', '2.0'])
def test_no_image_never_offers(device, image):
    assert update_available(device, image, image_present=False) is False


@pytest.mark.parametrize('device', [None, '', '1.7', '1.7+d', '2.0', '9.9', 'abc1234'])
def test_dirty_image_always_offered(device):
    # Even over a newer clean device, and over the same dirty label: a dirty
    # label does not pin the content.
    assert update_available(device, '1.7+d', True) is True


@pytest.mark.parametrize('device', [
    None,           # never heard from
    '',             # predates version reporting
    'unknown',      # built where git could not tell
    'abc1234',      # hash-style
    '1.7+d',        # dirty: the same label may be different code
    '9.9+d',        # dirty and "newer": still not comparable
])
def test_uncomparable_device_offered_any_image(device):
    assert update_available(device, '1.7', True) is True


@pytest.mark.parametrize('image', [None, '', 'unknown', 'abc1234'])
def test_uncomparable_image_offered_to_clean_device(image):
    assert update_available('1.7', image, True) is True


@pytest.mark.parametrize('device, image, expected', [
    ('1.7', '1.8', True),
    ('1.7', '2.0', True),
    ('1.7', '1.7', False),     # already running it
    ('1.8', '1.7', False),     # would be a downgrade
    ('2.0', '1.9', False),
    ('0.9', '0.10', True),     # numeric, not lexical
    ('0.10', '0.9', False),
    ('9.0', '10.0', True),
    ('10.0', '9.99', False),
])
def test_clean_versions_compare_numerically(device, image, expected):
    assert update_available(device, image, True) is expected
