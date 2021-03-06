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
from rest_framework import permissions
from rest_framework.decorators import api_view
from rest_framework.decorators import permission_classes
from rest_framework.response import Response

from api.status.model import Status
from api.status.serializer import StatusSerializer


@api_view(["GET", "HEAD"])
@permission_classes((permissions.AllowAny,))
def status(request):
    """Provide the server status information."""
    status_info = Status()
    serializer = StatusSerializer(status_info)
    server_info = serializer.data
    server_info["server_address"] = request.META.get("HTTP_HOST", "localhost")

    if Status.healthy:
        return Response(server_info)

    return Response(status=500)
