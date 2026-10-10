"""Initialize only this isolated MediaCMS database, without logging credentials."""
import os
import re
from pathlib import Path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "cms.settings")


def main():
    for directory in ["/home/mediacms.io/.cache", "/processing-tmp", "/processing-tmp/uploads", "/processing-tmp/nginx"]:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        os.chown(path, 33, 33)
        path.chmod(0o770)
    import django

    django.setup()
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from django.db import transaction
    from rest_framework.authtoken.models import Token
    from files.models import EncodeProfile

    token = os.environ["MEDIACMS_SERVICE_TOKEN"]
    if not re.fullmatch(r"[0-9a-f]{40}", token):
        raise RuntimeError("MEDIACMS_SERVICE_TOKEN must be 40 lowercase hexadecimal characters")
    password = os.environ["ADMIN_PASSWORD"]
    if len(password) < 24:
        raise RuntimeError("ADMIN_PASSWORD must have at least 24 characters")
    call_command("migrate", interactive=False, verbosity=1)
    User = get_user_model()
    with transaction.atomic():
        user, created = User.objects.get_or_create(
            username=os.environ["ADMIN_USER"],
            defaults={"email": os.environ["ADMIN_EMAIL"], "is_staff": True, "is_superuser": True, "is_active": True},
        )
        if created:
            user.set_password(password)
            user.save()
        elif not user.is_superuser:
            raise RuntimeError("Existing service username is not the integration administrator")
        existing = Token.objects.filter(user=user).first()
        if existing and existing.key != token:
            raise RuntimeError("Service token mismatch; explicit credential rotation is required")
        Token.objects.get_or_create(user=user, defaults={"key": token})
    # Migration 0019 creates the preview row even in a new database. Its presence
    # must not skip the fixture profiles needed for ordinary video encodings.
    if not EncodeProfile.objects.exclude(name="preview").exists():
        call_command("loaddata", "fixtures/encoding_profiles.json", verbosity=0)
        call_command("loaddata", "fixtures/categories.json", verbosity=0)
        EncodeProfile.objects.update(active=False)
        selected = EncodeProfile.objects.filter(name__in=["h264-360", "h264-720"])
        if selected.count() != 2:
            raise RuntimeError("Pinned encoding fixtures do not contain the expected preview profiles")
        selected.update(active=True)
        EncodeProfile.objects.filter(name="preview", extension="mp4").update(active=True)
    call_command("collectstatic", interactive=False, verbosity=0)
    print("MediaCMS integration database initialized; credentials are not printed.")


if __name__ == "__main__":
    main()
