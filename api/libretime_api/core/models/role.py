from django.db import models


class Role(models.TextChoices):
    GUEST = "G", "Guest"
    HOST = "H", "Host"
    MANAGER = "P", "Manager"
    ADMIN = "A", "Admin"
    # The legacy application chains its roles G < H < P < A < S, so a superadmin
    # is an admin with more pages to reach. It is not a fifth kind of user: the
    # installer creates one, and nothing else gives it a different right.
    SUPERADMIN = "S", "Superadmin"
