"""Only serve owned media. Protected qr-* collection images stay isolated from public static media."""
import re
from urllib.parse import unquote, urlsplit
from django.conf import settings

QR_FILENAME_RE = re.compile(r'qr-[A-Za-z0-9._-]+\.(?:jpg|jpeg|png|webp)\Z')


def owned_media_path(value):
    if not isinstance(value, str) or not value:
        return ''
    try:
        parsed = urlsplit(value)
        path = value
        for _ in range(3):
            path = unquote(path)
        if (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment
                or '%' in value or path != value
                or not value.startswith(('/images/', '/media/'))
                or '\\' in path or any(ord(char) < 32 for char in path)
                or any(part in ('', '.', '..') for part in path.split('/')[1:])):
            return ''
    except (TypeError, ValueError):
        return ''
    return value


def protected_qr_image(value, stall_id=None):
    cleaned = owned_media_path(value)
    if not cleaned:
        return ''
    media_url = settings.MEDIA_URL
    if not cleaned.startswith(media_url):
        return ''
    rel_path = cleaned[len(media_url):]
    parts = rel_path.split('/')
    if len(parts) != 3 or parts[0] != 'merchants' or not parts[1].isdigit():
        return ''
    if stall_id is not None and int(parts[1]) != int(stall_id):
        return ''
    if not QR_FILENAME_RE.fullmatch(parts[2]):
        return ''
    return cleaned


def public_image(value):
    cleaned = owned_media_path(value)
    if not cleaned or '/qr-' in cleaned.lower():
        return ''
    return cleaned
