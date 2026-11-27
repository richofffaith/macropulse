"""HTTP load test for the MacroPulse dashboard.

Streamlit does its real work over a websocket after the first HTML response,
so these times are the page and the health check, not chart-drawing time.

From the project folder, with the app already running:

    pip install locust
    locust -f locustfile.py --headless -u 75 -r 15 --run-time 60s --host http://127.0.0.1:8501

`-u` is concurrent users (use 50 to 100). `-r` is how many new users start
per second. `--run-time 60s` is the length of the test.

Read the summary table Locust prints at the end. p95 is the time within which
95% of requests finished. A local p95 under a couple of seconds on the HTML
page is healthy. A multi-second p95, or a failure rate above zero, means the
process was busy (often a data refresh) or the host URL was wrong.
"""

from locust import HttpUser, between, task


class DashboardUser(HttpUser):
    wait_time = between(1, 3)

    @task(5)
    def open_dashboard(self) -> None:
        self.client.get("/", name="dashboard")

    @task(1)
    def health(self) -> None:
        self.client.get("/_stcore/health", name="health")
