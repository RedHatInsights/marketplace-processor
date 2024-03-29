#
# Copyright 2018-2019 Red Hat, Inc.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
"""Models to capture server status."""
import logging
import os
import platform
import subprocess
import sys

from api import API_VERSION
from release import BUILD_VERSION

logger = logging.getLogger(__name__)  # pylint: disable=invalid-name


class Status:
    """A server's status."""

    healthy = True
    readiness_failures = 0

    @property
    def git_commit(self):  # pylint: disable=R0201
        """Collect the build number for the server.

        :returns: A build number
        """
        git_commit = os.environ.get("GIT_COMMIT", None)
        if git_commit is None:
            git_commit = subprocess.run(["git", "describe", "--always"], stdout=subprocess.PIPE, check=True)
            if git_commit.stdout:
                git_commit = git_commit.stdout.decode("utf-8").strip()
        return git_commit

    @property
    def platform_info(self):  # pylint: disable=R0201
        """Collect the platform information.

        :returns: A dictionary of platform data
        """
        return platform.uname()._asdict()

    @property
    def python_version(self):  # pylint: disable=R0201
        """Collect the python version information.

        :returns: The python version string.
        """
        return sys.version.replace("\n", "")

    @property
    def modules(self):  # pylint: disable=R0201
        """Collect the installed modules.

        :returns: A dictonary of module names and versions.
        """
        module_data = {}
        for name, module in sorted(sys.modules.items()):
            if hasattr(module, "__version__"):
                module_data[str(name)] = str(module.__version__)
        return module_data

    @property
    def api_version(self):
        """Return the API version."""
        return API_VERSION

    @property
    def release_version(self):
        """Return the release version."""
        return f"{BUILD_VERSION}.{self.git_commit}"

    @property
    def environment_vars(self):
        """Return the non-sensitive envs."""
        env_dict = {}
        for key, value in os.environ.items():
            if "password" in key.lower():
                value = "*" * 8
            env_dict[key] = value
        return env_dict

    def startup(self):
        """Log startup information."""
        logger.info("Platform:")
        for name, value in self.platform_info.items():
            logger.info("%s - %s ", name, value)

        logger.info("Python: %s", self.python_version)

        logger.info("Commit: %s", self.git_commit)
        logger.info("Release Version: %s", self.release_version)
        logger.info("API Version: %s", self.api_version)

        prefix = "-" * 18

        # Python modules
        logger.info("%s BEGIN Python Modules %s", prefix, prefix)
        for key, value in self.modules.items():
            logger.info("%s=%s", key, value)
        logger.info("%s END Python Modules %s", prefix, prefix)

        # Environment variables
        logger.info("%s BEGIN Environment Variables %s", prefix, prefix)
        for key, value in self.environment_vars.items():
            logger.info("%s=%s", key, value)
        logger.info("%s END Environment Variables %s", prefix, prefix)
