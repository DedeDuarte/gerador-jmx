from urllib.parse import urlsplit

STATIC_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tiff",
    ".css", ".js", ".map",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".mp3", ".webm", ".ogg", ".wav",
    ".pdf", ".zip", ".gz",
}

STATIC_MIME_PREFIXES = (
    "text/css",
    "text/javascript",
    "application/javascript",
    "image/",
    "font/",
    "audio/",
    "video/",
)


def is_static(url: str, mime: str) -> bool:
    """Return True if the request is for a static asset."""
    path = urlsplit(url).path.lower()
    segment = path.split("/")[-1]
    ext = "." + segment.rsplit(".", 1)[-1] if "." in segment else ""
    if ext in STATIC_EXTENSIONS:
        return True
    if any(mime.startswith(p) for p in STATIC_MIME_PREFIXES):
        return True
    return False


def is_third_party(url: str, main_host: str) -> bool:
    """Return True if the request goes to a different host than main_host."""
    return bool(main_host) and urlsplit(url).netloc != main_host
