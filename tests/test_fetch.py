"""Reading a feed: what's an article and what isn't."""
from fetch import fetch_source
from sources import Source

GOOGLE_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Africa News Reports | Latest News in Africa - AP News</title><link>https://news.google.com/a</link><source url="https://apnews.com">AP News</source></item>
<item><title>Saudi Arabia - AP News</title><link>https://news.google.com/b</link><source url="https://apnews.com">AP News</source></item>
<item><title>Lula wins a fourth term as Brazil's president - AP News</title><link>https://news.google.com/c</link><source url="https://apnews.com">AP News</source></item>
</channel></rss>"""


class _Response:
    content = GOOGLE_FEED

    def raise_for_status(self):
        pass


def test_google_topic_and_section_pages_are_not_articles(monkeypatch):
    monkeypatch.setattr("fetch.requests.get", lambda *a, **k: _Response())
    items = fetch_source(Source("ap", "AP", "https://news.google.com/rss", 1.0, via_google=True))
    assert [i["title"] for i in items] == ["Lula wins a fourth term as Brazil's president"]
