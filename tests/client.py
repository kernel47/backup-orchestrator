"""Business-page tests run as a signed-in dashboard user; writes still require CSRF."""

from fastapi.testclient import TestClient

from orchestrator.security import issue_session


class DashboardClient(TestClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cookies.set("session", issue_session())
