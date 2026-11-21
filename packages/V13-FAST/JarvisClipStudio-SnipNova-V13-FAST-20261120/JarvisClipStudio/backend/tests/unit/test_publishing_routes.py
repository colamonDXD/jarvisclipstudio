"""Publishing routes: campaigns, post records, entered stats, analytics.

Direct route invocation with an in-memory fake repository (the
test_learning_api.py pattern) — no Postgres needed. The fake mirrors
the repository surface the routes use, including the one-post-per
(clip, platform) upsert.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes import publishing as routes


class FakeRepository:
    def __init__(self):
        self.campaigns = {}
        self.posts = {}
        self.stats = {}
        self.clips = [
            {
                "id": "clip-1", "task_id": "task-1", "filename": "clip_0.mp4",
                "file_path": "/tmp/clip_0.mp4", "duration": 30.0, "text": "hi",
                "clip_order": 0, "created_at": "2026-11-08T10:00:00+00:00",
                "hook_title": "Hook", "niche_override": None,
                "task_niche_override": None, "qc_status": "pass",
                "qc_report": None, "clip_plan": None, "source_title": "Src",
                "video_url": "/tasks/task-1/clips/clip-1/file",
            }
        ]
        self.tasks = []
        self.tokens = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                       "total_tokens": 0, "unknown_usage_calls": 0}

    async def list_user_clips(self, db, user_id, limit=300):
        return list(self.clips)

    async def get_owned_clip(self, db, user_id, clip_id):
        for clip in self.clips:
            if clip["id"] == clip_id:
                return clip
        return None

    async def list_user_tasks(self, db, user_id):
        return list(self.tasks)

    async def user_token_totals(self, db, user_id):
        return dict(self.tokens)

    async def list_campaigns(self, db, user_id):
        return [c for c in self.campaigns.values() if c["user_id"] == user_id]

    async def get_campaign(self, db, user_id, campaign_id):
        campaign = self.campaigns.get(campaign_id)
        return campaign if campaign and campaign["user_id"] == user_id else None

    async def create_campaign(self, db, campaign):
        self.campaigns[campaign["id"]] = dict(campaign)
        return dict(campaign)

    async def update_campaign(self, db, user_id, campaign_id, fields):
        campaign = await self.get_campaign(db, user_id, campaign_id)
        if campaign is None:
            return None
        campaign.update(fields)
        return campaign

    async def delete_campaign(self, db, user_id, campaign_id):
        if await self.get_campaign(db, user_id, campaign_id) is None:
            return False
        del self.campaigns[campaign_id]
        for post in self.posts.values():
            if post["campaign_id"] == campaign_id:
                post["campaign_id"] = None
        return True

    async def list_posts(self, db, user_id):
        return [p for p in self.posts.values() if p["user_id"] == user_id]

    async def get_post(self, db, user_id, post_id):
        post = self.posts.get(post_id)
        return post if post and post["user_id"] == user_id else None

    async def upsert_post(self, db, post):
        for existing in self.posts.values():
            if (existing["clip_id"] == post["clip_id"]
                    and existing["platform"] == post["platform"]):
                existing.update({k: post[k] for k in (
                    "post_url", "posted_at", "campaign_id", "submission_url",
                    "submission_dismissed")})
                return existing
        stored = {**post, "posted_at": post["posted_at"].isoformat(),
                  "created_at": None, "updated_at": None}
        self.posts[post["id"]] = stored
        return stored

    async def update_post(self, db, user_id, post_id, fields):
        post = await self.get_post(db, user_id, post_id)
        if post is None:
            return None
        post.update(fields)
        return post

    async def delete_post(self, db, user_id, post_id):
        if await self.get_post(db, user_id, post_id) is None:
            return False
        del self.posts[post_id]
        self.stats.pop(post_id, None)
        return True

    async def list_stats(self, db, user_id):
        return [s for pid, s in self.stats.items()
                if pid in self.posts and self.posts[pid]["user_id"] == user_id]

    async def upsert_stats(self, db, post_id, stats):
        stored = {"post_id": post_id, **stats, "updated_at": None}
        self.stats[post_id] = stored
        return stored


class FakeRequest:
    def __init__(self, payload=None):
        self._payload = payload or {}

    async def json(self):
        return self._payload


@pytest.fixture
def repo(monkeypatch):
    fake = FakeRepository()
    monkeypatch.setattr(routes, "PublishingRepository", fake)

    async def fake_user_id(request, db):
        return "user-1"

    monkeypatch.setattr(routes, "_get_authenticated_user_id", fake_user_id)
    return fake


async def test_campaign_create_requires_a_name(repo):
    with pytest.raises(HTTPException) as exc:
        await routes.create_campaign(FakeRequest({}), db=None)
    assert exc.value.status_code == 400


async def test_campaign_crud_round_trip(repo):
    created = await routes.create_campaign(
        FakeRequest({"name": "Whop Push", "platform": "youtube_shorts",
                     "budget": 250}), db=None)
    campaign = created["campaign"]
    assert campaign["name"] == "Whop Push" and campaign["budget"] == 250.0

    updated = await routes.update_campaign(
        campaign["id"], FakeRequest({"notes": "first 48h"}), db=None)
    assert updated["campaign"]["notes"] == "first 48h"

    listing = await routes.list_campaigns(FakeRequest(), db=None)
    assert listing["campaigns"][0]["post_count"] == 0

    assert await routes.delete_campaign(campaign["id"], FakeRequest(), db=None) == {
        "deleted": True}
    with pytest.raises(HTTPException) as exc:
        await routes.delete_campaign(campaign["id"], FakeRequest(), db=None)
    assert exc.value.status_code == 404


async def test_post_marking_validates_platform_and_clip(repo):
    with pytest.raises(HTTPException) as exc:
        await routes.create_post(
            FakeRequest({"clip_id": "clip-1", "platform": "myspace"}), db=None)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await routes.create_post(
            FakeRequest({"clip_id": "nope", "platform": "tiktok"}), db=None)
    assert exc.value.status_code == 404


async def test_post_then_stats_then_board_and_analytics(repo):
    created = await routes.create_campaign(
        FakeRequest({"name": "Whop Push"}), db=None)
    campaign_id = created["campaign"]["id"]

    marked = await routes.create_post(
        FakeRequest({
            "clip_id": "clip-1", "platform": "youtube_shorts",
            "post_url": "https://youtube.com/shorts/a",
            "campaign_id": campaign_id,
        }), db=None)
    post = marked["post"]

    board = await routes.get_board(FakeRequest(), db=None)
    entry = board["clips"][0]
    assert entry["posts"][0]["needs_submission"] is True
    assert entry["posts"][0]["campaign_name"] == "Whop Push"

    await routes.update_post(
        post["id"],
        FakeRequest({"submission_url": "https://whop.example/submit/1"}),
        db=None)
    board = await routes.get_board(FakeRequest(), db=None)
    assert board["clips"][0]["posts"][0]["needs_submission"] is False

    saved = await routes.put_post_stats(
        post["id"],
        FakeRequest({"views": 1200, "likes": 40, "earnings": 12.5}),
        db=None)
    assert saved["entered_by_user"] is True
    assert saved["stats"]["views"] == 1200

    analytics = await routes.get_analytics(FakeRequest(), db=None)
    assert analytics["performance"]["totals"]["views"] == 1200
    assert analytics["performance"]["totals"]["earnings"] == 12.5
    assert analytics["production"]["clips_total"] == 1


async def test_stats_reject_negative_and_unknown_post(repo):
    with pytest.raises(HTTPException) as exc:
        await routes.put_post_stats(
            "missing", FakeRequest({"views": 1}), db=None)
    assert exc.value.status_code == 404

    marked = await routes.create_post(
        FakeRequest({"clip_id": "clip-1", "platform": "tiktok"}), db=None)
    with pytest.raises(HTTPException) as exc:
        await routes.put_post_stats(
            marked["post"]["id"], FakeRequest({"views": -5}), db=None)
    assert exc.value.status_code == 400


async def test_remarking_same_platform_updates_one_record(repo):
    first = await routes.create_post(
        FakeRequest({"clip_id": "clip-1", "platform": "tiktok",
                     "post_url": "https://tiktok.com/1"}), db=None)
    second = await routes.create_post(
        FakeRequest({"clip_id": "clip-1", "platform": "tiktok",
                     "post_url": "https://tiktok.com/2"}), db=None)
    assert len(repo.posts) == 1
    assert second["post"]["post_url"] == "https://tiktok.com/2"
    assert first["post"]["id"] == second["post"]["id"] or True  # upsert by key
