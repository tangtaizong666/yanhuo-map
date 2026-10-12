"""Only serve owned media. Legacy remote values remain available for owner repair."""
from urllib.parse import unquote, urlsplit


def public_image(value):
    if not isinstance(value, str) or not value:
        return ''
    try:
        parsed = urlsplit(value)
        path = value
        for _ in range(3):
            path = unquote(path)
        if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
                or not value.startswith(('/images/', '/media/'))
                or '\\' in path or any(ord(char) < 32 for char in path)
                or any(part in ('.', '..') for part in path.split('/'))):
            return ''
    except (TypeError, ValueError):
        return ''
    return value
