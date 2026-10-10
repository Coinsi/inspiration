"""Soft free-space guards for the private processing service, not disk quotas."""

import shutil

DEFAULT_RESERVE_BYTES = 10 * 1024**3


class DiskCapacityError(RuntimeError):
    pass


def require_space(settings, paths, *, additional_bytes=0):
    reserve = getattr(settings, "INSPIRATION_DISK_RESERVE_BYTES", DEFAULT_RESERVE_BYTES)
    if type(reserve) is not int or reserve < 0 or additional_bytes < 0:
        raise DiskCapacityError("Media storage capacity settings are invalid")
    required = reserve + additional_bytes
    for path in paths:
        if not path:
            raise DiskCapacityError(
                "Media storage capacity cannot be verified; retry after checking storage"
            )
        try:
            free = shutil.disk_usage(path).free
        except OSError as exc:
            raise DiskCapacityError(
                "Media storage capacity cannot be verified; retry after checking storage"
            ) from exc
        if free < required:
            raise DiskCapacityError(
                "Insufficient media storage space; free space and retry this stage"
            )


def check_processing_space(settings):
    require_space(settings, (settings.TEMP_DIRECTORY, settings.MEDIA_ROOT))


def check_upload_space(settings, *, additional_bytes=0):
    require_space(
        settings,
        (settings.FILE_UPLOAD_TEMP_DIR, settings.MEDIA_ROOT),
        additional_bytes=additional_bytes,
    )
