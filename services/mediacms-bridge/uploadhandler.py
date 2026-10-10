"""Enforce bounded upload bytes and free space before writing each file chunk."""

from django.conf import settings
from django.core.files.uploadhandler import TemporaryFileUploadHandler
from rest_framework.exceptions import APIException

from .capacity import DiskCapacityError, check_upload_space


class UploadTooLarge(APIException):
    status_code = 413
    default_detail = "Source exceeds configured upload size limit"


class StorageUnavailable(APIException):
    status_code = 507
    default_detail = "Insufficient media storage space; free space and retry the upload"


class CapacityUploadHandler(TemporaryFileUploadHandler):
    chunk_size = 1024 * 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.received_bytes = 0

    def _check_space(self, size=0):
        try:
            check_upload_space(settings, additional_bytes=size)
        except DiskCapacityError as exc:
            self.upload_interrupted()
            raise StorageUnavailable(str(exc)) from exc

    def handle_raw_input(self, *args, **kwargs):
        self._check_space()

    def new_file(self, *args, **kwargs):
        self._check_space()
        try:
            super().new_file(*args, **kwargs)
        except OSError as exc:
            self.upload_interrupted()
            raise StorageUnavailable(
                "Media upload could not be stored; check storage and retry"
            ) from exc

    def receive_data_chunk(self, raw_data, start):
        limit = getattr(settings, "INSPIRATION_MAX_UPLOAD_BYTES", 100 * 1024**3)
        if self.received_bytes + len(raw_data) > limit:
            self.upload_interrupted()
            raise UploadTooLarge()
        self._check_space(len(raw_data))
        try:
            result = super().receive_data_chunk(raw_data, start)
        except OSError as exc:
            self.upload_interrupted()
            raise StorageUnavailable(
                "Media upload could not be stored; check storage and retry"
            ) from exc
        self.received_bytes += len(raw_data)
        return result
