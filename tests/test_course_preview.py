import json

import pytest

from scripts.preview_course import Preview, PreviewStopped


class API:
    def __init__(self):
        self.days = []

    def eligible(self):
        return True

    def send(self, day):
        self.days.append(day)
        return day + 100


def runner(tmp_path, api=None, owner="owner"):
    return Preview(api or API(), tmp_path / "state.json", owner, "catalog")


def test_first_immediate_then_at_least_sixty_seconds(tmp_path):
    p = runner(tmp_path)
    assert p.tick(100, clock=lambda: 101)
    assert not p.tick(160, clock=lambda: 160)
    assert p.tick(161, clock=lambda: 161)
    assert p.api.days == [1, 2]


def test_resume_does_not_repeat_or_reset_spacing(tmp_path):
    p = runner(tmp_path)
    p.tick(100, clock=lambda: 100)
    resumed = runner(tmp_path)
    assert not resumed.tick(159, clock=lambda: 159)
    assert resumed.tick(160, clock=lambda: 160)
    assert resumed.api.days == [2]


def test_different_owner_never_receives_messages(tmp_path):
    p = runner(tmp_path)
    p.tick(100, clock=lambda: 100)
    with pytest.raises(PreviewStopped, match="Owner"):
        runner(tmp_path, owner="another-owner")


def test_uncertain_send_is_durable_and_never_automatically_repeated(tmp_path):
    class TimeoutAPI(API):
        def send(self, day):
            raise TimeoutError()

    p = runner(tmp_path, api=TimeoutAPI())
    with pytest.raises(PreviewStopped):
        p.tick(100)
    assert json.loads(p.path.read_text())["items"]["1"]["status"] == "uncertain"
    with pytest.raises(PreviewStopped, match="Unfinished"):
        runner(tmp_path)


def test_crash_after_claim_requires_manual_review(tmp_path):
    class CrashAPI(API):
        def send(self, day):
            raise KeyboardInterrupt()

    p = runner(tmp_path, api=CrashAPI())
    with pytest.raises(KeyboardInterrupt):
        p.tick(100)
    assert json.loads(p.path.read_text())["items"]["1"]["status"] == "sending"
    with pytest.raises(PreviewStopped):
        runner(tmp_path)


def test_finite_preview_stops_at_thirty_three(tmp_path):
    p = runner(tmp_path)
    for day in range(33):
        assert p.tick(day * 60, clock=lambda day=day: day * 60)
    assert p.next_due(99999) is None
    assert not p.tick(99999)
    assert p.api.days == list(range(1, 34))


def test_leave_pauses_without_claim_and_return_resumes_same_index(tmp_path):
    class MembershipAPI(API):
        allowed = True

        def eligible(self):
            return self.allowed

    api = MembershipAPI()
    p = runner(tmp_path, api=api)
    p.tick(100, clock=lambda: 100)
    api.allowed = False
    assert not p.tick(160, clock=lambda: 160)
    assert not p.tick(300, clock=lambda: 300)
    assert list(p.state["items"]) == ["1"]
    assert api.days == [1]
    api.allowed = True
    assert p.tick(301, clock=lambda: 301)
    assert api.days == [1, 2]
    assert not p.tick(360, clock=lambda: 360)


def test_main_waits_when_due_but_membership_paused(tmp_path, monkeypatch, capsys):
    import scripts.preview_course as module

    class Identity:
        owner_fingerprint = "owner"
        catalog_hash = "catalog"

    class LoopPreview:
        def __init__(self, *args):
            self.calls = 0
            self.state = {"items": {}}

        def next_due(self, now):
            return 0 if self.calls < 2 else None

        def tick(self, now):
            self.calls += 1
            if self.calls == 1:
                return False
            self.state["items"]["1"] = {"status": "sent"}
            return True

    sleeps = []
    monkeypatch.setattr(module, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(module, "OwnerAPI", Identity)
    monkeypatch.setattr(module, "Preview", LoopPreview)
    monkeypatch.setattr(module.time, "time", lambda: 100)
    monkeypatch.setattr(module.time, "sleep", sleeps.append)
    module.main()
    assert sleeps == [60]
    assert capsys.readouterr().out.count('"sent"') == 1

