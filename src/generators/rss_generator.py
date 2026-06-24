import logging
import os
from datetime import datetime
from email.utils import formatdate
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom import minidom

logger = logging.getLogger(__name__)

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"


class RSSGenerator:
    def __init__(self, settings: dict):
        podcast_cfg = settings.get("podcast", {})
        self.base_url = os.getenv(
            "PODCAST_BASE_URL", "https://example.github.io/idol-podcast"
        )
        self.title = os.getenv("PODCAST_TITLE", "ライブアイドルデイリー")
        self.description = os.getenv(
            "PODCAST_DESCRIPTION",
            "ライブアイドルシーンの最新情報を毎朝お届けする音声ニュースです。"
            "ライブ情報・メンバー変動・解散情報を網羅しています。",
        )
        self.author = os.getenv("PODCAST_AUTHOR", "idol-podcast")
        self.email = os.getenv("PODCAST_EMAIL", "noreply@example.com")
        self.image_url = os.getenv(
            "PODCAST_IMAGE_URL", f"{self.base_url}/artwork.png"
        )
        self.max_episodes = podcast_cfg.get("max_episodes_in_rss", 7)
        self.category = podcast_cfg.get("category", "Music")
        self.subcategory = podcast_cfg.get("subcategory", "")

    def generate(self, episodes: list[dict]) -> str:
        nsmap = {
            "xmlns:itunes": ITUNES_NS,
            "xmlns:content": CONTENT_NS,
        }
        rss = Element("rss", version="2.0", **nsmap)
        channel = SubElement(rss, "channel")
        self._add_channel_metadata(channel)

        for episode in episodes[: self.max_episodes]:
            if episode.get("audio_path") and Path(episode["audio_path"]).exists():
                self._add_episode_item(channel, episode)

        xml_str = tostring(rss, encoding="unicode")
        dom = minidom.parseString(f'<?xml version="1.0" encoding="UTF-8"?>{xml_str}')
        pretty_xml = dom.toprettyxml(indent="  ", encoding=None)
        pretty_xml = pretty_xml.replace('<?xml version="1.0" ?>', "").strip()
        result = '<?xml version="1.0" encoding="UTF-8"?>\n' + pretty_xml
        logger.info(f"RSS生成完了: {len(episodes)}エピソード")
        return result

    def _add_channel_metadata(self, channel: Element) -> None:
        SubElement(channel, "title").text = self.title
        SubElement(channel, "link").text = self.base_url
        SubElement(channel, "description").text = self.description
        SubElement(channel, "language").text = "ja"
        SubElement(channel, "copyright").text = f"© {datetime.now().year} {self.author}"
        SubElement(channel, "lastBuildDate").text = formatdate(localtime=True)
        SubElement(channel, "generator").text = "Idol Podcast Generator"

        SubElement(channel, "{%s}author" % ITUNES_NS).text = self.author
        SubElement(channel, "{%s}owner" % ITUNES_NS).append(self._make_itunes_owner())
        SubElement(channel, "{%s}explicit" % ITUNES_NS).text = "false"
        SubElement(channel, "{%s}type" % ITUNES_NS).text = "episodic"

        img = SubElement(channel, "{%s}image" % ITUNES_NS)
        img.set("href", self.image_url)

        cat = SubElement(channel, "{%s}category" % ITUNES_NS)
        cat.set("text", self.category)
        if self.subcategory:
            subcat = SubElement(cat, "{%s}category" % ITUNES_NS)
            subcat.set("text", self.subcategory)

        channel_img = SubElement(channel, "image")
        SubElement(channel_img, "url").text = self.image_url
        SubElement(channel_img, "title").text = self.title
        SubElement(channel_img, "link").text = self.base_url

    def _make_itunes_owner(self) -> Element:
        owner = Element("{%s}owner" % ITUNES_NS)
        SubElement(owner, "{%s}name" % ITUNES_NS).text = self.author
        SubElement(owner, "{%s}email" % ITUNES_NS).text = self.email
        return owner

    def _add_episode_item(self, channel: Element, episode: dict) -> None:
        item = SubElement(channel, "item")
        date_str = episode["date"]
        title = episode.get("title", f"{date_str}のライブアイドルデイリー")
        SubElement(item, "title").text = title

        audio_filename = Path(episode["audio_path"]).name
        audio_url = f"{self.base_url}/episodes/{audio_filename}"

        SubElement(item, "link").text = audio_url
        SubElement(item, "guid", isPermaLink="false").text = f"idol-podcast-{date_str}"

        description = episode.get("description", title)
        SubElement(item, "description").text = description
        SubElement(item, "{%s}summary" % ITUNES_NS).text = description

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        SubElement(item, "pubDate").text = formatdate(dt.timestamp(), localtime=False)

        file_size = episode.get("file_size_bytes", 0) or 0
        enclosure = SubElement(item, "enclosure")
        enclosure.set("url", audio_url)
        enclosure.set("length", str(file_size))
        enclosure.set("type", "audio/mpeg")

        duration = episode.get("duration_seconds", 0) or 0
        SubElement(item, "{%s}duration" % ITUNES_NS).text = self._format_duration(duration)
        SubElement(item, "{%s}explicit" % ITUNES_NS).text = "false"
        SubElement(item, "{%s}episodeType" % ITUNES_NS).text = "full"

    @staticmethod
    def _format_duration(seconds: int) -> str:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
