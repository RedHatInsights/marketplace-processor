#!/usr/bin/env python

import json
import os

lockfile = {}

CONTAINER_NAME = "registry.access.redhat.com/ubi7/python-36:latest"

with open("Pipfile.lock") as json_file:
    lockfile = json.load(json_file)

with open("marketplace-manifest", "w") as manifest:
    manifest.write(f"mgmt_services/marketplace:processor/{CONTAINER_NAME}\n")
    for name, value in sorted(lockfile["default"].items()):
        if "version" in value:
            version = value["version"].replace("=", "")
            manifest.write("mgmt_services/marketplace:processor/python-%s:%s.pipfile\n" % (name, version))
        elif "ref" in value:
            ref = value["ref"]
            manifest.write("mgmt_services/marketplace:processor/python-%s:%s.pipfile\n" % (name, ref))
        else:
            raise "unable to parse %s" % value
