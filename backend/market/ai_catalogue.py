"""Multimodal dish-photo draft assistant for merchant single-item listing.

Uses the server-configured DeepSeek vision endpoint (default ``deepseek-flash``)
to recognize an already-uploaded stall dish photo and draft truthful copywriting
without ever auto-publishing unconfirmed prices, stock, or unseen ingredients.
"""
import base64
from datetime import timedelta
import io
import json
import logging
import re
from threading import BoundedSemaphore
import urllib.error
import urllib.request
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import F
from django.utils import timezone
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .auth_limits import TrustedUserRateThrottle
from .errors import BusinessError
from .merchant import ImageAddress, StrictInput
from .security_models import DailyQuotaCounter
from .services import audit
from .views import merchant_stall

logger = logging.getLogger(__name__)

_ai_slots = BoundedSemaphore(4)
_OWNED_DISH_IMAGE_RE = re.compile(
    r'^/media/merchants/([0-9]+)/([A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp))\Z'
)
_BANNED_CLAIM_RE = re.compile(
    r'(?:月售|日销|日卖|累计售出|已售|销量冠军|好评率|零差评|全校第[一1]|全市第[一1]|全网第[一1]|包治|秘方祖传|网红爆款|必点榜)[^，,。；;！!？?\n]*'
)
_UNCONFIRMED_FACT_RE = re.compile(
    r'(?:'
    r'\d+(?:\.\d+)?\s*(?:千克|公斤|毫升|盎司|厘米|大卡|千卡|卡路里|克|斤|两|升|寸)'
    r'|(?<![A-Za-z])\d+(?:\.\d+)?\s*(?:kcal|kg|ml|oz|cm|mm|g)(?![A-Za-z])'
    r'|\d+\s*(?:分钟|秒|小时)[^，,。；;！!？?\n]*(?:出餐|做好|可取|立等|出锅|必达)'
    r'|(?:不含|无|零|0)[^，,。；;！!？?\n]{0,8}(?:过敏原|花生|坚果|麸质|乳制品|牛奶|鸡蛋|海鲜|大豆|芝麻|添加剂|防腐剂|色素|香精|反式脂肪)'
    r'|过敏原|低敏|无敏|(?:零|无|0)添加|有机认证|清真认证|非转基因'
    r'|花生油|菜籽油|橄榄油|大豆油|葵花籽油|猪油|牛油果油|动物奶油'
    r'|纯手工|100%|百分百|纯牛肉|纯羊肉|纯猪肉|纯虾仁|原切牛|鲜活现杀'
    r'|低脂|(?:零|0)脂|低卡|(?:零|0)卡|(?:无|零|0)糖|控糖|减肥|减脂|高蛋白|滋补养生'
    r')',
    re.IGNORECASE,
)
_DEFAULT_TAGS = ['趁热享用', '色泽诱人', '香气扑鼻', '街头风味']

_SYSTEM_PROMPT = """你是校园街头美食平台「烟火地图」的商家菜品上架助手。
请结合商家实拍照片与商家补充信息，输出严格的 json 对象（不要输出 markdown 代码块之外的任何解释）。

必须遵守以下真实与合规铁律：
1. 只能描述照片中肉眼清晰可见的食物外观、色泽、形态，以及商家明确填写的补充信息。
2. 严禁编造照片无法确认且商家未提及的肉类品种、内馅、配料、过敏原（如花生、坚果、海鲜、乳制品等）、克重尺寸、出餐时长、销量、评分、折扣或夸大极限词（如“全校第一”“日销百份”）。
3. 文案风格应朴实、自然、有校园街头烟火气，控制在 28 到 80 个汉字之间，不堆砌浮夸形容词。
4. 若图片明显不是餐饮食物（如收款二维码、证件、纯文字截图、黑屏或完全无关物品），必须将 is_food 设为 false，name 与 description 设为空字符串，并在 notice 中温和提示商家重拍菜品实拍图（若疑似二维码，提醒收款码请在店铺收款设置上传，勿作公开商品图）。

输出 json 格式示例：
{
  "is_food": true,
  "name": "鸡蛋火腿烤冷面",
  "name_candidates": ["招牌烤冷面", "铁板烤冷面"],
  "category": "铁板小吃",
  "description": "铁板现烤冷面裹着金黄鸡蛋，刷上浓郁酱汁，撒有葱花与白芝麻，热乎香软。",
  "highlight_tags": ["铁板现做", "酱香浓郁", "外软里香", "趁热吃更香"],
  "ask_hint": "可点选特色，或补一句真实主料（如加烤肠、甜辣口）再润色文案",
  "notice": ""
}"""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AiDraftThrottle(TrustedUserRateThrottle):
    scope = 'ai_draft'


class AiProductDraftInput(StrictInput):
    image = ImageAddress()
    merchant_name = serializers.CharField(max_length=80, allow_blank=True, required=False, default='')
    merchant_note = serializers.CharField(max_length=120, allow_blank=True, required=False, default='')
    selected_tags = serializers.ListField(
        child=serializers.CharField(max_length=20, allow_blank=False),
        max_length=6,
        required=False,
        default=list,
    )


def _clean_line(value, max_len):
    if not isinstance(value, str):
        return ''
    cleaned = re.sub(r'[\r\n\t]+', ' ', value)
    cleaned = ''.join(ch for ch in cleaned if ord(ch) >= 32)
    return re.sub(r'\s{2,}', ' ', cleaned).strip()[:max_len]


def _has_unconfirmed_fact(text, merchant_source=''):
    normalized_source = re.sub(r'\s+', '', merchant_source or '').lower()
    for match in _UNCONFIRMED_FACT_RE.finditer(text):
        token = re.sub(r'\s+', '', match.group(0)).lower()
        if token and token not in normalized_source:
            return True
    return False


def _clean_description(value, *, merchant_source='', enforce_source=False):
    text = _clean_line(value, 240)
    if not text:
        return ''
    text = _BANNED_CLAIM_RE.sub('', text)
    if enforce_source:
        kept_clauses = []
        for raw_clause in re.split(r'[，,。；;！!？?\n]+', text):
            clause = re.sub(r'^[、\s]+|[、\s]+$', '', raw_clause).strip()
            if not clause:
                continue
            if _has_unconfirmed_fact(clause, merchant_source):
                continue
            kept_clauses.append(clause)
        if not kept_clauses:
            return ''
        text = '，'.join(kept_clauses) + '。'
    else:
        text = re.sub(r'[，、；]{2,}', '，', text)
        text = re.sub(r'^[，、；。\s]+', '', text).strip()
    return text[:200]


def _unique_clean_list(items, *, max_items, max_len, exclude=''):
    if not isinstance(items, list):
        return []
    seen = {exclude} if exclude else set()
    result = []
    for raw in items:
        item = _clean_line(raw, max_len)
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= max_items:
            break
    return result


def _filter_ai_tags(items, *, merchant_source=''):
    cleaned = _unique_clean_list(items, max_items=8, max_len=12)
    safe = []
    for tag in cleaned:
        if _BANNED_CLAIM_RE.search(tag):
            continue
        if _has_unconfirmed_fact(tag, merchant_source):
            continue
        safe.append(tag)
        if len(safe) >= 5:
            break
    return safe


def daily_ai_quota_exhausted():
    limit = settings.AI_PRODUCT_DRAFT_DAILY_LIMIT
    if limit <= 0:
        return True
    today = timezone.localdate()
    now = timezone.now()
    updated = DailyQuotaCounter.objects.filter(
        scope='ai_product_draft', day=today, count__lt=limit
    ).update(count=F('count') + 1, updated_at=now)
    if updated:
        return False
    if DailyQuotaCounter.objects.filter(scope='ai_product_draft', day=today).exists():
        return True
    _, created = DailyQuotaCounter.objects.get_or_create(
        scope='ai_product_draft', day=today, defaults={'count': 0, 'updated_at': now}
    )
    if created:
        DailyQuotaCounter.objects.filter(day__lt=today - timedelta(days=90)).delete()
    updated = DailyQuotaCounter.objects.filter(
        scope='ai_product_draft', day=today, count__lt=limit
    ).update(count=F('count') + 1, updated_at=now)
    return updated == 0


def _encode_owned_dish_image(stall, image_url):
    match = _OWNED_DISH_IMAGE_RE.fullmatch(image_url or '')
    if not match or int(match.group(1)) != stall.pk:
        raise BusinessError('请先上传本摊位的菜品实拍照片。', 'invalid_image', status=400)
    filename = match.group(2)
    if filename.lower().startswith('qr-'):
        raise BusinessError('收款码图片不能用作公开商品图片。', 'invalid_image', status=400)
    rel_path = f'merchants/{stall.pk}/{filename}'
    if not default_storage.exists(rel_path):
        raise BusinessError('未找到已上传的菜品照片，请重新拍照或上传。', 'invalid_image', status=400)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with default_storage.open(rel_path, 'rb') as stream:
                with Image.open(stream, formats=('JPEG', 'PNG', 'WEBP')) as img:
                    if img.width * img.height > 20_000_000:
                        raise BusinessError('图片尺寸超出限制，请重新上传。', 'image_too_large', status=400)
                    img.load()
                    transposed = ImageOps.exif_transpose(img).convert('RGB')
                    transposed.thumbnail((1024, 1024))
                    buf = io.BytesIO()
                    transposed.save(buf, format='JPEG', quality=82, optimize=True)
                    return base64.b64encode(buf.getvalue()).decode('ascii')
    except BusinessError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise BusinessError('菜品照片无法读取，请重新上传有效图片。', 'invalid_image', status=400)


def _factual_local_description(merchant_note, selected_tags):
    parts = []
    if merchant_note:
        parts.append(merchant_note.rstrip('。；，, '))
    if selected_tags:
        parts.append('，'.join(selected_tags))
    if not parts:
        return ''
    return _clean_description('，'.join(parts) + '。')


def _fallback_draft(*, reason, notice, default_category, merchant_name, merchant_note, selected_tags):
    return {
        'ai_available': False,
        'fallback_reason': reason,
        'is_food': True,
        'name': merchant_name,
        'name_candidates': [],
        'category': default_category,
        'description': _factual_local_description(merchant_note, selected_tags),
        'highlight_tags': list(_DEFAULT_TAGS),
        'ask_hint': '可点选符合实物的标签或补充真实主料，系统会帮你整理成菜单介绍',
        'notice': notice,
    }


def _extract_json_object(raw_text):
    text = (raw_text or '').strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*```$', '', text).strip()
    return json.loads(text)


def _request_deepseek_vision(*, b64_jpeg, stall, existing_categories, merchant_name, merchant_note, selected_tags):
    context_lines = [
        f'摊位名称：{stall.name}',
        f'摊位主营品类：{stall.category or "街头小吃"}',
    ]
    if existing_categories:
        context_lines.append(f'本店已有菜单分类（优先从中匹配，不合适再新建简短分类）：{"、".join(existing_categories)}')
    if merchant_name:
        context_lines.append(f'摊主确认或指定的菜名：{merchant_name}')
    if selected_tags:
        context_lines.append(f'摊主勾选的真实特点：{"、".join(selected_tags)}')
    if merchant_note:
        context_lines.append(f'摊主补充的真实说明：{merchant_note}')
    context_lines.append('请根据实拍照片与上述信息返回 json 对象。')

    payload = {
        'model': settings.DEEPSEEK_VISION_MODEL,
        'thinking': {'type': 'disabled'},
        'temperature': 0.35,
        'max_tokens': 500,
        'response_format': {'type': 'json_object'},
        'messages': [
            {'role': 'system', 'content': _SYSTEM_PROMPT},
            {
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': '\n'.join(context_lines)},
                    {
                        'type': 'image_url',
                        'image_url': {'url': f'data:image/jpeg;base64,{b64_jpeg}'},
                    },
                ],
            },
        ],
    }
    url = f'{settings.DEEPSEEK_API_BASE_URL}/chat/completions'
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {settings.DEEPSEEK_API_KEY}',
            'User-Agent': 'YanhuoMap/1.0',
        },
        method='POST',
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(req, timeout=settings.AI_PRODUCT_DRAFT_TIMEOUT_SECONDS) as resp:
        raw_body = resp.read(64 * 1024 + 1)
        if len(raw_body) > 64 * 1024:
            raise ValueError('AI response exceeded 64 KiB limit')
    envelope = json.loads(raw_body.decode('utf-8'))
    if not isinstance(envelope, dict):
        raise ValueError('AI response envelope is not a JSON object')
    choices = envelope.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ValueError('Missing choices in AI response')
    message = choices[0].get('message')
    if not isinstance(message, dict):
        raise ValueError('Missing message object in AI response')
    content = message.get('content')
    if not isinstance(content, str) or not content.strip():
        raise ValueError('Empty message content in AI response')
    return _extract_json_object(content)


def _validate_ai_schema(parsed):
    if not isinstance(parsed, dict):
        raise ValueError('Parsed AI content is not a JSON object')
    if not isinstance(parsed.get('is_food'), bool):
        raise ValueError('Missing or non-boolean is_food in AI response')
    for list_key in ('name_candidates', 'highlight_tags'):
        val = parsed.get(list_key)
        if val is not None and not isinstance(val, list):
            raise ValueError(f'Invalid {list_key} type in AI response')
    for str_key in ('name', 'category', 'description', 'ask_hint', 'notice'):
        val = parsed.get(str_key)
        if val is not None and not isinstance(val, str):
            raise ValueError(f'Invalid {str_key} type in AI response')


def _sanitize_ai_result(parsed, *, default_category, merchant_name, merchant_note, selected_tags):
    _validate_ai_schema(parsed)
    is_food = parsed['is_food']
    if not is_food:
        notice = _clean_line(
            parsed.get('notice') or '这张图看起来不像菜品实拍（收款码请在店铺收款设置中上传）。建议重拍菜品实物后再上架。',
            140,
        )
        return {
            'ai_available': True,
            'fallback_reason': '',
            'is_food': False,
            'name': merchant_name,
            'name_candidates': [],
            'category': default_category,
            'description': '',
            'highlight_tags': list(_DEFAULT_TAGS),
            'ask_hint': '请换一张清晰的菜品实拍图，或核实确认后手动填写菜名与售价',
            'notice': notice,
        }

    merchant_source = ' '.join(part for part in [merchant_name, merchant_note, *selected_tags] if part)
    ai_name = _clean_line(parsed.get('name'), 80)
    effective_name = merchant_name or ai_name
    raw_candidates = list(parsed.get('name_candidates') or [])
    if merchant_name and ai_name and ai_name != merchant_name:
        raw_candidates.insert(0, ai_name)
    candidates = _unique_clean_list(raw_candidates, max_items=3, max_len=30, exclude=effective_name)
    category = _clean_line(parsed.get('category'), 30) or default_category
    description = _clean_description(
        parsed.get('description'),
        merchant_source=merchant_source,
        enforce_source=True,
    )
    if not description and (merchant_note or selected_tags):
        description = _factual_local_description(merchant_note, selected_tags)
    tags = _filter_ai_tags(parsed.get('highlight_tags') or [], merchant_source=merchant_source) or list(_DEFAULT_TAGS)
    ask_hint = _clean_line(
        parsed.get('ask_hint') or '可点选符合实物的特色，或补一句真实主料后重写文案',
        80,
    )
    notice = _clean_line(parsed.get('notice'), 140)
    return {
        'ai_available': True,
        'fallback_reason': '',
        'is_food': True,
        'name': effective_name,
        'name_candidates': candidates,
        'category': category,
        'description': description,
        'highlight_tags': tags,
        'ask_hint': ask_hint,
        'notice': notice,
    }


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@throttle_classes([AiDraftThrottle])
def ai_product_draft(request, stall_id):
    form = AiProductDraftInput(data=request.data)
    form.is_valid(raise_exception=True)
    stall = merchant_stall(stall_id, request.user, permission='market.add_product')

    image_url = form.validated_data['image']
    merchant_name = _clean_line(form.validated_data.get('merchant_name', ''), 80)
    merchant_note = _clean_line(form.validated_data.get('merchant_note', ''), 120)
    selected_tags = _unique_clean_list(
        form.validated_data.get('selected_tags', []), max_items=6, max_len=16
    )

    b64_jpeg = _encode_owned_dish_image(stall, image_url)
    existing_categories = list(
        dict.fromkeys(
            c.strip()
            for c in stall.products.values_list('category', flat=True)
            if isinstance(c, str) and c.strip() and c.strip() != '未分类'
        )
    )[:10]
    default_category = existing_categories[0] if existing_categories else (_clean_line(stall.category, 30) or '招牌美味')

    if not settings.DEEPSEEK_API_KEY:
        audit(request.user, 'ai_product_draft_fallback', stall.pk, reason='unconfigured')
        return Response(
            _fallback_draft(
                reason='unconfigured',
                notice='当前未开启 DeepSeek 自动识图，照片已保存；核对菜名与售价后即可直接上架。',
                default_category=default_category,
                merchant_name=merchant_name,
                merchant_note=merchant_note,
                selected_tags=selected_tags,
            )
        )

    if not _ai_slots.acquire(blocking=False):
        audit(request.user, 'ai_product_draft_fallback', stall.pk, reason='busy')
        return Response(
            _fallback_draft(
                reason='busy',
                notice='识图服务当前繁忙，照片已保存；你可以直接填写菜名和定价上架，或稍后点“润色文案”。',
                default_category=default_category,
                merchant_name=merchant_name,
                merchant_note=merchant_note,
                selected_tags=selected_tags,
            )
        )
    try:
        if daily_ai_quota_exhausted():
            audit(request.user, 'ai_product_draft_fallback', stall.pk, reason='quota_exhausted')
            return Response(
                _fallback_draft(
                    reason='quota_exhausted',
                    notice='今日智能识图次数已达上限，照片已保存；请直接填写菜名与定价上架。',
                    default_category=default_category,
                    merchant_name=merchant_name,
                    merchant_note=merchant_note,
                    selected_tags=selected_tags,
                )
            )
        parsed = _request_deepseek_vision(
            b64_jpeg=b64_jpeg,
            stall=stall,
            existing_categories=existing_categories,
            merchant_name=merchant_name,
            merchant_note=merchant_note,
            selected_tags=selected_tags,
        )
        result = _sanitize_ai_result(
            parsed,
            default_category=default_category,
            merchant_name=merchant_name,
            merchant_note=merchant_note,
            selected_tags=selected_tags,
        )
        audit(
            request.user,
            'ai_product_draft_generated',
            stall.pk,
            is_food=result['is_food'],
            refined=bool(merchant_name or merchant_note or selected_tags),
        )
        return Response(result)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError) as exc:
        logger.warning('DeepSeek vision draft failed for stall %s (%s)', stall.pk, type(exc).__name__)
        audit(request.user, 'ai_product_draft_fallback', stall.pk, reason='upstream_error')
        return Response(
            _fallback_draft(
                reason='upstream_error',
                notice='智能识图暂时未能响应，照片已保存；请确认菜名与售价后直接上架。',
                default_category=default_category,
                merchant_name=merchant_name,
                merchant_note=merchant_note,
                selected_tags=selected_tags,
            )
        )
    finally:
        _ai_slots.release()
