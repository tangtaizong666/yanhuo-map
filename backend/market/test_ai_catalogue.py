import io
import json
import tempfile
import urllib.error
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import AuditLog, Product
from .tests import fixtures


class _FakeUpstreamResponse:
    def __init__(self, payload_bytes):
        self._stream = io.BytesIO(payload_bytes)

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _sample_jpeg_bytes(width=32, height=24, color='#ee7828'):
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, format='JPEG')
    return buf.getvalue()


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AiProductDraftTests(TestCase):
    def setUp(self):
        self.student, self.other, self.vendor, self.stall, self.product = fixtures()
        self.client = APIClient()
        self.client.force_authenticate(self.vendor)
        self.upload_url = f'/api/v1/merchant/stalls/{self.stall.pk}/image'
        self.draft_url = f'/api/v1/merchant/stalls/{self.stall.pk}/ai-product-draft'

    def _upload_dish_photo(self):
        image = SimpleUploadedFile('dish.jpg', _sample_jpeg_bytes(), content_type='image/jpeg')
        res = self.client.post(self.upload_url, {'file': image}, format='multipart')
        self.assertEqual(res.status_code, 201, res.data)
        return res.data['url']

    def test_permissions_strict_fields_and_media_isolation(self):
        with tempfile.TemporaryDirectory(prefix='yanhuo-ai-draft-') as media_dir, override_settings(MEDIA_ROOT=media_dir):
            dish_url = self._upload_dish_photo()
            qr_file = SimpleUploadedFile('qr.jpg', _sample_jpeg_bytes(), content_type='image/jpeg')
            qr_res = self.client.post(self.upload_url + '?purpose=payment_qr', {'file': qr_file}, format='multipart')
            self.assertEqual(qr_res.status_code, 201, qr_res.data)
            qr_url = qr_res.data['url']

            # Unauthorized / non-owner gets 404 or 403 without leaking existence.
            anon = APIClient()
            self.assertIn(anon.post(self.draft_url, {'image': dish_url}, format='json').status_code, (401, 403))

            self.client.force_authenticate(self.other)
            self.assertEqual(self.client.post(self.draft_url, {'image': dish_url}, format='json').status_code, 404)
            self.client.force_authenticate(self.student)
            self.assertEqual(self.client.post(self.draft_url, {'image': dish_url}, format='json').status_code, 404)

            self.client.force_authenticate(self.vendor)
            # Reject protected QR image, static asset, cross-stall path, missing file, and unknown fields.
            for bad_body in (
                {'image': qr_url},
                {'image': '/images/demo.jpg'},
                {'image': f'/media/merchants/{self.stall.pk + 99}/abcd1234.jpg'},
                {'image': f'/media/merchants/{self.stall.pk}/missing1234.jpg'},
                {'image': dish_url, 'price_cents': 900},
            ):
                with self.subTest(body=bad_body):
                    bad_res = self.client.post(self.draft_url, bad_body, format='json')
                    self.assertEqual(bad_res.status_code, 400, bad_res.data)

    @override_settings(DEEPSEEK_API_KEY='')
    def test_unconfigured_key_returns_truthful_fallback_and_factual_local_assembly(self):
        with tempfile.TemporaryDirectory(prefix='yanhuo-ai-unconf-') as media_dir, override_settings(MEDIA_ROOT=media_dir):
            dish_url = self._upload_dish_photo()
            res = self.client.post(
                self.draft_url,
                {
                    'image': dish_url,
                    'merchant_name': '鸡蛋灌饼',
                    'merchant_note': '现烙酥皮加生菜',
                    'selected_tags': ['现点现做', '份量实在'],
                },
                format='json',
            )
            self.assertEqual(res.status_code, 200, res.data)
            self.assertFalse(res.data['ai_available'])
            self.assertEqual(res.data['fallback_reason'], 'unconfigured')
            self.assertEqual(res.data['name'], '鸡蛋灌饼')
            self.assertEqual(res.data['description'], '现烙酥皮加生菜，现点现做，份量实在。')
            self.assertIn('未开启 DeepSeek', res.data['notice'])
            self.assertEqual(Product.objects.filter(stall=self.stall).count(), 1)
            self.assertTrue(AuditLog.objects.filter(action='ai_product_draft_fallback', target=str(self.stall.pk)).exists())

    @override_settings(
        DEEPSEEK_API_KEY='sk-test-never-expose-999',
        DEEPSEEK_API_BASE_URL='https://api.deepseek.com',
        DEEPSEEK_VISION_MODEL='deepseek-flash',
        AI_PRODUCT_DRAFT_DAILY_LIMIT=50,
    )
    def test_deepseek_vision_parses_sanitizes_hallucinations_and_respects_merchant_name(self):
        with tempfile.TemporaryDirectory(prefix='yanhuo-ai-live-') as media_dir, override_settings(MEDIA_ROOT=media_dir):
            dish_url = self._upload_dish_photo()
            captured = {}

            def fake_open(req, timeout=None):
                captured['url'] = req.full_url
                captured['auth'] = req.get_header('Authorization')
                captured['timeout'] = timeout
                captured['body'] = json.loads(req.data.decode('utf-8'))
                ai_json = {
                    'is_food': True,
                    'name': '铁板鸡蛋烤冷面',
                    'name_candidates': ['招牌烤冷面', '鸡蛋火腿烤冷面', '铁板鸡蛋烤冷面'],
                    'category': '铁板小吃',
                    'description': '铁板现烤冷面裹着金黄鸡蛋，刷上甜辣酱汁撒白芝麻，全校第一好吃，月售500份，热乎香软。',
                    'highlight_tags': ['铁板现做', '甜辣够味', '趁热吃更香'],
                    'ask_hint': '可补一句真实配料',
                    'notice': '',
                }
                envelope = {'choices': [{'message': {'content': '```json\n' + json.dumps(ai_json, ensure_ascii=False) + '\n```'}}]}
                return _FakeUpstreamResponse(json.dumps(envelope, ensure_ascii=False).encode('utf-8'))

            with patch('market.ai_catalogue.urllib.request.OpenerDirector.open', side_effect=fake_open):
                res = self.client.post(
                    self.draft_url,
                    {
                        'image': dish_url,
                        'merchant_name': '加蛋烤冷面',
                        'merchant_note': '刷甜辣酱',
                        'selected_tags': ['铁板现做'],
                    },
                    format='json',
                )
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(captured['url'], 'https://api.deepseek.com/chat/completions')
            self.assertEqual(captured['auth'], 'Bearer sk-test-never-expose-999')
            self.assertEqual(captured['body']['model'], 'deepseek-flash')
            self.assertEqual(captured['body']['response_format'], {'type': 'json_object'})
            user_blocks = captured['body']['messages'][1]['content']
            self.assertTrue(user_blocks[1]['image_url']['url'].startswith('data:image/jpeg;base64,'))

            self.assertTrue(res.data['ai_available'])
            self.assertTrue(res.data['is_food'])
            # Merchant's explicit name wins; AI's visual guess moves to candidates.
            self.assertEqual(res.data['name'], '加蛋烤冷面')
            self.assertIn('铁板鸡蛋烤冷面', res.data['name_candidates'])
            self.assertEqual(res.data['category'], '铁板小吃')
            # Banned fabricated rankings/sales are stripped automatically.
            self.assertNotIn('全校第一', res.data['description'])
            self.assertNotIn('月售', res.data['description'])
            self.assertIn('铁板现烤冷面裹着金黄鸡蛋', res.data['description'])

    @override_settings(DEEPSEEK_API_KEY='sk-test-secret-abc', AI_PRODUCT_DRAFT_DAILY_LIMIT=1)
    def test_non_food_warning_daily_quota_and_upstream_failure_never_leak_secret(self):
        with tempfile.TemporaryDirectory(prefix='yanhuo-ai-guard-') as media_dir, override_settings(MEDIA_ROOT=media_dir):
            dish_url = self._upload_dish_photo()

            def non_food_open(req, timeout=None):
                envelope = {
                    'choices': [{
                        'message': {
                            'content': json.dumps({
                                'is_food': False,
                                'name': '微信收款码',
                                'description': '不该出现的文案',
                                'notice': '检测到疑似二维码，请拍摄真实菜品照片。',
                            }, ensure_ascii=False)
                        }
                    }]
                }
                return _FakeUpstreamResponse(json.dumps(envelope, ensure_ascii=False).encode('utf-8'))

            with patch('market.ai_catalogue.urllib.request.OpenerDirector.open', side_effect=non_food_open):
                first = self.client.post(self.draft_url, {'image': dish_url}, format='json')
            self.assertEqual(first.status_code, 200, first.data)
            self.assertTrue(first.data['ai_available'])
            self.assertFalse(first.data['is_food'])
            self.assertEqual(first.data['description'], '')
            self.assertIn('二维码', first.data['notice'])

            # Second call hits AI_PRODUCT_DRAFT_DAILY_LIMIT=1 and degrades gracefully.
            second = self.client.post(self.draft_url, {'image': dish_url}, format='json')
            self.assertEqual(second.status_code, 200, second.data)
            self.assertFalse(second.data['ai_available'])
            self.assertEqual(second.data['fallback_reason'], 'quota_exhausted')

        with tempfile.TemporaryDirectory(prefix='yanhuo-ai-err-') as media_dir, override_settings(
            MEDIA_ROOT=media_dir, AI_PRODUCT_DRAFT_DAILY_LIMIT=10
        ):
            dish_url = self._upload_dish_photo()
            with patch(
                'market.ai_catalogue.urllib.request.OpenerDirector.open',
                side_effect=urllib.error.URLError('sk-test-secret-abc upstream timeout'),
            ):
                failed = self.client.post(self.draft_url, {'image': dish_url}, format='json')
            self.assertEqual(failed.status_code, 200, failed.data)
            self.assertFalse(failed.data['ai_available'])
            self.assertEqual(failed.data['fallback_reason'], 'upstream_error')
            self.assertNotIn('sk-test-secret-abc', json.dumps(failed.data, ensure_ascii=False))
