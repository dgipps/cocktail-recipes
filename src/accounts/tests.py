from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class SignupViewTests(TestCase):
    def test_get_renders_form(self):
        response = self.client.get(reverse("signup"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/signup.html")
        self.assertIn("form", response.context)

    def test_post_valid_creates_user_and_redirects(self):
        response = self.client.post(reverse("signup"), {
            "username": "newuser",
            "password1": "TestPass123!",
            "password2": "TestPass123!",
        })
        self.assertRedirects(response, reverse("recipe_list"))
        self.assertTrue(User.objects.filter(username="newuser").exists())

    def test_post_valid_logs_user_in(self):
        self.client.post(reverse("signup"), {
            "username": "newuser",
            "password1": "TestPass123!",
            "password2": "TestPass123!",
        })
        response = self.client.get(reverse("recipe_list"))
        self.assertEqual(response.status_code, 200)

    def test_post_valid_user_has_no_elevated_privileges(self):
        self.client.post(reverse("signup"), {
            "username": "newuser",
            "password1": "TestPass123!",
            "password2": "TestPass123!",
        })
        user = User.objects.get(username="newuser")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.is_active)

    def test_post_mismatched_passwords_rerenders(self):
        response = self.client.post(reverse("signup"), {
            "username": "newuser",
            "password1": "TestPass123!",
            "password2": "Different123!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/signup.html")
        self.assertFalse(User.objects.filter(username="newuser").exists())

    def test_post_duplicate_username_rerenders(self):
        User.objects.create_user(username="existing", password="TestPass123!")
        response = self.client.post(reverse("signup"), {
            "username": "existing",
            "password1": "TestPass123!",
            "password2": "TestPass123!",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/signup.html")

    def test_authenticated_user_redirected(self):
        User.objects.create_user(username="existing", password="TestPass123!")
        self.client.login(username="existing", password="TestPass123!")
        response = self.client.get(reverse("signup"))
        self.assertRedirects(response, reverse("recipe_list"))


class LoginViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="TestPass123!")

    def test_get_renders_form(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_post_valid_credentials_redirects(self):
        response = self.client.post(reverse("login"), {
            "username": "testuser",
            "password": "TestPass123!",
        })
        self.assertRedirects(response, "/recipes/")

    def test_post_invalid_credentials_rerenders(self):
        response = self.client.post(reverse("login"), {
            "username": "testuser",
            "password": "wrongpassword",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "accounts/login.html")

    def test_next_parameter_honoured(self):
        response = self.client.post(
            reverse("login") + "?next=/recipes/",
            {"username": "testuser", "password": "TestPass123!"},
        )
        self.assertRedirects(response, "/recipes/")


class LogoutViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="TestPass123!")
        self.client.login(username="testuser", password="TestPass123!")

    def test_post_logs_out_and_redirects(self):
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, "/accounts/login/")

    def test_after_logout_protected_view_redirects_to_login(self):
        self.client.post(reverse("logout"))
        response = self.client.get(reverse("recipe_list"))
        self.assertRedirects(response, "/accounts/login/?next=/recipes/")


class LoginRequiredRedirectTests(TestCase):
    def test_unauthenticated_recipe_list_redirects_to_login(self):
        response = self.client.get(reverse("recipe_list"))
        self.assertRedirects(response, "/accounts/login/?next=/recipes/")
