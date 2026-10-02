from datetime import timedelta
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from market.models import Area, BusinessSession, MerchantProfile, Product, SiteConfiguration, Stall, StallLocation


CATALOG = [
    ('老李烤冷面', '小吃', '一张铁板，十年手艺。酸甜酱汁遇上现煎鸡蛋，是下课后熟悉的那一口。', 'noodles', 0, '南门夜市 · 入口左侧第 3 个摊位', 31.23051, 121.47343, 8,
      [('招牌烤冷面', '鸡蛋、香肠、洋葱，酸甜微辣', 800), ('双蛋烤冷面', '两颗鸡蛋，满满蛋香', 1000), ('芝士烤冷面', '拉丝芝士，浓郁满足', 1200), ('豪华全家福', '鸡蛋、培根、芝士的快乐组合', 1600)]),
    ('阿叔炭火烧烤', '烧烤', '炭火慢烤，撒一把孜然。和朋友把晚风里的快乐串起来。', 'skewers', 0, '南门夜市 · 灯笼巷中段', 31.23022, 121.47463, 12,
      [('秘制羊肉串 · 3 串', '现切羊肉，炭火现烤', 1500), ('蜜汁鸡翅 · 2 只', '外皮微焦，鲜嫩多汁', 1200), ('炭烤玉米', '甜玉米裹上独家香料', 600), ('烤茄子', '蒜蓉铺满，香气扑鼻', 800)]),
    ('阿姨手作煎饼', '小吃', '杂粮面糊摊得薄薄，脆饼和蔬菜卷得满满。今天也要好好吃饭。', 'pancake', 1, '东门生活区 · 便利店旁', 31.23321, 121.47572, 5,
      [('杂粮煎饼', '鸡蛋、薄脆、生菜，经典不出错', 700), ('里脊肉煎饼', '鲜嫩里脊，元气满满', 1000), ('双蛋煎饼', '爱吃鸡蛋的同学看这里', 900), ('火腿煎饼', '厚切火腿，咸香满足', 900)]),
    ('一碗小馄饨', '正餐', '热汤升起的白雾里，藏着属于校园的安心。鲜肉现包，汤底每日熬。', 'dumplings', 1, '东门生活区 · 梧桐路 6 号', 31.23384, 121.47650, 10,
      [('鲜肉小馄饨', '现包鲜肉，紫菜虾皮清汤', 1200), ('荠菜鲜肉馄饨', '荠菜清香，鲜肉饱满', 1400), ('红油抄手', '香而不燥，微麻微辣', 1500), ('鲜虾小馄饨', '整颗鲜虾，弹嫩可口', 1800)]),
    ('晚风柠檬茶', '饮品', '手打鲜柠檬，配一杯好茶。把燥热留给夏天，把清爽交给你。', 'tea', 0, '南门夜市 · 榕树下', 31.22965, 121.47388, 4,
      [('招牌手打柠檬茶', '香水柠檬 · 茉莉茶底', 900), ('鸭屎香柠檬茶', '浓郁茶香，清新回甘', 1200), ('冰镇酸梅汤', '乌梅山楂熬煮，解腻好搭档', 600), ('桂花乌龙', '清甜桂花，乌龙回甘', 1000)]),
    ('小陈铁板饭', '正餐', '铁板滋滋响，饭香飘过一整条街。现炒现做，让每一口都热乎。', 'burger', 2, '西门运动场 · 校外便民点', 31.23101, 121.46982, 10,
      [('黑椒鸡排饭', '香嫩鸡排配黑椒汁', 1600), ('照烧鸡腿饭', '浓郁照烧汁，软嫩鸡腿肉', 1800), ('香菇卤肉饭', '慢炖卤肉，配一颗卤蛋', 1500), ('时蔬蛋炒饭', '粒粒分明，镬气十足', 1000)]),
    ('周记脆皮豆腐', '小吃', '外壳酥脆，内里软嫩。一勺蒜香酱，就是记忆里的街头味道。', 'tofu', 0, '南门夜市 · 中央休息区旁', 31.22989, 121.47505, 6,
      [('招牌脆皮豆腐', '蒜香酱汁，外酥里嫩', 800), ('香辣豆腐', '鲜辣过瘾，越吃越香', 900), ('酱香土豆', '土豆煎至金黄，酱香浓郁', 700)]),
    ('甜甜糯米铺', '甜品', '软糯手作，每天新鲜。用一点甜，给忙碌的一天画个圆满的句号。', 'dessert', 2, '西门运动场 · 梧桐树旁', 31.23210, 121.47031, 3,
      [('红糖糍粑', '现炸糍粑，淋上浓浓红糖', 800), ('桂花酒酿圆子', '软糯小圆子，清甜酒酿香', 1000), ('芒果糯米饭', '香甜芒果与椰香糯米', 1600), ('椰汁西米露', '椰香浓郁，冰爽顺滑', 900)]),
]

# Licensed photographs illustrate the dish category, never a real merchant's food.
# Exact stall/product names keep existing demo records stable across asset refreshes.
DEMO_STALL_IMAGES = {
    '老李烤冷面': 'food-cold-noodles.jpg',
    '阿叔炭火烧烤': 'food-skewers.jpg',
    '阿姨手作煎饼': 'food-jianbing.jpg',
    '一碗小馄饨': 'food-wontons.jpg',
    '晚风柠檬茶': 'food-dark-lemon-tea.jpg',
    '小陈铁板饭': 'food-chicken-rice.jpg',
    '周记脆皮豆腐': 'food-crispy-tofu.jpg',
    '甜甜糯米铺': 'food-ciba.jpg',
}

DEMO_PRODUCT_IMAGES = {
    '老李烤冷面': ['food-cold-noodles.jpg', 'food-egg-noodles.jpg', 'food-noodles-griddle.jpg', 'food-noodles.jpg'],
    '阿叔炭火烧烤': ['food-skewers.jpg', 'food-chicken-wings.jpg', 'food-corn.jpg', 'food-eggplant.jpg'],
    '阿姨手作煎饼': ['food-jianbing.jpg', 'food-pancake.jpg', 'food-jianbing-making.jpg', 'food-jianbing.jpg'],
    '一碗小馄饨': ['food-wontons.jpg', 'food-wonton-soup.jpg', 'food-red-oil-wontons.jpg', 'food-wontons.jpg'],
    '晚风柠檬茶': ['food-lemon-tea.jpg', 'food-dark-lemon-tea.jpg', 'food-plum-juice.jpg', 'food-oolong.jpg'],
    '小陈铁板饭': ['food-chicken-cutlet.jpg', 'food-chicken-rice.jpg', 'food-pork-rice.jpg', 'food-fried-rice.jpg'],
    '周记脆皮豆腐': ['food-crispy-tofu.jpg', 'food-marinated-tofu.jpg', 'food-potato.jpg'],
    '甜甜糯米铺': ['food-ciba.jpg', 'food-tangyuan.jpg', 'food-mango-rice.jpg', 'food-sago.jpg'],
}


class Command(BaseCommand):
    help = 'Create explicitly marked development demo data. Never permitted in production.'
    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEMO_MODE or settings.PRODUCTION:
            raise CommandError('Demo seeding is disabled outside DEMO_MODE development.')
        SiteConfiguration.objects.get_or_create(pk=1)
        for username, name, staff in [('student', '小宇同学', False), ('vendor', '老李摊主', False), ('admin', '平台运营', True)]:
            user, created = User.objects.get_or_create(username=username, defaults={'first_name': name, 'is_staff': staff, 'is_superuser': staff})
            if created:
                user.set_password('demo12345')
                user.save()
        vendor = User.objects.get(username='vendor')
        merchant, _ = MerchantProfile.objects.get_or_create(user=vendor, defaults={
            'business_name': '校园烟火示例商户（演示）', 'contact_phone': '', 'is_verified': True,
            'license_number': 'DEMO-ONLY', 'qualification_note': '演示环境：商户、经营资质与地点均为示例，不能用于实际交易。正式经营资料须由运营核验后公示。'})
        areas = []
        for name, subtitle, lat, lng in [('示例大学 · 南门', '南门夜市与周边', 31.2304, 121.4740),
            ('示例大学 · 东门', '生活区与梧桐路', 31.2335, 121.4760), ('示例大学 · 西门', '运动场与便民点', 31.2315, 121.4700)]:
            area, _ = Area.objects.get_or_create(name=name, is_demo=True, defaults={'subtitle': subtitle, 'latitude': lat, 'longitude': lng})
            areas.append(area)
        for index, (name, category, description, image, area_index, address, lat, lng, prep, products) in enumerate(CATALOG):
            stall, created = Stall.objects.get_or_create(name=name, is_demo=True, defaults={
                'merchant': merchant, 'area': areas[area_index], 'category': category, 'description': description,
                'image': f'/images/{DEMO_STALL_IMAGES[name]}', 'prep_minutes': prep, 'transaction_enabled': index not in (6,)})
            if not created: continue
            StallLocation.objects.create(stall=stall, address=address, latitude=lat, longitude=lng)
            status = 'paused' if index == 5 else 'closed' if index == 7 else 'open'
            session = BusinessSession.objects.create(stall=stall, status=status,
                last_confirmed_at=timezone.now()-timedelta(minutes=(index+1)*2), closes_at=timezone.now()+timedelta(hours=6))
            stall.current_session = session
            stall.save(update_fields=['current_session'])
            for j, (product_name, product_description, price) in enumerate(products):
                Product.objects.create(stall=stall, name=product_name, description=product_description,
                    image=f'/images/{DEMO_PRODUCT_IMAGES[name][j]}', price_cents=price, stock=0 if (index == 1 and j == 3) else 30+j*5)
        self.stdout.write(self.style.SUCCESS('Demo ready: 8 stalls, 31 products. student / vendor / admin: demo12345'))
        self.stdout.write('Seeding is additive only; rerunning does not reset orders, passwords, stock, or location freshness.')
