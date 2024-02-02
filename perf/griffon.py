from locust import HttpUser, between, task


class GriffonUser(HttpUser):
    """perf test common griffon queries"""

    wait_time = between(1, 3)

    def on_start(self):
        self.client.verify = False

    @task
    def get_product_streams(self):
        self.client.get("/api/v1/product_streams?include_fields=name,ofuri,active")

    @task
    def get_re_name_provides_small_latest(self):
        self.client.get(
            "/api/v1/components?re_provides_name=pdf-generator&include_fields=purl,link&latest_components_by_streams=True",  # noqa
        )

    @task
    def get_re_name_provides_small_latest_active(self):
        self.client.get(
            "/api/v1/components?re_provides_name=pdf-generator&include_fields=purl,link&active_streams=True&latest_components_by_streams=True",  # noqa
        )

    @task
    def get_re_name_provides_medium(self):
        self.client.get(
            "/api/v1/components?re_provides_name=nmap&include_fields=purl,link&active_streams=True&latest_components_by_streams=True",  # noqa
        )

    @task
    def get_re_name_provides_latest_medium(self):
        self.client.get(
            "/api/v1/components?re_provides_name=webkitgtk&include_fields=purl,link&latest_components_by_streams=True",  # noqa
        )

    @task
    def get_re_name_provides_medium_latest_active(self):
        self.client.get(
            "/api/v1/components?re_provides_name=webkitgtk&include_fields=purl,link&active_streams=True&latest_components_by_streams=True",  # noqa
        )
