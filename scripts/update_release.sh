#!/usr/bin/env bash
# Update corgi version in all places

function check_and_set_version(){
    if [[ $(grep -E "${1}=\"[0-9]*\.[0-9]*\.[0-9]*\"" "${2}" -c) != 1 ]]; then
        echo "Didn't find ${1} version in ${2}. Giving up."
        exit 1
    else
        echo "Replacing version in ${2}."
        sed -i "s/${1}=\"[0-9]*\.[0-9]*\.[0-9]*\"/${1}=\"${3}\"/g" "${2}"
    fi
}

if [[ "${1}" =~ [0-9]*\.[0-9]*\.[0-9]* ]]; then
    echo "Replacing version in corgi/__init__.py"
    sed -i "s/__version__ = \"[0-9]*\.[0-9]*\.[0-9]*\"/__version__ = \"${1}\"/g" corgi/__init__.py

    check_and_set_version 'corgi_source_ref' 'openshift/inventory/corgi' "${1}"
else
    echo "invalid version ${1}"
    exit 1
fi

exit 0
