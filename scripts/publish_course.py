#!/usr/bin/env python3
"""Publish the reviewed course to WriteFreely, safely resuming interrupted uploads.

Run on the VPS with an owner token file outside the checkout. Credentials are
never persisted in the publication state or copied into the bot catalog.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class PublicationError(Exception):
    """A publication could not be completed safely."""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def payload_hash(payload: dict) -> str:
    return sha256(json.dumps({key: payload[key] for key in ("title", "body")},
                             ensure_ascii=False, sort_keys=True))


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as output:
            name = output.name
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WriteFreelyClient:
    def __init__(self, api_url: str, token_file: Path):
        parts = urlsplit(api_url)
        if parts.scheme != "https" and not (
            parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost", "::1"}
        ):
            raise PublicationError("API URL must use HTTPS or a local loopback address")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise PublicationError("Invalid API URL")
        self.base_url = api_url.rstrip("/")
        credential_text = token_file.read_text(encoding="utf-8").strip()
        self.token = (json.loads(credential_text)["access_token"]
                      if credential_text.startswith("{") else credential_text)
        if not self.token or "\n" in self.token or "\r" in self.token:
            raise PublicationError("Invalid owner token file")
        self.opener = build_opener(NoRedirect)

    def request(self, method: str, path: str, payload: dict | None = None):
        headers = {"Authorization": "Token " + self.token, "Accept": "application/json"}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=25) as response:
                result = json.load(response)
        except HTTPError as exc:
            if method == "GET" and exc.code == 404:
                return None
            raise PublicationError(f"WriteFreely {method} {path}: HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise PublicationError(f"WriteFreely {method} {path}: network or response error") from None
        if (not isinstance(result, dict) or "data" not in result
                or not 200 <= int(result.get("code", 0)) < 300):
            raise PublicationError(f"WriteFreely {method} {path}: invalid API response")
        return result["data"]

    def owned_collections(self):
        return self.request("GET", "/api/me/collections")

    def find_post(self, collection: str, slug: str):
        path = f"/api/collections/{quote(collection, safe='')}/posts/{quote(slug, safe='')}"
        return self.request("GET", path)

    def create_post(self, collection: str, payload: dict):
        return self.request("POST", f"/api/collections/{quote(collection, safe='')}/posts", payload)

    def update_post(self, post_id: str, payload: dict):
        return self.request("POST", f"/api/posts/{quote(post_id, safe='')}", payload)


LINK = re.compile(r"(\]\()([^\s)]+)(\))")


class Publisher:
    def __init__(self, root: Path, api, collection: str, base_url: str, state_path: Path):
        self.root = root.resolve()
        self.content = self.root / "docs/lesson-content"
        self.api = api
        self.collection = collection
        parts = urlsplit(base_url)
        if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
            raise PublicationError("Public base URL must be HTTPS")
        self.base_url = base_url.rstrip("/")
        self.state_path = state_path
        self.manifest = read_json(self.content / "publication.json")
        self.articles = self.manifest["articles"]
        self.state = read_json(state_path) if state_path.exists() else {
            "version": 1, "base_url": self.base_url, "collection": collection, "items": {}}
        if self.state.get("base_url") != self.base_url or self.state.get("collection") != collection:
            raise PublicationError("Publication state belongs to a different site or collection")
        self.by_file = {}
        self.raw = {}
        self._validate()

    def _validate(self):
        if len(self.articles) != self.manifest["article_count"]:
            raise PublicationError("Article count mismatch")
        keys = [item["key"] for item in self.articles]
        slugs = [item["desired_slug"] for item in self.articles]
        if len(set(keys)) != len(keys) or len(set(slugs)) != len(slugs):
            raise PublicationError("Duplicate article key or slug")
        for article in self.articles:
            if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", article["desired_slug"]):
                raise PublicationError("Invalid article slug")
            path = (self.content / article["file"]).resolve()
            if not path.is_relative_to(self.content.resolve()) or path in self.by_file:
                raise PublicationError("Invalid or duplicate article path")
            text = path.read_text(encoding="utf-8")
            if sha256(text) != article["sha256"]:
                raise PublicationError(f"Source hash mismatch: {article['file']}")
            if not text.startswith("# ") or "\n" not in text:
                raise PublicationError(f"Missing title: {article['file']}")
            self.by_file[path] = article["key"]
            self.raw[article["key"]] = text
        key_set = set(keys)
        for article in self.articles:
            if not set(article["dependencies"]).issubset(key_set):
                raise PublicationError("Unknown article dependency")
            path = (self.content / article["file"]).resolve()
            targets = set()
            for match in LINK.finditer(self.raw[article["key"]]):
                target = urlsplit(match[2])
                if target.scheme or target.netloc or not target.path:
                    continue
                linked = (path.parent / target.path).resolve()
                if linked not in self.by_file:
                    raise PublicationError(f"Unknown relative link in {article['file']}")
                targets.add(self.by_file[linked])
            if not targets.issubset(set(article["dependencies"])):
                raise PublicationError(f"Missing dependency for relative link in {article['file']}")
        self.ordered = self._ordered_articles()

    def _ordered_articles(self):
        result, done = [], set()
        pending = sorted(self.articles, key=lambda a: a["type"] != "supplement")
        while pending:
            eligible = next((a for a in pending if set(a["dependencies"]).issubset(done)), None)
            if eligible is None:
                raise PublicationError("Cyclic article dependencies")
            result.append(eligible)
            done.add(eligible["key"])
            pending.remove(eligible)
        return result

    def _payload(self, article):
        title, body = self.raw[article["key"]].split("\n", 1)
        source = self.content / article["file"]

        def resolve(match):
            target = urlsplit(match[2])
            if target.scheme or target.netloc or not target.path:
                return match[0]
            key = self.by_file[(source.parent / target.path).resolve()]
            if key not in self.state["items"]:
                raise PublicationError(f"Dependency not published: {key}")
            url = self.state["items"][key]["url"]
            if target.fragment:
                url += "#" + target.fragment
            return match[1] + url + match[3]

        return {"title": title[2:].strip(), "body": LINK.sub(resolve, body.lstrip("\n")),
                "slug": article["desired_slug"], "lang": "ru", "font": "sans"}

    def publish(self):
        collections = self.api.owned_collections()
        collection = next((c for c in collections if c["alias"] == self.collection), None)
        if collection is None:
            raise PublicationError("Owner token does not own the course collection")
        self.collection_url = collection.get("url") or f"{self.base_url}/{self.collection}/"
        if not self.collection_url.startswith(self.base_url + "/"):
            raise PublicationError("Collection URL does not match public site")
        result = {"created": 0, "updated": 0, "unchanged": 0}
        for article in self.ordered:
            payload = self._payload(article)
            fingerprint = payload_hash(payload)
            previous = self.state["items"].get(article["key"])
            remote = self.api.find_post(self.collection, article["desired_slug"])
            if previous and remote and previous["remote_id"] != remote["id"]:
                raise PublicationError(f"Remote identity conflict: {article['key']}")
            action = "created"
            if remote:
                remote_hash = payload_hash(remote)
                if remote_hash == fingerprint:
                    action = "unchanged"
                elif previous and remote_hash == previous["published_sha256"]:
                    remote = self.api.update_post(remote["id"], payload)
                    action = "updated"
                else:
                    raise PublicationError(f"Remote content conflict: {article['key']}")
            else:
                try:
                    remote = self.api.create_post(self.collection, payload)
                except PublicationError:
                    # A lost response can follow a committed create. Reconcile by
                    # stable slug before allowing a later invocation to retry it.
                    remote = self.api.find_post(self.collection, article["desired_slug"])
                    if remote is None or payload_hash(remote) != fingerprint:
                        raise
            # Re-fetch the stored representation: never publish a catalog based
            # solely on a response to an unverified mutation.
            stored = self.api.find_post(self.collection, article["desired_slug"])
            if not stored or stored["id"] != remote["id"] or payload_hash(stored) != fingerprint:
                raise PublicationError(f"Stored post verification failed: {article['key']}")
            url = stored.get("url") or urljoin(self.collection_url, stored["slug"])
            if not url.startswith(self.base_url + "/") or stored["slug"] != article["desired_slug"]:
                raise PublicationError(f"Unexpected public URL or slug: {article['key']}")
            self.state["items"][article["key"]] = {
                "remote_id": stored["id"], "slug": stored["slug"], "url": url,
                "source_sha256": article["sha256"], "published_sha256": fingerprint}
            atomic_json(self.state_path, self.state)
            result[action] += 1
        self._publish_navigation()
        self._write_catalogs()
        return result

    def _publish_navigation(self):
        state = self.state["items"]
        messages = read_json(self.content / "bot-messages.json")["items"]
        lessons = read_json(self.content / "lessons.json")["items"]
        lesson_titles = {i["id"]: i.get("title", i["id"]) for i in lessons}
        lines = ["# Creator Hero · программа", "", "Один небольшой урок в день. "
                 "Практика и дополнительные материалы — по желанию.", "", "## Уроки", ""]
        for message in sorted(messages, key=lambda item: item["day"]):
            key = message["article_key"]
            lines.append(f"{message['day']}. [{lesson_titles[message['lesson_id']]}]({state[key]['url']})")
        lines.extend(["", "## Дополнительные материалы", ""])
        for article in self.articles:
            if article["type"] == "supplement":
                lines.append(f"- [{article['title']}]({state[article['key']]['url']})")
        text = "\n".join(lines) + "\n"
        title, body = text.split("\n", 1)
        payload = {"slug": "course", "title": title[2:], "body": body.lstrip("\n"),
                   "lang": "ru", "font": "sans"}
        fingerprint = payload_hash(payload)
        previous = self.state.get("navigation", {}).get("course")
        remote = self.api.find_post(self.collection, "course")
        if remote:
            remote_hash = payload_hash(remote)
            if previous and previous["remote_id"] != remote["id"]:
                raise PublicationError("Navigation identity conflict")
            if remote_hash != fingerprint:
                if not previous or remote_hash != previous["published_sha256"]:
                    raise PublicationError("Navigation content conflict")
                self.api.update_post(remote["id"], payload)
        else:
            try:
                self.api.create_post(self.collection, payload)
            except PublicationError:
                remote = self.api.find_post(self.collection, "course")
                if not remote or payload_hash(remote) != fingerprint:
                    raise
        stored = self.api.find_post(self.collection, "course")
        if not stored or payload_hash(stored) != fingerprint:
            raise PublicationError("Navigation verification failed")
        url = stored.get("url") or urljoin(self.collection_url, "course")
        if not url.startswith(self.base_url + "/"):
            raise PublicationError("Unexpected navigation URL")
        self.state["navigation"] = {"course": {"remote_id": stored["id"], "url": url,
                                               "published_sha256": fingerprint}}
        atomic_json(self.state_path, self.state)
        (self.content / "course-index.md").write_text(text, encoding="utf-8")

    def _write_catalogs(self):
        state = self.state["items"]
        for article in self.articles:
            record = state[article["key"]]
            article.update(url=record["url"], remote_id=record["remote_id"],
                           published_sha256=record["published_sha256"])
        self.manifest.update(base_url=self.base_url, collection=self.collection, published=True,
                             site_status="writefreely_public", content_status="published",
                             course_url=self.state["navigation"]["course"]["url"])
        updates = [(self.content / "publication.json", self.manifest)]
        for name, key, prefix in [("lessons.json", "id", "lesson"),
                                  ("supplements.json", "id", "supplement"),
                                  ("chain.json", "id", "lesson")]:
            if not (self.content / name).exists():
                continue
            path = self.content / name
            catalog = read_json(path)
            for item in catalog["items"]:
                record = state[f"{prefix}:{item[key]}"]
                item.update(url=record["url"], remote_id=record["remote_id"],
                            published_sha256=record["published_sha256"], publication_status="published")
            catalog.update(publication_status="published",
                           course_url=self.state["navigation"]["course"]["url"])
            updates.append((path, catalog))
        messages_path = self.content / "bot-messages.json"
        messages = read_json(messages_path)
        for item in messages["items"]:
            item["article_url"] = state[item["article_key"]]["url"]
        messages["delivery_status"] = "published_not_scheduled"
        runtime = {"brand": "Creator Hero", "count": len(messages["items"]), "items": [
            {key: item[key] for key in ("day", "lesson_id", "text", "button_text", "article_url")}
            for item in messages["items"]]}
        updates.append((messages_path, messages))
        # Write the runtime catalog last, only after every article was verified.
        for path, catalog in updates:
            atomic_json(path, catalog)
        atomic_json(self.root / "bot/data/lesson-catalog.json", runtime)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-url", help="Optional loopback API endpoint; public URLs still use --base-url")
    parser.add_argument("--collection", default="creator-hero")
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        state = args.state or args.root / "docs/lesson-content/publication-state.json"
        if args.dry_run:
            pub = Publisher(args.root, None, args.collection, args.base_url, state)
            print(json.dumps({"validated_articles": len(pub.ordered),
                              "order": [a["key"] for a in pub.ordered]}, ensure_ascii=False))
            return 0
        if args.token_file is None:
            raise PublicationError("--token-file is required for publication")
        # The lock prevents another publisher from racing the slug existence
        # check. WriteFreely creates suffixed duplicates instead of rejecting them.
        import fcntl
        state.parent.mkdir(parents=True, exist_ok=True)
        with state.with_suffix(".lock").open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            api = WriteFreelyClient(args.api_url or args.base_url, args.token_file)
            pub = Publisher(args.root, api, args.collection, args.base_url, state)
            print(json.dumps(pub.publish(), ensure_ascii=False))
        return 0
    except (PublicationError, OSError, ValueError, KeyError) as exc:
        # No request bodies, headers, token contents, or remote server messages.
        message = str(exc) if isinstance(exc, PublicationError) else type(exc).__name__
        print("Publication stopped: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
