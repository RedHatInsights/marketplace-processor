#!/usr/bin/env python
import json

lockfile = {}

CONTAINER_NAME = "registry.redhat.io/ubi8/python-38:latest"

with open("Pipfile.lock") as json_file:
    lockfile = json.load(json_file)

with open("marketplace-manifest", "w") as manifest:
    manifest.write(f"mgmt_services/marketplace:processor/{CONTAINER_NAME}\n")
    for name, value in sorted(lockfile["default"].items()):
        if "version" in value:
            version = value["version"].replace("=", "")
            manifest.write(f"mgmt_services/marketplace:processor/python-{name}:{version}.pipfile\n")
        elif "ref" in value:
            ref = value["ref"]
            manifest.write(f"mgmt_services/marketplace:processor/python-{name}:{ref}.pipfile\n")
        else:
            raise "unable to parse %s" % value
