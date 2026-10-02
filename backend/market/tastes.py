"""Optional, free per-portion preferences and their immutable order snapshot."""
from collections.abc import Mapping
from rest_framework import serializers
from .errors import BusinessError


def text_value(value, label, *, maximum=20, blank=False):
    if not isinstance(value, str):
        raise serializers.ValidationError(f'{label}必须为文字。')
    value = value.strip()
    if (not value and not blank) or len(value) > maximum:
        raise serializers.ValidationError(f'{label}请填写{0 if blank else 1}至{maximum}个字。')
    return value


class TasteOptionsField(serializers.Field):
    def to_internal_value(self, value):
        if not isinstance(value, list) or len(value) > 3:
            raise serializers.ValidationError('最多设置3组免费口味选项。')
        result, names = [], set()
        for group in value:
            if not isinstance(group, Mapping) or set(group) != {'name', 'choices'}:
                raise serializers.ValidationError('每组口味需要名称和可选项，不支持加价字段。')
            name = text_value(group['name'], '口味名称')
            if name in names: raise serializers.ValidationError('口味组名称不能重复。')
            names.add(name)
            if not isinstance(group['choices'], list) or not 1 <= len(group['choices']) <= 8:
                raise serializers.ValidationError('每组口味需要1至8个选项。')
            choices = [text_value(choice, '口味选项') for choice in group['choices']]
            if len(set(choices)) != len(choices): raise serializers.ValidationError('同一组内选项不能重复。')
            result.append({'name': name, 'choices': choices})
        return result

    def to_representation(self, value): return value


class PortionsField(serializers.Field):
    def to_internal_value(self, value):
        if not isinstance(value, list) or not 1 <= len(value) <= 99:
            raise serializers.ValidationError('请为每份餐点填写一组口味，最多99份。')
        result = []
        for portion in value:
            if not isinstance(portion, Mapping) or set(portion) - {'options', 'note'}:
                raise serializers.ValidationError('每份只支持免费口味和备注。')
            options = portion.get('options', {})
            if not isinstance(options, Mapping) or len(options) > 3:
                raise serializers.ValidationError('每份最多选择3组口味。')
            selected = {}
            for key, choice in options.items():
                name = text_value(key, '口味名称')
                if name in selected: raise serializers.ValidationError('每份的口味名称不能重复。')
                selected[name] = text_value(choice, '口味选项')
            result.append({'options': selected,
                'note': text_value(portion.get('note', ''), '单份备注', maximum=100, blank=True)})
        return result

    def to_representation(self, value): return value


def canonical_item(item):
    """Empty preferences hash exactly like the pre-preferences request."""
    value = dict(item)
    portions = value.get('portions')
    if portions and not any(portion.get('options') or portion.get('note') for portion in portions):
        value.pop('portions', None)
    return value


def validate_portions(product, item):
    portions = item.get('portions', [])
    available = {group['name']: set(group['choices']) for group in product.taste_options}
    for portion in portions:
        for name, choice in portion['options'].items():
            if name not in available or choice not in available[name]:
                raise BusinessError('商品口味选项已更新，请重新选择后提交。', 'tastes_changed', product_id=product.pk)
    return portions
