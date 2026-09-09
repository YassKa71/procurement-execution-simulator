import os
os.environ.setdefault("DJANGO_SECRET_KEY", "test-only-secret-not-for-deployment")
from .settings import *
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
STORAGES = {"default": {"BACKEND": "django.core.files.storage.InMemoryStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
SECURE_SSL_REDIRECT = False
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
