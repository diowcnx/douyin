import json
import subprocess
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from automation import auto_pipeline


def test_extract_tiktok_product_reads_redirect_og_info(monkeypatch):
    info = {"title": "เครื่องปั่นมือถือ 300W", "image": "https://example.com/item.jpg"}
    location = "https://shop.tiktok.com/view?{}".format(
        urllib.parse.urlencode({"og_info": json.dumps(info, ensure_ascii=False)})
    )
    response = SimpleNamespace(headers={"Location": location})
    monkeypatch.setattr(auto_pipeline, "_open_without_redirect", lambda url: response)

    assert auto_pipeline.extract_tiktok_product("https://shop.tiktok.com/item") == info


def test_build_ass_subtitles_uses_large_yellow_text_and_black_box(tmp_path):
    output = tmp_path / "caption.ass"
    auto_pipeline.build_ass_subtitles(
        [{"start": 0, "end": 2.5, "text": "ตัวหนังสือสีเหลือง"}], output
    )

    contents = output.read_text(encoding="utf-8")
    assert "Fontsize" in contents
    assert "96,&H0000FFFF" in contents
    assert ",3,14,0,2," in contents
    assert "ตัวหนังสือสีเหลือง" in contents


def test_make_dialogues_covers_the_complete_duration():
    text = "หนึ่ง สอง สาม สี่ ห้า หก เจ็ด แปด เก้า"
    dialogues = auto_pipeline.make_dialogues(text, 18)

    assert dialogues[0]["start"] == 0
    assert dialogues[-1]["end"] == 18
    assert " ".join(item["text"] for item in dialogues) == text


def test_chinese_mask_samples_each_part_and_ignores_non_chinese(tmp_path, monkeypatch):
    frames = []

    def fake_ffmpeg(args, timeout=180):
        frames.append(args[1])
        return True

    class FakeOCR:
        def __call__(self, path):
            return SimpleNamespace(
                boxes=[[[10, 20], [210, 20], [210, 70], [10, 70]],
                       [[10, 80], [210, 80], [210, 130], [10, 130]]],
                txts=["中文", "English"],
                scores=[0.99, 0.99],
            )

    monkeypatch.setattr(auto_pipeline, "run_ffmpeg", fake_ffmpeg)
    regions = auto_pipeline._chinese_regions(tmp_path / "clip.mp4", 5.0, tmp_path, FakeOCR())

    assert len(frames) == 3
    assert [item["start"] for item in regions] == [0, 2, 4]
    assert [item["end"] for item in regions] == [2, 4, 5]
    assert regions[0]["boxes"] == [(0, 20, 444, 140)]
    assert "drawbox=" in auto_pipeline._mask_filters(regions)


def test_run_downloader_reports_expired_login(monkeypatch, tmp_path):
    result = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="LoginRequiredError status 2483"
    )
    monkeypatch.setattr(auto_pipeline, "DOWNLOADER_DIR", tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: result)

    with pytest.raises(auto_pipeline.DouyinLoginRequired, match="เซสชัน Douyin หมดอายุ"):
        auto_pipeline._run_downloader(["--search", "手持搅拌机"])


def test_media_filename_is_url_encoded():
    output = Path("ชื่อ คลิป.mp4")
    encoded = urllib.parse.quote(output.name)

    assert encoded.endswith(".mp4")
    assert "%20" in encoded


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://www.douyin.com/video/7519325654426881339", True),
        ("https://v.douyin.com/abc123/", True),
        ("https://v.iesdouyin.com/abc123/", True),
        ("https://shop.tiktok.com/th/pdp/1734182154051945647", False),
        ("https://www.douyin.com/user/abc123", False),
        ("https://notdouyin.com/video/7519325654426881339", False),
    ],
)
def test_only_direct_douyin_clips_are_accepted(url, expected):
    assert auto_pipeline.is_douyin_video_url(url) is expected


def test_product_link_is_rejected_without_creating_video(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_pipeline, "TEMP_DIR", tmp_path)

    with pytest.raises(auto_pipeline.PipelineError, match="ไม่สร้างวิดีโอจากภาพสินค้า"):
        auto_pipeline.process_url("https://shop.tiktok.com/th/pdp/1734182154051945647")

    assert list(tmp_path.iterdir()) == []


def test_process_urls_downloads_each_clip_in_order_before_composing(tmp_path, monkeypatch):
    urls = [
        "https://www.douyin.com/video/7519325654426881339",
        "https://www.douyin.com/video/7582795910027955465",
    ]
    downloaded = []
    composed = []
    delivered = []

    def fake_download(url, job_dir):
        downloaded.append((url, job_dir.name))
        return tmp_path / "source_{}.mp4".format(len(downloaded))

    def fake_compose(sources, voiceover, job_dir, output):
        composed.append((list(sources), output))
        return output.with_suffix(".jpg")

    monkeypatch.setattr(auto_pipeline, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(auto_pipeline, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(auto_pipeline, "download_douyin_video", fake_download)
    monkeypatch.setattr(auto_pipeline, "compose_video", fake_compose)
    monkeypatch.setattr(auto_pipeline, "notify_success", lambda *args: delivered.append(args))

    output = auto_pipeline.process_urls(urls)

    assert downloaded == [(urls[0], "clip_01"), (urls[1], "clip_02")]
    assert composed[0][0] == [tmp_path / "source_1.mp4", tmp_path / "source_2.mp4"]
    assert composed[0][1] == output
    assert len(delivered) == 1


def test_process_urls_does_not_send_partial_montage(tmp_path, monkeypatch):
    urls = [
        "https://www.douyin.com/video/7519325654426881339",
        "https://www.douyin.com/video/7582795910027955465",
    ]
    calls = []

    def fake_download(url, job_dir):
        calls.append(url)
        if len(calls) == 2:
            raise auto_pipeline.PipelineError("ดาวน์โหลดไม่สำเร็จ")
        return tmp_path / "first.mp4"

    monkeypatch.setattr(auto_pipeline, "TEMP_DIR", tmp_path)
    monkeypatch.setattr(auto_pipeline, "download_douyin_video", fake_download)
    monkeypatch.setattr(
        auto_pipeline, "compose_video",
        lambda *args: pytest.fail("must not compose a partial montage"),
    )

    with pytest.raises(auto_pipeline.PipelineError, match="คลิปลำดับที่ 2"):
        auto_pipeline.process_urls(urls)

    assert calls == urls
