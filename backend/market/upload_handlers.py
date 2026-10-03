"""Stop oversized file streams before Django's temporary-file handler writes them."""
from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload


class BoundedImageUploadHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.received = 0

    def receive_data_chunk(self, raw_data, start):
        # Bound the total of all uploaded files, including unexpected extra files.
        self.received += len(raw_data)
        if self.received > settings.IMAGE_UPLOAD_MAX_BYTES:
            self.request._image_upload_error = 'image_too_large'
            raise StopUpload(connection_reset=True)
        return raw_data

    def file_complete(self, file_size):
        return None
