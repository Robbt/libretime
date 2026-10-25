from django.urls import path
from rest_framework import routers

from .views import UploadView
from .views.file import FileViewSet
from .views.library import LibraryViewSet

router = routers.DefaultRouter(trailing_slash=False)
router.register("files", FileViewSet)
router.register("libraries", LibraryViewSet)

# A file upload is not a row create: it moves bytes into shared storage and asks
# the analyzer to read them, so it has its own endpoint rather than a multipart
# variant of /files.
urls = [
    path("upload", UploadView.as_view()),
    *router.urls,
]
