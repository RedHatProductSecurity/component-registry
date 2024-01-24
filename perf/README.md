# Performance tests

This directory contains a set of standalone [locust](https://docs.locust.io/en/stable/) perf tests

In local dev env can access locust [here](http://localhost:9000/).

To independently run locust, first install locust
```shell
> pip install locust
```
and invoke locust web ux setting the host (ex. _http://corgi-nginx:8080_) and which tests to run (ex. _CoreUser_)
```shell
> pip install locust
> locust --class-picker -f ./perf -H http://corgi-nginx:8080 --web-host 0.0.0.0  --modern-ui
```


