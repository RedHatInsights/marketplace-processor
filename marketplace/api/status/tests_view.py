#
# Copyright 2019 Red Hat, Inc.
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
"""Test the status API."""
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from api.status.model import Status


class StatusViewTest(TestCase):
    """Tests the status view."""

    def test_status_endpoint(self):
        """Test the status endpoint."""
        Status.healthy = True
        url = reverse("server-status")
        with patch("api.status.view.check_kafka_connection", return_value=True):
            with patch("api.status.view.check_database_connection", return_value=True):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                # json_result = response.json()
                # self.assertEqual(json_result["api_version"], 1)

    def test_status_endpoint_bad(self):
        """Test the status endpoint when unhealthy."""
        Status.healthy = False
        url = reverse("server-status")
        with patch("api.status.view.check_kafka_connection", return_value=True):
            with patch("api.status.view.check_database_connection", return_value=True):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 500)
