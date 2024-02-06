from locust import HttpUser, between, task


class ServiceApiV1(HttpUser):
    """perf test service api/v1"""

    wait_time = between(1, 3)

    def on_start(self):
        self.client.verify = False

    @task
    def get_home(self):
        self.client.get(
            "/",
        )

    @task
    def get_status(self):
        self.client.get(
            "/api/v1/status",
        )

    @task
    def get_components(self):
        self.client.get("/api/v1/components")

    @task
    def get_components_name(self):
        self.client.get("/api/v1/components?name=curl")

    @task
    def get_components_re_name(self):
        self.client.get("/api/v1/components?re_name=curl")

    @task
    def get_components_include_fields(self):
        resp = self.client.get(
            "/api/v1/components?re_name=curl&include_fields=name,nvr,purl,product_streams"
        )
        resp.raise_for_status()
        json = resp.json()
        assert "results" in json

    @task
    def get_products(self):
        self.client.get("/api/v1/products")

    @task
    def get_product_versions(self):
        self.client.get("/api/v1/product_versions")

    @task
    def get_product_streams(self):
        self.client.get("/api/v1/product_streams")

    @task
    def get_product_variants(self):
        self.client.get("/api/v1/product_variants")

    @task
    def get_channels(self):
        self.client.get("/api/v1/channels")
