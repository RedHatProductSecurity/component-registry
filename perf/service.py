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


class OlderTestsServiceApiV1(HttpUser):
    """perf test service api/v1"""

    wait_time = between(1, 3)

    def on_start(self):
        self.client.verify = False

    @task
    def test_displaying_product_stream_with_many_roots(self):
        response = self.client.get(
            "/api/v1/components?ofuri=o:redhat:rhel:8.8.0.z&view=summary&limit=5000"
        )
        response.raise_for_status()
        response_json = response.json()
        assert response_json["count"] > 1900

    @task
    def test_displaying_component_with_many_sources(self):
        large_component_purl = "pkg:rpm/redhat/systemd-libs@250-12.el9_1?arch=aarch64"
        response = self.client.get(f"/api/v1/components?provides={large_component_purl}")
        response_json = response.json()
        assert response_json["count"] > 2000

    @task
    def display_manifest_with_many_components(self):
        large_stream_ofuri = "o:redhat:rhel:9.2.0"
        response = self.client.get(f"/api/v1/product_streams?ofuri={large_stream_ofuri}")
        response.raise_for_status()
        response_json = response.json()

        external_name = response_json["external_name"]
        manifest_link = response_json["manifest"]
        assert manifest_link == f"/staticfiles/{external_name}.json"

        response = self.client.get(manifest_link)
        response.raise_for_status()
        response_json = response.json()

        assert len(response_json["packages"]) > 9000
        return response_json
