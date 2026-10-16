"""Publication tests exercise restart safety and real Markdown transformations."""

import hashlib
import json
from copy import deepcopy

import pytest

from scripts.publish_course import PublicationError, Publisher


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class FakeAPI:
    def __init__(self):
        self.posts = {}
        self.mutations = []
        self.lose_create_response = False
        self.fail_slug = None

    def owned_collections(self):
        return [{"alias": "creator-hero", "url": "https://lessons.example/"}]

    def find_post(self, collection, slug):
        return deepcopy(self.posts.get(slug))

    def create_post(self, collection, payload):
        if payload["slug"] == self.fail_slug:
            raise PublicationError("temporary failure")
        assert payload["slug"] not in self.posts, "duplicate create"
        remote = {**payload, "id": f"remote{len(self.posts):04}",
                  "url": f"https://lessons.example/{payload['slug']}"}
        self.posts[payload["slug"]] = remote
        self.mutations.append(("create", payload["slug"]))
        if self.lose_create_response:
            self.lose_create_response = False
            raise PublicationError("response lost")
        return deepcopy(remote)

    def update_post(self, post_id, payload):
        slug = next(k for k, p in self.posts.items() if p["id"] == post_id)
        self.posts[slug].update(payload)
        self.mutations.append(("update", slug))
        return deepcopy(self.posts[slug])


@pytest.fixture
def course(tmp_path):
    content = tmp_path / "docs" / "lesson-content"
    content.mkdir(parents=True)
    texts = {
        "supplements/b.md": "# 📊 Statistics\n\n| A | B |\n|---|---|\n|1|2|\n",
        "supplements/a.md": "# 📘 Guide\n\n[Statistics](b.md)\n",
        "lessons/01.md": "# 💭 Lesson one\n\n**One idea.**\n\n[Guide](../supplements/a.md)\n",
        "lessons/02.md": "# 🎧 Lesson two\n\n[Audio](https://drive.google.com/file/d/user-audio/view)\n",
    }
    records = []
    for key, file, slug, deps in [
        ("lesson:l1", "lessons/01.md", "lesson-01", ["supplement:a"]),
        ("supplement:a", "supplements/a.md", "s01-guide", ["supplement:b"]),
        ("supplement:b", "supplements/b.md", "s02-statistics", []),
        ("lesson:l2", "lessons/02.md", "lesson-02", []),
    ]:
        path = content / file
        path.parent.mkdir(exist_ok=True)
        path.write_text(texts[file])
        records.append({"key": key, "file": file, "title": "metadata title",
                        "desired_slug": slug, "type": key.split(":")[0],
                        "dependencies": deps, "sha256": digest(texts[file]), "url": None})
    (content / "publication.json").write_text(json.dumps({"article_count": 4, "articles": records}))
    (content / "lessons.json").write_text(json.dumps({"items": [
        {"day": 1, "id": "l1", "sha256": records[0]["sha256"]}, {"day": 2, "id": "l2"}]}))
    (content / "chain.json").write_text(json.dumps({"items": [{"id": "l1"}, {"id": "l2"}]}))
    (content / "supplements.json").write_text(json.dumps({"items": [{"id": "a"}, {"id": "b"}]}))
    (content / "bot-messages.json").write_text(json.dumps({"items": [
        {"day": day, "lesson_id": key, "text": f"Creator Hero — {day}",
         "button_text": "Открыть урок", "article_key": f"lesson:{key}", "article_url": None}
        for day, key in [(1, "l1"), (2, "l2")]]}))
    return tmp_path, content


def publisher(course, api):
    root, content = course
    return Publisher(root, api, "creator-hero", "https://lessons.example", content / "publication-state.json")


def test_topological_publish_preserves_formatting_resolves_links_and_removes_h1(course):
    api = FakeAPI()
    result = publisher(course, api).publish()
    assert result == {"created": 4, "updated": 0, "unchanged": 0}
    assert [slug for _, slug in api.mutations] == [
        "s02-statistics", "s01-guide", "lesson-01", "lesson-02", "course"]
    lesson = api.posts["lesson-01"]
    assert lesson["title"] == "💭 Lesson one"
    assert lesson["body"].startswith("**One idea.**")
    assert "https://lessons.example/s01-guide" in lesson["body"]
    assert "https://lessons.example/s02-statistics" in api.posts["s01-guide"]["body"]
    assert "| A | B |" in api.posts["s02-statistics"]["body"]
    assert "user-audio" in api.posts["lesson-02"]["body"]


def test_second_run_skips_unchanged_posts_and_writes_matching_catalogs(course):
    api = FakeAPI()
    pub = publisher(course, api)
    pub.publish()
    original_count = len(api.mutations)
    assert publisher(course, api).publish() == {"created": 0, "updated": 0, "unchanged": 4}
    assert len(api.mutations) == original_count
    root, content = course
    runtime = json.loads((root / "bot/data/lesson-catalog.json").read_text())
    assert runtime["brand"] == "Creator Hero" and runtime["count"] == 2
    assert runtime["items"][0]["article_url"] == "https://lessons.example/lesson-01"
    chain = json.loads((content / "chain.json").read_text())
    assert chain["items"][0]["url"] == runtime["items"][0]["article_url"]
    state = json.loads((content / "publication-state.json").read_text())
    record = json.loads((content / "publication.json").read_text())["articles"][0]
    assert record["sha256"] == state["items"]["lesson:l1"]["source_sha256"]
    assert record["published_sha256"] != record["sha256"]


def test_lost_create_response_is_recovered_without_duplicate(course):
    api = FakeAPI()
    api.lose_create_response = True
    publisher(course, api).publish()
    assert len(api.posts) == 5
    assert len(api.mutations) == 5


def test_partial_failure_persists_progress_and_restart_resumes(course):
    api = FakeAPI()
    api.fail_slug = "lesson-01"
    with pytest.raises(PublicationError):
        publisher(course, api).publish()
    content = course[1]
    state = json.loads((content / "publication-state.json").read_text())
    assert set(state["items"]) == {"supplement:a", "supplement:b"}
    assert not (course[0] / "bot/data/lesson-catalog.json").exists()
    api.fail_slug = None
    result = publisher(course, api).publish()
    assert result == {"created": 2, "updated": 0, "unchanged": 2}
    assert len(api.posts) == 5


def test_changed_source_updates_existing_post_and_preserves_id(course):
    api = FakeAPI()
    publisher(course, api).publish()
    old_id = api.posts["lesson-02"]["id"]
    content = course[1]
    path = content / "lessons/02.md"
    path.write_text(path.read_text() + "\nAnother thought.\n")
    manifest = json.loads((content / "publication.json").read_text())
    manifest["articles"][3]["sha256"] = digest(path.read_text())
    (content / "publication.json").write_text(json.dumps(manifest))
    assert publisher(course, api).publish()["updated"] == 1
    assert api.posts["lesson-02"]["id"] == old_id
    assert "Another thought." in api.posts["lesson-02"]["body"]


def test_unexpected_remote_content_is_never_overwritten(course):
    api = FakeAPI()
    api.posts["s02-statistics"] = {"id": "unrelated", "slug": "s02-statistics",
                                     "title": "Personal article", "body": "Keep this"}
    with pytest.raises(PublicationError, match="conflict"):
        publisher(course, api).publish()
    assert not api.mutations


def test_changed_remote_content_is_never_overwritten(course):
    api = FakeAPI()
    publisher(course, api).publish()
    api.posts["s01-guide"]["body"] = "Edited in browser"
    before = len(api.mutations)
    with pytest.raises(PublicationError, match="conflict"):
        publisher(course, api).publish()
    assert len(api.mutations) == before


def test_invalid_source_hash_fails_before_any_remote_mutation(course):
    api = FakeAPI()
    (course[1] / "lessons/02.md").write_text("# Changed without updating manifest")
    with pytest.raises(PublicationError, match="hash"):
        publisher(course, api).publish()
    assert not api.mutations


def test_navigation_lists_course_in_logical_order_and_supplements_separately(course):
    api = FakeAPI()
    publisher(course, api).publish()
    navigation = api.posts["course"]
    assert navigation["body"].index("/lesson-01") < navigation["body"].index("/lesson-02")
    assert "/s01-guide" in navigation["body"]
    assert "/s02-statistics" in navigation["body"]
    assert (course[1] / "course-index.md").exists()


def test_owner_credentials_json_can_be_used_without_exposing_password(tmp_path):
    from scripts.publish_course import WriteFreelyClient

    token_file = tmp_path / "credentials.json"
    token_file.write_text(json.dumps({"access_token": "private-token", "password": "private-password"}))
    client = WriteFreelyClient("http://127.0.0.1:8088", token_file)
    assert client.token == "private-token"
