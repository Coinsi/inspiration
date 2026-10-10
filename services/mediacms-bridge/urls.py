"""Mount this directory as mediacms_bridge; no database schema is added."""

from django.urls import include, path

from .views import EncodeView, ManifestView, OutputView, TranscriptionView, UploadView

urlpatterns = [
    path("api/inspiration/v1/media/", UploadView.as_view()),
    path("api/inspiration/v1/media/<str:token>/manifest", ManifestView.as_view()),
    path("api/inspiration/v1/media/<str:token>/encode", EncodeView.as_view()),
    path("api/inspiration/v1/media/<str:token>/files/<str:output_key>", OutputView.as_view()),
    path("api/inspiration/v1/media/<str:token>/transcription", TranscriptionView.as_view()),
    path("", include("cms.urls")),
]
