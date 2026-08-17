"""
Spark Dating — нагрузочное тестирование (Locust)

Запуск:
  locust --host=https://spark-dating.club

Открыть: http://localhost:8089
Задать: 50 users, spawn rate 5, запустить.

Для headless (CI):
  locust --host=https://spark-dating.club --headless -u 50 -r 5 --run-time 60s
"""
import random
import string
import time

from locust import HttpUser, SequentialTaskSet, TaskSet, between, task


def _random_email() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    return f"load_{suffix}@test.com"


def _get_csrf(client) -> str:
    r = client.get("/login", name="/login [csrf]")
    for cookie in client.cookiejar:
        if cookie.name == "csrftoken":
            return cookie.value
    return ""


# ── Анонимный пользователь (лендинг, маркетинговые страницы) ─────────────────

class AnonymousUser(TaskSet):
    @task(5)
    def landing(self):
        self.client.get("/", name="/ [landing]")

    @task(2)
    def login_page(self):
        self.client.get("/login", name="/login [page]")

    @task(2)
    def register_page(self):
        self.client.get("/register", name="/register [page]")

    @task(1)
    def privacy(self):
        self.client.get("/privacy", name="/privacy")

    @task(1)
    def health(self):
        self.client.get("/health", name="/health")

    @task(1)
    def robots(self):
        self.client.get("/robots.txt", name="/robots.txt")


# ── Зарегистрированный пользователь ──────────────────────────────────────────

class AuthenticatedUserFlow(SequentialTaskSet):
    """Полный путь нового пользователя: регистрация → профиль → свайп → матчи."""

    def on_start(self):
        self._email = _random_email()
        self._password = "LoadTest123!"
        self._csrf = ""
        self._logged_in = False
        self._register()

    def _register(self):
        csrf = _get_csrf(self.client)
        r = self.client.post(
            "/register",
            data={"email": self._email, "password": self._password, "csrftoken": csrf},
            name="/register [post]",
            allow_redirects=True,
        )
        self._logged_in = r.status_code < 400
        # Refresh csrf after registration
        for cookie in self.client.cookiejar:
            if cookie.name == "csrftoken":
                self._csrf = cookie.value

    @task
    def view_profile_edit(self):
        self.client.get("/profile/edit", name="/profile/edit [get]")

    @task
    def save_profile(self):
        r = self.client.post(
            "/profile/edit",
            data={
                "name": "Тест",
                "age": str(random.randint(20, 35)),
                "gender": "female",
                "looking_for": "male",
                "bio": "Тестовый пользователь",
                "csrftoken": self._csrf,
            },
            name="/profile/edit [post]",
            allow_redirects=True,
        )

    @task
    def view_swipe(self):
        self.client.get("/swipe", name="/swipe [get]")

    @task
    def view_matches(self):
        self.client.get("/matches", name="/matches [get]")

    @task
    def view_likes_received(self):
        self.client.get("/likes/received", name="/likes/received")


# ── Активный пользователь (только свайп и матчи) ─────────────────────────────

class ActiveUser(TaskSet):
    """Имитирует вернувшегося пользователя — быстрые повторные посещения."""

    def on_start(self):
        self._email = _random_email()
        self._csrf = _get_csrf(self.client)
        self.client.post(
            "/register",
            data={"email": self._email, "password": "LoadTest123!", "csrftoken": self._csrf},
            allow_redirects=True,
        )
        for cookie in self.client.cookiejar:
            if cookie.name == "csrftoken":
                self._csrf = cookie.value

    @task(4)
    def swipe_page(self):
        self.client.get("/swipe", name="/swipe")

    @task(3)
    def matches(self):
        self.client.get("/matches", name="/matches")

    @task(2)
    def likes_received(self):
        self.client.get("/likes/received", name="/likes/received")

    @task(1)
    def profile_edit(self):
        self.client.get("/profile/edit", name="/profile/edit")

    @task(1)
    def api_geo(self):
        self.client.get("/api/geo", name="/api/geo")

    @task(1)
    def health(self):
        self.client.get("/health", name="/health")


# ── Пользователи Locust ───────────────────────────────────────────────────────

class AnonymousVisitor(HttpUser):
    """Незарегистрированный посетитель (боты, поисковики, случайные заходы)."""
    weight = 2
    wait_time = between(1, 4)
    tasks = [AnonymousUser]


class NewUser(HttpUser):
    """Новый пользователь — регистрация + первый визит."""
    weight = 1
    wait_time = between(2, 5)
    tasks = [AuthenticatedUserFlow]


class ReturningUser(HttpUser):
    """Вернувшийся активный пользователь."""
    weight = 3
    wait_time = between(0.5, 2)
    tasks = [ActiveUser]
