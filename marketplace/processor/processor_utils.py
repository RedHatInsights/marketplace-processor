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
"""Utilities for all of the processor classes."""
import asyncio
import logging
import sys
import time

from api.status.model import Status


LOG = logging.getLogger(__name__)
UPLOAD_REPORT_CONSUMER_LOOP = asyncio.get_event_loop()
REPORT_PROCESSING_LOOP = asyncio.new_event_loop()
SLICE_PROCESSING_LOOP = asyncio.new_event_loop()
GARBAGE_COLLECTION_LOOP = asyncio.new_event_loop()
PROCESSOR_INSTANCES = []  # this list holds processor instances that have kafka components


def format_message(prefix, message, account_number=None, report_platform_id=None, request_id=None):
    """Format log messages in a consistent way.

    :param prefix: (str) A meaningful prefix to be displayed in all caps.
    :param message: (str) A short message describing the state
    :param account_number: (str) The account sending the report.
    :param report_platform_id: (str) The marketplace report id.
    :returns: (str) containing formatted message
    """
    if not report_platform_id and not account_number and not request_id:
        actual_message = f"{prefix} - {message}"
    elif account_number and not report_platform_id and not request_id:
        actual_message = f"Report(account={account_number}) {prefix} - {message}"
    elif account_number and request_id and not report_platform_id:
        actual_message = f"Report(account={account_number}, request_id={request_id}) {prefix} - {message}"
    elif account_number and report_platform_id and not request_id:
        actual_message = "Report(account={}, report_platform_id={}) {} - {}".format(
            account_number, report_platform_id, prefix, message
        )
    else:
        actual_message = "Report(account={}, request_id={}, report_platform_id={}) {} - {}".format(
            account_number, request_id, report_platform_id, prefix, message
        )

    return actual_message


def stop_all_event_loops():
    """Stop all of the event loops."""
    prefix = "STOPPING EVENT LOOPS"
    for i in PROCESSOR_INSTANCES:
        try:
            # the only processor with a consumer is the ReportConsumer
            # so we check the class and stop the consumer if we have a
            # ReportConsumer instance - otherwise we stop a producer
            if i:
                if i.__class__.__name__ == "ReportConsumer" and i.consumer:
                    i.consumer.close()
                elif i.__class__.__name__ == "ReportProcessor" and i.producer:
                    i.producer.close()
        except Exception as err:  # pylint:disable=broad-except
            LOG.error(format_message(prefix, "The following error occurred: %s" % err))
    try:
        LOG.error(format_message(prefix, "A fatal error occurred. Shutting down all processors: "))
        LOG.info(format_message(prefix, "Shutting down the report consumer."))
        UPLOAD_REPORT_CONSUMER_LOOP.stop()
        LOG.info(format_message(prefix, "Shutting down the report processor."))
        REPORT_PROCESSING_LOOP.stop()
        LOG.info(format_message(prefix, "Shutting down the report slice processor."))
        SLICE_PROCESSING_LOOP.stop()
        LOG.info(format_message(prefix, "Shutting down the garbage collector."))
        GARBAGE_COLLECTION_LOOP.stop()
        Status.healthy = False
        testing = len(sys.argv) > 1 and sys.argv[1] == "test"
        if not testing:
            LOG.info(format_message(prefix, "Sleeping for 10 minutes to allow for fatal error resolution."))
            time.sleep(600)
            exit(-1)
    except Exception as err:  # pylint: disable=broad-except
        LOG.error(format_message(prefix, str(err)))
