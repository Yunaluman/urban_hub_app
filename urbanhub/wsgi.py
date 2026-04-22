import os
import sys

if sys.platform == 'win32':
    os.environ.setdefault('PGCLIENTENCODING', 'UTF8')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'urbanhub.settings')

from django.core.wsgi import get_wsgi_application  # noqa: E402

application = get_wsgi_application()
