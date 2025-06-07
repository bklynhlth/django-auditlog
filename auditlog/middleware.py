from django.contrib.auth import get_user_model
from auditlog.context import set_source
from django.utils.functional import SimpleLazyObject
from django.conf import settings

from typing import Optional

class AuditlogMiddleware:
    """
    Middleware to couple the request's user to log items. This is accomplished by currying the
    signal receiver with the user from the request (or None if the user is not authenticated).
    """

    def __init__(self, get_response=None):
        self.get_response = get_response
    
    @staticmethod
    def _get_remote_addr(request):
        if settings.AUDITLOG_DISABLE_REMOTE_ADDR:
            return None

        # In case there is no proxy, return the original address
        if not request.headers.get("X-Forwarded-For"):
            return request.META.get("REMOTE_ADDR")

        # In case of proxy, set 'original' address
        remote_addr: str = request.headers.get("X-Forwarded-For").split(",")[0]

        # Remove port number from remote_addr
        if "." in remote_addr and ":" in remote_addr:  # IPv4 with port (`x.x.x.x:x`)
            remote_addr = remote_addr.split(":")[0]
        elif "[" in remote_addr:  # IPv6 with port (`[:::]:x`)
            remote_addr = remote_addr[1:].split("]")[0]

        return remote_addr
    
    @staticmethod
    def _get_remote_port(request) -> Optional[int]:
        remote_port = request.headers.get("X-Forwarded-Port", "")

        try:
            remote_port = int(remote_port)
        except ValueError:
            remote_port = None

        return remote_port
    
    def __call__(self, request):
        remote_addr = self._get_remote_addr(request)
        remote_port = self._get_remote_port(request)

        def get_user():
            user = getattr(request, "user", None)
            if callable(user):
                user = user()
            return user if isinstance(user, get_user_model()) and user.is_authenticated else None

        with set_source(source=SimpleLazyObject(get_user), remote_addr=remote_addr, remote_port=remote_port):
            return self.get_response(request)