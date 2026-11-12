"""Stable location consent; routine position heartbeats do not change the token."""
import json

from django.utils.crypto import salted_hmac


def location_snapshot(stall):
    location = getattr(stall, 'location', None)
    if location is None:
        return {'address': '', 'latitude': None, 'longitude': None, 'location_token': ''}
    values = [stall.pk, location.address, float(location.latitude), float(location.longitude)]
    token = salted_hmac('market.checkout.location.v1',
        json.dumps(values, ensure_ascii=False, separators=(',', ':')), algorithm='sha256').hexdigest()
    return {'address': location.address, 'latitude': location.latitude,
        'longitude': location.longitude, 'location_token': token}
