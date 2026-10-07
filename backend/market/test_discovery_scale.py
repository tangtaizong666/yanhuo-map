"""Reproducible synthetic scale measurements on the isolated test database.

Run explicitly: python manage.py test market.test_discovery_scale --noinput
These are machine-local API measurements, not production/user experience claims.
"""
import json
import statistics
import time
import tracemalloc
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient
from .models import BusinessSession, Product, Stall, StallLocation
from .tests import fixtures


@override_settings(DEMO_MODE=True, REST_FRAMEWORK={'DEFAULT_AUTHENTICATION_CLASSES': [], 'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'], 'DEFAULT_THROTTLE_CLASSES': []})
class DiscoveryScaleMeasurements(TestCase):
    def test_scale_payloads_stay_bounded(self):
        _, _, _, original, _ = fixtures()
        api = APIClient()
        measurements = []
        previous = 1
        for size in (100, 500, 2000):
            stalls = Stall.objects.bulk_create([Stall(merchant=original.merchant, area=original.area,
                name=f'规模测试摊位 {index}', category='小吃', transaction_enabled=True) for index in range(previous, size)])
            StallLocation.objects.bulk_create([StallLocation(stall=stall, address='校园测试点', latitude=31, longitude=121) for stall in stalls])
            sessions = BusinessSession.objects.bulk_create([BusinessSession(stall=stall, status='open', last_confirmed_at=timezone.now()) for stall in stalls])
            for stall, session in zip(stalls, sessions): stall.current_session = session
            Stall.objects.bulk_update(stalls, ['current_session'])
            Product.objects.bulk_create([Product(stall=stall, name=f'测试餐点 {index}', price_cents=800, stock=100,
                description='仅用于隔离数据库的性能测量', image='/images/food-jianbing.jpg') for stall in stalls for index in range(12)])
            previous = size
            for endpoint, params, limit in (
                ('/api/v1/stalls', {}, 20), ('/api/v1/stalls', {'page_size':50}, 50),
                ('/api/v1/products', {'status':'open'}, 20), ('/api/v1/stalls/map', {'bounds':'120,30,122,32'}, 200)):
                api.get(endpoint, params)  # warm framework/schema caches before recording
                timings=[]
                for _ in range(3):
                    start=time.perf_counter(); response=api.get(endpoint, params); timings.append((time.perf_counter()-start)*1000)
                with CaptureQueriesContext(connection) as queries:
                    response=api.get(endpoint, params)
                query_count = len(queries)
                self.assertGreater(query_count, 0)
                tracemalloc.start()
                api.get(endpoint, params)
                _, peak=tracemalloc.get_traced_memory(); tracemalloc.stop()
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(len(response.data['results']), limit)
                if endpoint == '/api/v1/stalls':
                    self.assertTrue(all(len(row['products']) <= 2 for row in response.data['results']))
                measurements.append({'stalls':size, 'products':Product.objects.count(), 'endpoint':endpoint,
                    'page_size':params.get('page_size',20), 'returned':len(response.data['results']), 'response_bytes':len(response.content),
                    'sql_queries':query_count, 'median_ms_3_runs':round(statistics.median(timings),2),
                    'python_peak_bytes':peak, 'database':connection.vendor})
        print('\nDISCOVERY_SCALE_RESULTS='+json.dumps(measurements, ensure_ascii=False))
