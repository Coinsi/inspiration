"""Private, API-only MediaCMS settings; secrets come from the runtime environment."""
import os
import sys as _sys

FRONTEND_HOST = os.environ.get("FRONTEND_HOST", "http://127.0.0.1:18080")
PORTAL_NAME = "Inspiration media processing"
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "web"]
DEBUG = False
ROOT_URLCONF = "mediacms_bridge.urls"
INSPIRATION_SERVICE_TOKEN = os.environ["MEDIACMS_SERVICE_TOKEN"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["POSTGRES_NAME"],
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
    }
}
REDIS_LOCATION = os.environ["REDIS_LOCATION"]
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_LOCATION,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}
BROKER_URL = REDIS_LOCATION
CELERY_RESULT_BACKEND = REDIS_LOCATION
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERYD_PREFETCH_MULTIPLIER = 1
CELERY_TASK_TRACK_STARTED = True
# local_settings is imported near the end of cms.settings. Preserve any pinned
# upstream imports while adding our explicit task module; autodiscovery remains.
_upstream_imports = getattr(_sys.modules.get("cms.settings"), "CELERY_IMPORTS", ())
if isinstance(_upstream_imports, str):
    _upstream_imports = (_upstream_imports,)
CELERY_IMPORTS = tuple(dict.fromkeys((*_upstream_imports, "mediacms_bridge.tasks")))
PORTAL_WORKFLOW = "private"
USERS_CAN_SELF_REGISTER = False
REGISTER_ALLOWED = False
ALLOW_ANONYMOUS_USER_LISTING = False
ALLOW_ANONYMOUS_ACTIONS = []
NUMBER_OF_MEDIA_USER_CAN_UPLOAD = 100000
UPLOAD_MAX_SIZE = 100 * 1024 * 1024 * 1024
INSPIRATION_MAX_UPLOAD_BYTES = UPLOAD_MAX_SIZE
INSPIRATION_DISK_RESERVE_BYTES = int(os.environ.get("MEDIACMS_DISK_RESERVE_BYTES", 10 * 1024**3))
FILE_UPLOAD_HANDLERS = ["mediacms_bridge.uploadhandler.CapacityUploadHandler"]
FILE_UPLOAD_TEMP_DIR = "/processing-tmp/uploads"
TEMP_DIRECTORY = "/processing-tmp"
MINIMUM_RESOLUTIONS_TO_ENCODE = [360]
FFMPEG_DEFAULT_PRESET = "fast"
USE_WHISPER_TRANSCRIBE = os.environ.get("USE_WHISPER_TRANSCRIBE", "false").lower() == "true"
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
# Inspiration imports protected MP4 derivatives; HLS is not consumed yet.
# The upstream task checks this executable before doing any HLS processing.
MP4HLS_COMMAND = ""
USER_CAN_TRANSCRIBE_VIDEO = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
