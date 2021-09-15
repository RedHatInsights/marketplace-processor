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
"""View for server status."""
import logging
import socket
from http import HTTPStatus

from django.db import connection
from django.db import InterfaceError
from django.db import NotSupportedError
from django.db import OperationalError
from django.db import ProgrammingError
from kafka import BrokerConnection
from rest_framework import permissions
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.response import Response

from api.status.model import Status
from api.status.serializer import StatusSerializer
from config.settings.base import INSIGHTS_KAFKA_HOST
from config.settings.base import INSIGHTS_KAFKA_PORT
from processor.processor_utils import format_message


LOG = logging.getLogger(__name__)
BROKER_CONNECTION = BrokerConnection(INSIGHTS_KAFKA_HOST, int(INSIGHTS_KAFKA_PORT), socket.AF_UNSPEC)


def check_kafka_connection():
    """Check connectability of Kafka Broker."""
    connected = BROKER_CONNECTION.connect_blocking(timeout=1)
    if connected:
        BROKER_CONNECTION.close()
    return connected


def check_database_connection():
    """Collect database connection information.
    :returns: boolean
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT datname AS database,
                    numbackends as database_connections
                FROM pg_stat_database
                """
            )
            raw = cursor.fetchall()

            # get pg_stat_database column names
            names = [desc[0] for desc in cursor.description]
    except (InterfaceError, NotSupportedError, OperationalError, ProgrammingError) as exc:
        LOG.warning("Unable to connect to DB: %s", str(exc))
        return False

    # transform list-of-lists into list-of-dicts including column names.
    result = [dict(zip(names, row)) for row in raw]  # noqa

    return True


@api_view(["GET", "HEAD"])
@permission_classes((permissions.AllowAny,))
def status(request):
    """Provide the server status information."""
    prefix = "STATUS"
    status_info = Status()

    if Status.readiness_failures > 5:
        LOG.info(format_message(prefix, "Application is unhealthy. Triggering an exit."))
        exit(-1)

    if not check_kafka_connection():
        status = HTTPStatus.FAILED_DEPENDENCY
        prefix = "STATUS"
        LOG.info(format_message(prefix, "Unable to establish connection with broker."))
        Status.readiness_failures = Status.readiness_failures + 1 
        return Response(data={"error": "Unable to establish connection with broker.", "status": status}, status=status)

    if not check_database_connection():
        status = HTTPStatus.FAILED_DEPENDENCY
        LOG.info(format_message(prefix, "Database backend not connected."))
        Status.readiness_failures = Status.readiness_failures + 1 
        return Response(data={"error": "Database backend not connected.", "status": status}, status=status)


    serializer = StatusSerializer(status_info)
    server_info = serializer.data
    server_info["server_address"] = request.META.get("HTTP_HOST", "localhost")

    if Status.healthy:
        return Response(server_info)
    
    LOG.info(format_message(prefix, "Application is unhealthy. Readiness failure count increased."))
    Status.readiness_failures = Status.readiness_failures + 1 
    
    return Response(status=500)
