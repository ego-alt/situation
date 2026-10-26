"""Reading a feed: what's an article and what isn't."""
from fetch import fetch_source
from sources import Source


def _item(title: bytes, link: bytes) -> bytes:
    """One Google News item, its headline ending in the outlet's name as Google writes it."""
    return (b"<item><title>" + title + b" - AP News</title><link>https://news.google.com/" + link + b"</link>"
            b'<source url="https://apnews.com">AP News</source></item>')


GOOGLE_FEED = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
               + _item(b"Africa News Reports | Latest News in Africa", b"a")
               + _item(b"Saudi Arabia", b"b")
               + _item(b"Lula wins a fourth term as Brazil's president", b"c")
               + b"</channel></rss>")


class _Response:
    content = GOOGLE_FEED

    def raise_for_status(self):
        pass


def test_google_topic_and_section_pages_are_not_articles(monkeypatch):
    monkeypatch.setattr("fetch.requests.get", lambda *a, **k: _Response())
    items = fetch_source(Source("ap", "AP", "https://news.google.com/rss", 1.0, via_google=True))
    assert [i["title"] for i in items] == ["Lula wins a fourth term as Brazil's president"]
