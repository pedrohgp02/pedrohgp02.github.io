from __future__ import annotations

# Syncs the latest Out of Distribution post into the portfolio.
import html as html_lib
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

FEED_URL = "https://oodnotes.substack.com/feed"
INDEX_PATH = Path("index.html")
ASSETS_DIR = Path("assets")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/150 Safari/537.36"


def fetch_bytes(url: str, timeout: int = 25):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get_content_type()
    except Exception as urllib_exc:
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-LfsS",
                    "--max-time",
                    str(timeout),
                    "-A",
                    USER_AGENT,
                    "-H",
                    "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "-H",
                    "Accept-Language: en-US,en;q=0.9",
                    url,
                ],
                check=True,
                capture_output=True,
            )
            return result.stdout, None
        except Exception as curl_exc:
            raise RuntimeError(f"urllib failed ({urllib_exc}); curl failed ({curl_exc})") from curl_exc


def first_text(node, names):
    for name in names:
        child = node.find(name)
        if child is not None and child.text:
            return child.text.strip()
    return None


def find_image_in_html(fragment: str | None):
    if not fragment:
        return None
    fragment = html_lib.unescape(fragment)
    for pattern in [r'<img[^>]+src=["\']([^"\']+)', r'<img[^>]+data-src=["\']([^"\']+)']:
        m = re.search(pattern, fragment, re.I)
        if m:
            return html_lib.unescape(m.group(1))
    return None


def find_og_image(post_url: str):
    data, _ = fetch_bytes(post_url)
    text = data.decode("utf-8", errors="ignore")
    patterns = [
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return html_lib.unescape(m.group(1))
    return None


def parse_latest():
    data, _ = fetch_bytes(FEED_URL)
    root = ET.fromstring(data)
    item = root.find("./channel/item")
    if item is None:
        raise RuntimeError("No RSS item found")

    title = first_text(item, ["title"])
    link = first_text(item, ["link", "guid"])
    if not title or not link:
        raise RuntimeError("Latest RSS item is missing title or link")

    image_url = None

    enclosure = item.find("enclosure")
    if enclosure is not None and enclosure.attrib.get("url"):
        if enclosure.attrib.get("type", "").startswith("image/"):
            image_url = enclosure.attrib["url"]

    if not image_url:
        for child in list(item):
            tag = child.tag.lower()
            if tag.endswith("content") or tag.endswith("thumbnail"):
                url = child.attrib.get("url")
                if url:
                    image_url = url
                    break

    if not image_url:
        for child in list(item):
            if child.text and (child.tag.lower().endswith("encoded") or child.tag.lower().endswith("description")):
                image_url = find_image_in_html(child.text)
                if image_url:
                    break

    if not image_url:
        image_url = find_og_image(link)

    return title, link, image_url


def extension_for(content_type: str | None, url: str):
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if content_type in mapping:
        return mapping[content_type]
    m = re.search(r"\.(jpe?g|png|webp|gif)(?:\?|$)", url, re.I)
    if m:
        ext = m.group(1).lower()
        return ".jpg" if ext in {"jpg", "jpeg"} else f".{ext}"
    return ".jpg"


def download_image(url: str):
    data, content_type = fetch_bytes(url)
    if not data:
        raise RuntimeError("Latest post image was empty")
    ASSETS_DIR.mkdir(exist_ok=True)
    for old in ASSETS_DIR.glob("substack-latest.*"):
        old.unlink()
    path = ASSETS_DIR / f"substack-latest{extension_for(content_type, url)}"
    path.write_bytes(data)
    return path.as_posix()


def ensure_css(text: str):
    marker = "/* Automatic latest Substack image */"
    if marker in text:
        return text
    css = f'''\n    {marker}\n    .writing-visual.latest-post-visual{{position:relative;padding:0;min-height:300px;overflow:hidden;background:var(--g-1)}}\n    .writing-visual.latest-post-visual img{{width:100%;height:100%;min-height:300px;object-fit:cover;display:block;transition:transform 220ms ease}}\n    .writing-visual.latest-post-visual:hover img{{transform:scale(1.025)}}\n    .writing-visual-overlay{{position:absolute;left:18px;bottom:18px;padding:8px 11px;border-radius:999px;background:rgba(255,255,255,.9);border:1px solid var(--line);font-size:11px;font-weight:900;color:rgba(31,36,48,.78);backdrop-filter:blur(7px)}}\n'''
    return text.replace("</style>", css + "\n</style>", 1)


def update_index(title: str, link: str, image_path: str | None):
    text = INDEX_PATH.read_text()
    safe_title = html_lib.escape(title)
    safe_link = html_lib.escape(link, quote=True)

    latest_pattern = re.compile(
        r'<a class="writing-latest" href="[^"]+" target="_blank" rel="noreferrer">\s*<span>(?:Start here|Latest post)</span><strong>.*?</strong>\s*</a>',
        re.S,
    )
    latest_replacement = (
        f'<a class="writing-latest" href="{safe_link}" target="_blank" rel="noreferrer">\n'
        f'              <span>Latest post</span><strong>{safe_title} ↗</strong>\n'
        f'            </a>'
    )
    text, count = latest_pattern.subn(latest_replacement, text, count=1)
    if count != 1:
        raise RuntimeError("Could not locate the latest-post text block in index.html")

    if image_path:
        visual_pattern = re.compile(
            r'<a class="writing-visual(?: latest-post-visual)?" href="[^"]+" target="_blank" rel="noreferrer" aria-label="[^"]+">.*?</a>',
            re.S,
        )
        visual_replacement = (
            f'<a class="writing-visual latest-post-visual" href="{safe_link}" target="_blank" rel="noreferrer" aria-label="Open latest Out of Distribution post">\n'
            f'            <img src="{image_path}" alt="Cover image for {safe_title}" loading="lazy">\n'
            f'            <span class="writing-visual-overlay">Latest from Out of Distribution ↗</span>\n'
            f'          </a>'
        )
        text, count = visual_pattern.subn(visual_replacement, text, count=1)
        if count != 1:
            raise RuntimeError("Could not locate the writing visual block in index.html")
        text = ensure_css(text)

    INDEX_PATH.write_text(text)


def main():
    try:
        title, link, image_url = parse_latest()
        image_path = None
        if image_url:
            try:
                image_path = download_image(image_url)
            except Exception as exc:
                print(f"Image sync skipped: {exc}")
        update_index(title, link, image_path)
        print(f"Synced latest Substack post: {title}")
    except Exception as exc:
        print(f"Substack sync skipped safely: {exc}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
