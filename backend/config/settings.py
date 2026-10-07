import os
from pathlib import Path
from urllib.parse import urlparse, unquote
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
ENV = os.getenv('DJANGO_ENV', 'development')
PRODUCTION = ENV == 'production'
DEBUG = not PRODUCTION
DEMO_MODE = os.getenv('DEMO_MODE', 'true' if not PRODUCTION else 'false').lower() == 'true'
SERVICES_SIMULATION_ENABLED = os.getenv('SERVICES_SIMULATION_ENABLED', 'false').lower() == 'true'
if SERVICES_SIMULATION_ENABLED and (PRODUCTION or not DEMO_MODE):
    raise ImproperlyConfigured('SERVICES_SIMULATION_ENABLED requires a non-production DEMO_MODE environment.')
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'local-development-only-change-before-deploy-yhdt')
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,testserver').split(',')
CSRF_TRUSTED_ORIGINS = [x for x in os.getenv('CSRF_TRUSTED_ORIGINS', '' if PRODUCTION else
    'http://localhost:5173,http://127.0.0.1:5173,http://localhost:5183,http://127.0.0.1:5183').split(',') if x]
BRAND_NAME = os.getenv('BRAND_NAME', '烟火地图')
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')
if PUBLIC_BASE_URL:
    public_url = urlparse(PUBLIC_BASE_URL)
    if (public_url.scheme not in (('https',) if PRODUCTION else ('http', 'https'))
            or not public_url.netloc or public_url.username or public_url.password
            or public_url.query or public_url.fragment or public_url.path):
        raise ImproperlyConfigured('PUBLIC_BASE_URL must be a public origin without credentials, path, query or fragment.')
AMAP_KEY = os.getenv('AMAP_KEY', '')
AMAP_SECURITY_CODE = os.getenv('AMAP_SECURITY_CODE', '')
AMAP_PROXY = '/api/v1/amap-proxy/_AMapService'
# Payment credentials stay in an operator-owned server file, never in frontend settings.
WECHAT_PAY_ENABLED = os.getenv('WECHAT_PAY_ENABLED', 'false').lower() == 'true'
WECHAT_PAY_CONFIG_FILE = os.getenv('WECHAT_PAY_CONFIG_FILE', '')
WECHAT_PAY_PUBLIC_ORIGIN = os.getenv('WECHAT_PAY_PUBLIC_ORIGIN', '')
# Only enable when the reverse proxy overwrites X-Real-IP with the actual peer address.
WECHAT_PAY_TRUST_PROXY_CLIENT_IP = os.getenv('WECHAT_PAY_TRUST_PROXY_CLIENT_IP', 'false').lower() == 'true'
# Enable only when the backend is isolated behind a proxy overwriting X-Real-IP.
AUTH_TRUST_PROXY_CLIENT_IP = os.getenv('AUTH_TRUST_PROXY_CLIENT_IP', 'false').lower() == 'true'
AUTH_FAILURE_WINDOW_SECONDS = int(os.getenv('AUTH_FAILURE_WINDOW_SECONDS', '900'))
AUTH_FAILURE_ACCOUNT_LIMIT = int(os.getenv('AUTH_FAILURE_ACCOUNT_LIMIT', '10'))
AUTH_FAILURE_IP_LIMIT = int(os.getenv('AUTH_FAILURE_IP_LIMIT', '60'))
CHECKOUT_MAX_ACTIVE_PER_STALL = int(os.getenv('CHECKOUT_MAX_ACTIVE_PER_STALL', '1'))
CHECKOUT_MAX_ACTIVE_TOTAL = int(os.getenv('CHECKOUT_MAX_ACTIVE_TOTAL', '3'))
CHECKOUT_MAX_PORTIONS = int(os.getenv('CHECKOUT_MAX_PORTIONS', '10'))
CHECKOUT_PER_MINUTE = int(os.getenv('CHECKOUT_PER_MINUTE', '6'))
CHECKOUT_PER_HOUR = int(os.getenv('CHECKOUT_PER_HOUR', '30'))
PAYMENT_QUERY_INTERVAL_SECONDS = 5
PAYMENT_QUERY_FAILURE_DELAYS = (10, 20, 30, 60)
PAYMENT_NOTIFICATION_MAX_ATTEMPTS = 10
if min(CHECKOUT_MAX_ACTIVE_PER_STALL, CHECKOUT_MAX_ACTIVE_TOTAL, CHECKOUT_MAX_PORTIONS,
       CHECKOUT_PER_MINUTE, CHECKOUT_PER_HOUR) < 1:
    raise ImproperlyConfigured('Checkout limits must be positive.')
if min(AUTH_FAILURE_WINDOW_SECONDS, AUTH_FAILURE_ACCOUNT_LIMIT, AUTH_FAILURE_IP_LIMIT) < 1:
    raise ImproperlyConfigured('Authentication failure limits must be positive.')
if PRODUCTION:
    if SECRET_KEY.startswith('local-') or len(SECRET_KEY) < 40:
        raise ImproperlyConfigured('Production requires a random DJANGO_SECRET_KEY with at least 40 characters.')
    if DEMO_MODE:
        raise ImproperlyConfigured('DEMO_MODE must be false in production.')
    if not os.getenv('DJANGO_ALLOWED_HOSTS') or '*' in ALLOWED_HOSTS:
        raise ImproperlyConfigured('Production requires explicit DJANGO_ALLOWED_HOSTS.')

INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'rest_framework', 'market',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware',
    'market.session_boundary.SessionActorMiddleware',
    'market.auth_limits.AdminLoginLimitStatusMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': ['django.template.context_processors.request',
      'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
database_url = os.getenv('DATABASE_URL', '')
if database_url:
    parsed = urlparse(database_url)
    if parsed.scheme not in ('postgres', 'postgresql'):
        raise ImproperlyConfigured('DATABASE_URL must use PostgreSQL.')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': unquote(parsed.path.lstrip('/')),
      'USER': unquote(parsed.username or ''), 'PASSWORD': unquote(parsed.password or ''),
      'HOST': parsed.hostname, 'PORT': parsed.port or 5432, 'CONN_MAX_AGE': 60,
      'OPTIONS': {'sslmode': os.getenv('DB_SSLMODE', 'require' if PRODUCTION else 'prefer')}}}
else:
    if PRODUCTION:
        raise ImproperlyConfigured('Production requires DATABASE_URL for PostgreSQL/PostGIS.')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {'timeout': 20, 'transaction_mode': 'IMMEDIATE'}}}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
# Throttle counters must survive restarts and be shared by every worker process.
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.db.DatabaseCache', 'LOCATION': 'yanhuo_cache'}} \
    if PRODUCTION or os.getenv('DJANGO_CACHE', '') == 'database' else \
    {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
    'DEFAULT_PARSER_CLASSES': ['market.request_parsers.BoundedJSONParser',
        'market.request_parsers.BoundedFormParser', 'rest_framework.parsers.MultiPartParser'],
    'DEFAULT_THROTTLE_CLASSES': ['market.auth_limits.TrustedAnonRateThrottle', 'market.auth_limits.TrustedUserRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {'anon': '1200/hour', 'user': '6000/hour', 'auth': '300/hour' if DEMO_MODE and not PRODUCTION else '30/hour'},
    'EXCEPTION_HANDLER': 'market.errors.exception_handler',
}
LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
IMAGE_UPLOAD_MAX_BYTES = 5 * 1024 * 1024
FILE_UPLOAD_HANDLERS = ['market.upload_handlers.BoundedImageUploadHandler',
    'django.core.files.uploadhandler.MemoryFileUploadHandler', 'django.core.files.uploadhandler.TemporaryFileUploadHandler']
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
APPEND_SLASH = False
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = PRODUCTION
CSRF_COOKIE_SECURE = PRODUCTION
SECURE_SSL_REDIRECT = PRODUCTION
SECURE_HSTS_SECONDS = 31536000 if PRODUCTION else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = PRODUCTION
SECURE_HSTS_PRELOAD = PRODUCTION
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
# Enable only behind a trusted reverse proxy that overwrites this header.
if os.getenv('TRUST_PROXY', 'false').lower() == 'true':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
LOGGING = {'version': 1, 'disable_existing_loggers': False,
    'handlers': {'console': {'class': 'logging.StreamHandler'}},
    'loggers': {'market': {'handlers': ['console'], 'level': 'INFO'},
                'django.request': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False}}}
