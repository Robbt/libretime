from rest_framework.test import APITestCase

from ....permission_constants import GROUPS
from ...models import Role, User


class TestUserManager(APITestCase):
    def test_create_user(self):
        user = User.objects.create_user(
            role=Role.HOST,
            username="test",
            password="test",
            email="test@example.com",
            first_name="test",
            last_name="user",
        )
        db_user = User.objects.get(pk=user.pk)
        self.assertEqual(db_user.username, user.username)

    def test_create_superuser(self):
        user = User.objects.create_superuser(
            username="test",
            password="test",
            email="test@example.com",
            first_name="test",
            last_name="user",
        )
        db_user = User.objects.get(pk=user.pk)
        self.assertEqual(db_user.username, user.username)
        self.assertEqual(db_user.role, Role.ADMIN)


class TestUser(APITestCase):
    def test_guest_get_group_perms(self):
        user = User.objects.create_user(
            role=Role.GUEST,
            username="test",
            password="test",
            email="test@example.com",
            first_name="test",
            last_name="user",
        )

        permissions = user.get_group_permissions()
        # APIRoot permission hardcoded in the check as it isn't a Permission object
        str_perms = [p.codename for p in permissions] + ["view_apiroot"]
        self.assertCountEqual(str_perms, GROUPS[Role.GUEST.value])


class TestRoles(APITestCase):
    """
    The four roles the legacy application chains - guest, host, program manager,
    admin - plus the superadmin it inherits from the admin, and what happens to a
    row whose type is none of these.
    """

    @staticmethod
    def _user(role, username) -> User:
        return User.objects.create_user(
            role=role,
            username=username,
            password="test",
            email=f"{username}@example.org",
            first_name="Role",
            last_name="Test",
        )

    def test_a_superadmin_is_an_admin(self):
        """
        The legacy role list is G < H < P < A < S, and 'S' only ever means "an
        admin who also reaches the pages an admin reaches".

        Before it was known to this code, GROUPS['S'] raised KeyError inside
        has_perm, so every endpoint such a user touched answered 500 - including
        for an installation whose only admin was the one the installer creates.
        """
        user = self._user(Role.SUPERADMIN, "role-superadmin")

        self.assertTrue(user.is_superuser())
        self.assertTrue(user.has_perm("change_show"))
        self.assertTrue(user.has_perm("delete_user"))

    def test_an_admin_is_a_superuser_and_a_host_is_not(self):
        self.assertTrue(self._user(Role.ADMIN, "role-admin").is_superuser())
        self.assertFalse(self._user(Role.HOST, "role-host").is_superuser())
        self.assertFalse(self._user(Role.MANAGER, "role-manager").is_superuser())

    def test_an_unknown_role_grants_nothing(self):
        """
        ``cc_subjs.type`` is a plain column, so a row can say something this code
        has never heard of - written by a future release, or by an operator.

        A role with no entry must grant no permission rather than every one: the
        group permissions are looked up with a filter built from that role's list,
        and a filter built from nothing constrains nothing.
        """
        user = self._user("Z", "role-unknown")

        self.assertFalse(user.is_superuser())
        self.assertEqual(user.get_group_permissions(), [])
        self.assertFalse(user.has_perm("change_show"))
        self.assertFalse(user.has_perm("view_file"))
        self.assertFalse(user.has_perm("delete_user"))

    def test_every_role_is_accounted_for(self):
        """
        The invariant whose absence made a superadmin crash the api: a role is
        either a superuser, and needs no list, or it has one.
        """
        for role in Role:
            with self.subTest(role=role.value):
                user = self._user(role, f"role-{role.value}")

                if user.is_superuser():
                    continue

                self.assertIn(role.value, GROUPS)
                self.assertTrue(user.has_perm("view_file"))
