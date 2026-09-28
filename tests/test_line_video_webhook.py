import pytest
from aiohttp.test_utils import TestClient, TestServer

from automation import line_webhook_server


@pytest.mark.asyncio
async def test_product_link_gets_reply_without_starting_video_job(monkeypatch):
    replies = []
    monkeypatch.setattr(
        line_webhook_server,
        "reply_line_message",
        lambda token, message: replies.append((token, message)),
    )

    async with TestClient(TestServer(await line_webhook_server.init_app())) as client:
        response = await client.post(
            "/line-webhook",
            json={
                "events": [
                    {
                        "type": "message",
                        "replyToken": "test-reply",
                        "source": {"userId": line_webhook_server.LINE_USER_ID},
                        "message": {
                            "type": "text",
                            "text": "https://shop.tiktok.com/th/pdp/1734182154051945647",
                        },
                    }
                ]
            },
        )

    assert response.status == 200
    assert len(replies) == 1
    assert "ไม่ใช่คลิป Douyin" in replies[0][1]
    assert "ไม่สร้างวิดีโอจากภาพสินค้า" in replies[0][1]


@pytest.mark.asyncio
async def test_links_from_separate_messages_become_one_background_job(monkeypatch):
    replies = []
    jobs = []

    class FakeTimer:
        def __init__(self, seconds, callback, args):
            self.seconds = seconds
            self.callback = callback
            self.args = args
            self.daemon = False

        def start(self):
            pass

        def cancel(self):
            pass

    class ImmediateThread:
        def __init__(self, target, args, daemon):
            jobs.append((target, args, daemon))

        def start(self):
            pass

    monkeypatch.setattr(line_webhook_server.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(line_webhook_server.threading, "Timer", FakeTimer)
    monkeypatch.setattr(
        line_webhook_server,
        "reply_line_message",
        lambda token, message: replies.append((token, message)),
    )
    urls = [
        "https://www.douyin.com/video/7519325654426881339",
        "https://www.douyin.com/video/7582795910027955465",
    ]

    async with TestClient(TestServer(await line_webhook_server.init_app())) as client:
        for url in urls:
            response = await client.post(
                "/line-webhook",
                json={
                    "events": [
                        {
                            "type": "message",
                            "replyToken": "test-reply",
                            "source": {"userId": line_webhook_server.LINE_USER_ID},
                            "message": {"type": "text", "text": url},
                        }
                    ]
                },
            )
            assert response.status == 200

    assert len(replies) == 2
    assert "2 ลิงก์" in replies[-1][1]
    assert not jobs
    assert line_webhook_server.dispatch_pending_videos() == 2
    assert len(jobs) == 1
    assert jobs[0][1] == (urls,)


@pytest.mark.asyncio
async def test_two_links_in_one_message_are_queued_together(monkeypatch):
    queued = []
    replies = []
    monkeypatch.setattr(
        line_webhook_server,
        "queue_video_urls",
        lambda urls: queued.append(urls) or len(urls),
    )
    monkeypatch.setattr(
        line_webhook_server,
        "reply_line_message",
        lambda token, message: replies.append(message),
    )
    urls = [
        "https://www.douyin.com/video/7519325654426881339",
        "https://www.douyin.com/video/7582795910027955465",
    ]
    async with TestClient(TestServer(await line_webhook_server.init_app())) as client:
        response = await client.post(
            "/line-webhook",
            json={
                "events": [
                    {
                        "type": "message",
                        "replyToken": "test-reply",
                        "source": {"userId": line_webhook_server.LINE_USER_ID},
                        "message": {"type": "text", "text": "\n".join(urls)},
                    }
                ]
            },
        )
    assert response.status == 200
    assert queued == [urls]
    assert "2 ลิงก์" in replies[0]
