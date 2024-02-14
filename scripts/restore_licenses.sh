#!/usr/bin/env bash
set -e

export PGPASSWORD=test

if [[ -z "$1" ]]; then
    licenses_file='./licenses.tar.gz'
else
    licenses_file="$1"
fi

echo "Decompressing ${licenses_file} ..."
tar xvf "${licenses_file}"

echo "Creating a temporary table to populate with values from licenses_file"
psql -h localhost -p 5433 -U corgi-db-user --dbname corgi-db -c 'CREATE TABLE license_temp(purl varchar(1024), copyright_text text, license_concluded_raw text, openlcs_scan_url text, openlcs_scan_version text)'

echo "Populating the temporary table"
psql -h localhost -p 5433 -U corgi-db-user --dbname corgi-db -c '\copy license_temp(purl, copyright_text, license_concluded_raw, openlcs_scan_url, openlcs_scan_version) from licenses.txt'

echo "Clear existing OpenLCS data"
psql -h localhost -p 5433 -U corgi-db-user --dbname corgi-db -c "UPDATE core_component SET copyright_text = '', license_concluded_raw = '', openlcs_scan_url = '', openlcs_scan_version = '' where openlcs_scan_url <> '';"

echo "Prepare for mass update of core_component"
psql -h localhost -p 5433 -U corgi-db-user --dbname corgi-db -c 'ANALYZE core_component; set work_mem=163840'

echo "Updating the core_component table with values from temporary table"
psql -h localhost -p 5433 -U corgi-db-user --dbname corgi-db -c 'UPDATE core_component o SET copyright_text=t.copyright_text, license_concluded_raw=t.license_concluded_raw, openlcs_scan_url=t.openlcs_scan_url, openlcs_scan_version=t.openlcs_scan_version FROM license_temp t WHERE o.purl=t.purl'

echo "Resetting work_mem and removing the temporary table"
psql -h localhost -p 5433 -U corgi-db-user --dbname corgi-db -c 'set work_mem=16384; DROP TABLE license_temp'




