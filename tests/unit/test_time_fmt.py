"""Unit tests for time formatting utilities."""

import pytest

from video_annotator.utils.time_fmt import seconds_to_hms, hms_to_seconds, format_duration


class TestSecondsToHms:
    def test_zero(self):
        assert seconds_to_hms(0.0) == "00:00:00.000"

    def test_one_minute(self):
        assert seconds_to_hms(61.5) == "00:01:01.500"

    def test_one_hour(self):
        assert seconds_to_hms(3661.123) == "01:01:01.123"

    def test_fractional_milliseconds_truncated(self):
        # 1.9999 should NOT round up to 2.000 — truncate, never round
        assert seconds_to_hms(1.9999) == "00:00:01.999"

    def test_negative_clamped_to_zero(self):
        assert seconds_to_hms(-5.0) == "00:00:00.000"

    def test_exact_one_hour(self):
        assert seconds_to_hms(3600.0) == "01:00:00.000"

    def test_large_value(self):
        # 2 hours 30 minutes 15.250 s
        assert seconds_to_hms(9015.25) == "02:30:15.250"


class TestHmsToSeconds:
    def test_zero(self):
        assert hms_to_seconds("00:00:00.000") == pytest.approx(0.0)

    def test_roundtrip(self):
        assert hms_to_seconds(seconds_to_hms(3661.5)) == pytest.approx(3661.5, abs=0.001)

    def test_one_minute(self):
        assert hms_to_seconds("00:01:01.500") == pytest.approx(61.5)

    def test_no_milliseconds(self):
        assert hms_to_seconds("00:01:00") == pytest.approx(60.0)

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            hms_to_seconds("not-a-time")

    def test_wrong_segment_count_raises(self):
        with pytest.raises(ValueError):
            hms_to_seconds("01:00")


class TestFormatDuration:
    def test_seconds_only(self):
        assert format_duration(8.3) == "8.3s"

    def test_minutes_and_seconds(self):
        assert format_duration(75.0) == "1m 15s"

    def test_hours_and_minutes(self):
        assert format_duration(5400.0) == "1h 30m"

    def test_zero(self):
        assert format_duration(0.0) == "0.0s"
