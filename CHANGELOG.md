# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

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
