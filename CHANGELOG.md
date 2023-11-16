# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## [1.4.1] - 2023-11-15

### Changed
* Ignore known permission errors when scanning private Github repos and Quay images / 
continue analyzing all the remaining components for some managed service
* Fix migration that failed to run, so duplicate data is cleaned up and other migrations aren't blocked
* Change how Quarkus data is saved to avoid storing duplicate relationships
* Fix several small bugs that blocked reloading Quarkus data
* Fix duplicated root component in Quarkus manifests
* Reload Quarkus data to ensure it's up-to-date, avoid reporting stale data

## [1.4.0] - 2023-11-08
Note- incomplete changelog

### Changed
* refactored latest component filter into a pgsql stored proc

### Added
* added exclude_components to /api/v1/product_streams
* include_inactive_streams to /api/v1/components
* added provides_name to /api/v1/components
* added upstreams_name to /api/v1/components
* added sources_name to /api/v1/components
* added re_provides_name to /api/v1/components
* added re_upstreams_name to /api/v1/components
* added re_sources_name to /api/v1/components
