"""Unit tests for Project, Video, Segment dataclasses."""

import pytest

from video_annotator.models.project import Project, Segment, Video


class TestSegment:
    def test_duration_property(self):
        seg = Segment(id="x", video_id="v", start=10.0, end=34.5, tags=[])
        assert seg.duration == pytest.approx(24.5)

    def test_create_generates_uuid(self):
        s1 = Segment.create("v1", 0.0, 5.0)
        s2 = Segment.create("v1", 0.0, 5.0)
        assert s1.id != s2.id

    def test_roundtrip_serialization(self):
        seg = Segment.create("vid1", 10.5, 20.25)
        seg.tags = ["action", "outdoor"]
        seg.notes = "some note"
        assert Segment.from_dict(seg.to_dict()) == seg

    def test_start_equals_end_duration_is_zero(self):
        seg = Segment(id="x", video_id="v", start=5.0, end=5.0, tags=[])
        assert seg.duration == 0.0

    def test_to_dict_contains_expected_keys(self):
        seg = Segment.create("v", 1.0, 2.0)
        d = seg.to_dict()
        for key in ("id", "video_id", "start", "end", "tags", "notes", "exported", "output_path"):
            assert key in d

    def test_duration_convenience_field_not_read_back(self):
        seg = Segment.create("v", 0.0, 5.0)
        d = seg.to_dict()
        d["duration"] = 99.0   # mutate: should be ignored on load
        loaded = Segment.from_dict(d)
        assert loaded.duration == pytest.approx(5.0)


class TestVideo:
    def test_create_generates_uuid(self):
        v1 = Video.create("a.mp4", "a.mp4", "/folder")
        v2 = Video.create("a.mp4", "a.mp4", "/folder")
        assert v1.id != v2.id

    def test_abs_path_computed(self):
        v = Video.create("clip.mp4", "clip.mp4", "/data/videos")
        assert v.abs_path == "/data/videos/clip.mp4"

    def test_abs_path_not_serialized(self):
        v = Video.create("clip.mp4", "clip.mp4", "/data/videos")
        d = v.to_dict()
        assert "abs_path" not in d
        assert "_source_folder" not in d

    def test_default_status_is_unseen(self):
        v = Video.create("clip.mp4", "clip.mp4", "/data")
        assert v.status == "unseen"

    def test_roundtrip_preserves_source_folder(self):
        v = Video.create("clip.mp4", "sub/clip.mp4", "/data")
        v.tags = ["test"]
        seg = Segment.create(v.id, 1.0, 2.0)
        v.segments.append(seg)
        loaded = Video.from_dict(v.to_dict(), "/data")
        assert loaded.abs_path == "/data/sub/clip.mp4"
        assert loaded.tags == ["test"]
        assert len(loaded.segments) == 1

    def test_unicode_filename(self):
        v = Video.create("видео.mp4", "видео.mp4", "/data")
        loaded = Video.from_dict(v.to_dict(), "/data")
        assert loaded.filename == "видео.mp4"


class TestProject:
    def test_get_video_returns_none_for_unknown_id(self):
        p = Project(source_folder="/x", output_folder="/y", created_at="2026-01-01T00:00:00Z")
        assert p.get_video("nonexistent") is None

    def test_get_video_returns_correct_video(self):
        p = Project(source_folder="/x", output_folder="/y", created_at="2026-01-01T00:00:00Z")
        v = Video.create("a.mp4", "a.mp4", "/x")
        p.videos.append(v)
        assert p.get_video(v.id) is v

    def test_all_segments_flattens(self):
        p = Project(source_folder="/x", output_folder="/y", created_at="2026-01-01T00:00:00Z")
        v1 = Video.create("a.mp4", "a.mp4", "/x")
        v2 = Video.create("b.mp4", "b.mp4", "/x")
        s1 = Segment.create(v1.id, 0.0, 1.0)
        s2 = Segment.create(v2.id, 0.0, 2.0)
        v1.segments.append(s1)
        v2.segments.append(s2)
        p.videos = [v1, v2]
        assert set(s.id for s in p.all_segments()) == {s1.id, s2.id}

    def test_roundtrip_serialization(self):
        p = Project(source_folder="/x", output_folder="/y", created_at="2026-01-01T00:00:00Z")
        v = Video.create("a.mp4", "a.mp4", "/x")
        v.tags = ["tag1"]
        seg = Segment.create(v.id, 1.5, 3.0)
        seg.tags = ["action"]
        v.segments.append(seg)
        p.videos.append(v)
        p.tag_vocabulary = ["action", "tag1"]

        loaded = Project.from_dict(p.to_dict())
        assert loaded.source_folder == p.source_folder
        assert loaded.output_folder == p.output_folder
        assert len(loaded.videos) == 1
        lv = loaded.videos[0]
        assert lv.tags == ["tag1"]
        assert len(lv.segments) == 1
        assert lv.segments[0].tags == ["action"]
        assert loaded.tag_vocabulary == ["action", "tag1"]
