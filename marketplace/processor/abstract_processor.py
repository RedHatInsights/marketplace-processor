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
"""Upload message report processor."""
import asyncio
import json
import logging
from abc import ABC
from datetime import datetime
from datetime import timedelta
from enum import Enum

import pytz
from asgiref.sync import sync_to_async
from django.db import transaction
from prometheus_client import Counter
from prometheus_client import Gauge
from prometheus_client import Summary

from api.models import Report
from api.models import ReportSlice
from api.models import Status
from api.serializers import ReportArchiveSerializer
from api.serializers import ReportSliceArchiveSerializer
from config.settings.base import NEW_REPORT_QUERY_INTERVAL
from config.settings.base import RETRIES_ALLOWED
from config.settings.base import RETRY_TIME
from processor.processor_utils import format_message
from processor.processor_utils import stop_all_event_loops
from processor.report_consumer import DB_ERRORS
from processor.report_consumer import MKTReportException

LOG = logging.getLogger(__name__)
FAILURE_CONFIRM_STATUS = "failure"

FAILED_VALIDATION = "VALIDATION"
NEW_REPORT_QUERY_INTERVAL = int(NEW_REPORT_QUERY_INTERVAL)
RETRY = Enum("RETRY", "clear increment keep_same")
RETRIES_ALLOWED = int(RETRIES_ALLOWED)
RETRY_TIME = int(RETRY_TIME)

# setup for prometheus metrics
QUEUED_REPORTS = Gauge("queued_reports", "Reports waiting to be processed")
QUEUED_REPORT_SLICES = Gauge("queued_report_slices", "Report Slices waiting to be processed")
ARCHIVED_FAIL_REPORTS = Gauge(
    "archived_fail_reports", "Reports that have been archived as failures", ["account_number"]
)
ARCHIVED_SUCCESS_REPORTS = Gauge(
    "archived_success_reports", "Reports that have been archived as successes", ["account_number"]
)
ARCHIVED_FAIL_SLICES = Gauge("archived_fail_slices", "Slices that have been archived as failures", ["account_number"])
ARCHIVED_SUCCESS_SLICES = Gauge(
    "archived_success_slices", "Slices that have been archived as successes", ["account_number"]
)
DOWNLOAD_REPORTS = Counter("download_reports", "Reports that were successfuly downloaded", ["account_number"])
UPLOADED_REPORT_METRICS = Counter(
    "uploaded_report_metrics", "Report metrics that were successfuly uploaded", ["account_number"]
)
FAILED_TO_DOWNLOAD = Gauge("failed_download", "Reports that failed to download", ["account_number"])
FAILED_TO_VALIDATE = Gauge("failed_validation", "Reports that could not be validated", ["account_number"])
INVALID_REPORTS = Gauge("invalid_reports", "Reports containing invalid syntax", ["account_number"])
TIME_RETRIES = Gauge("time_retries", "The total number of retries based on time for all reports", ["account_number"])
COMMIT_RETRIES = Gauge(
    "commit_retries", "The total number of retries based on commit for all reports", ["account_number"]
)
REPORT_PROCESSING_LATENCY = Summary(
    "report_processing_latency", "The time in seconds that it takes to process a report"
)
VALIDATION_LATENCY = Summary("validation_latency", "The time it takes to validate a report")


# pylint: disable=broad-except, too-many-lines, too-many-public-methods
class AbstractProcessor(ABC):  # pylint: disable=too-many-instance-attributes
    """Class for processing saved reports that have been uploaded."""

    # pylint: disable=too-many-arguments
    def __init__(
        self,
        pre_delegate,
        state_functions,
        state_metrics,
        async_states,
        object_prefix,
        object_class,
        object_serializer,
    ):
        """Create an abstract processor."""
        self.report_or_slice = None
        self.object_class = object_class
        self.object_serializer = object_serializer
        self.run_before_delegate = pre_delegate
        self.state_functions = state_functions
        self.state_to_metric = state_metrics
        self.async_states = async_states
        self.object_prefix = object_prefix
        self.prefix = "PROCESSING %s" % self.object_prefix
        self.state = None
        self.next_state = None
        self.account_number = None
        self.upload_message = None
        self.report_platform_id = None
        self.report_slice_id = None
        self.report_json = None
        self.status = None
        self.report = None
        self.should_run = True

    def reset_variables(self):
        """Reset the class variables to original values."""
        self.report_or_slice = None
        self.state = None
        self.account_number = None
        self.upload_message = None
        self.report_platform_id = None
        self.report_slice_id = None
        self.report_json = None
        self.status = None
        self.report = None
        self.prefix = "PROCESSING %s" % self.object_prefix

    async def run(self):
        """Run the report processor in a loop.

        Later, if we find that we want to stop looping, we can
        manipulate the class variable should_run.
        """
        while self.should_run:
            if not self.report_or_slice:
                try:
                    async_assign = sync_to_async(self.assign_object)
                    await async_assign()
                except Exception as error:  # pylint:disable=broad-except
                    LOG.error(
                        format_message(self.prefix, "The following error occurred: %s." % str(error)), exc_info=error
                    )
                    stop_all_event_loops()
            if self.report_or_slice:
                try:
                    await self.delegate_state()
                except Exception as error:
                    LOG.error(
                        format_message(self.prefix, "The following error occurred: %s." % str(error)), exc_info=error
                    )
                    self.reset_variables()
            else:
                await asyncio.sleep(NEW_REPORT_QUERY_INTERVAL)

    @DB_ERRORS.count_exceptions()
    def calculate_queued_objects(self, current_time, status_info):
        """Calculate the number of reports waiting to be processed.

        :param current_time: time object.
        :param status_info: status object.
        """
        minimum_update_time = current_time - timedelta(minutes=RETRY_TIME)
        # we want to grab all the new reports, or all the reports that are ready to be retried
        # based on time, or commit
        all_objects = self.object_class.objects.all()
        new_objects = all_objects.filter(state=self.object_class.NEW)
        retry_time_objects = all_objects.filter(
            retry_type=self.object_class.TIME, last_update_time__lte=minimum_update_time
        ).exclude(state=self.object_class.NEW)
        retry_commit_objects = (
            all_objects.filter(retry_type=self.object_class.GIT_COMMIT)
            .exclude(state=self.object_class.NEW)
            .exclude(git_commit=status_info.git_commit)
        )
        objects_count = new_objects.count() + retry_time_objects.count() + retry_commit_objects.count()

        return objects_count

    @staticmethod
    def return_queryset_object(queryset):
        """Return the earliest object in a queryset.

        param queryset: the queryset we care about
        returns: the earliest object in the queryset or None.
        """
        try:
            report_or_slice = queryset.earliest("last_update_time")
            return report_or_slice
        except (Report.DoesNotExist, ReportSlice.DoesNotExist):
            return None

    @DB_ERRORS.count_exceptions()
    def get_oldest_object_to_retry(self):
        """Grab the oldest report or report slice object to retry.

        returns: object to retry or None.
        """
        status_info = Status()
        current_time = datetime.now(pytz.utc)
        objects_count = self.calculate_queued_objects(current_time, status_info)
        if self.object_class == Report:
            QUEUED_REPORTS.set(objects_count)
        else:
            QUEUED_REPORT_SLICES.set(objects_count)
        LOG.info(
            format_message(
                self.prefix,
                "Number of {} waiting to be processed: {}".format(self.object_prefix.lower() + "s", objects_count),
            )
        )
        # first we have to query for all objects with commit retries
        commit_retry_query = self.object_class.objects.filter(retry_type=self.object_class.GIT_COMMIT)
        # then we grab the oldest object from the query
        oldest_commit_object = self.return_queryset_object(queryset=commit_retry_query)
        if oldest_commit_object:
            same_commit = oldest_commit_object.git_commit == status_info.git_commit
            if not same_commit:
                return oldest_commit_object
        # If the above doesn't return, we should query for all time retries
        time_retry_query = self.object_class.objects.filter(retry_type=self.object_class.TIME)
        oldest_time_object = self.return_queryset_object(queryset=time_retry_query)
        if oldest_time_object:
            minutes_passed = int((current_time - oldest_time_object.last_update_time).total_seconds() / 60)
            if minutes_passed >= RETRY_TIME:
                return oldest_time_object
        # if we haven't returned a retry object, return None
        return None

    @DB_ERRORS.count_exceptions()
    def get_new_record(self):
        """Grab the newest report or report slice object."""
        # Get the queryset for all of the objects in the NEW state
        new_object_query = self.object_class.objects.filter(state=self.object_class.NEW)
        oldest_new_object = self.return_queryset_object(queryset=new_object_query)
        return oldest_new_object

    @transaction.atomic
    def assign_object(self):
        """Assign the object processor objects that are saved in the db.

        First priority is the oldest object in any state. We check to see if an
        appropriate amount of time has passed  or code has changed before we retry this object.

        If none of the above qualify, we look for the oldest objects that are in the new state.
        """
        self.prefix = "ASSIGNING %s" % self.object_prefix
        object_found_message = 'Starting %s processor. State is "%s".'
        if self.report_or_slice is None:
            assigned = False
            oldest_object_to_retry = self.get_oldest_object_to_retry()
            if oldest_object_to_retry:
                assigned = True
                self.report_or_slice = oldest_object_to_retry
                self.next_state = oldest_object_to_retry.state
                LOG.info(
                    format_message(
                        self.prefix,
                        object_found_message % (self.object_prefix.lower(), self.report_or_slice.state),
                        account_number=self.account_number,
                        report_platform_id=self.report_or_slice.report_platform_id,
                    )
                )
                options = {"retry": RETRY.keep_same}
                self.update_object_state(options=options)
            else:
                new_object = self.get_new_record()
                if new_object:
                    assigned = True
                    self.report_or_slice = new_object
                    LOG.info(
                        format_message(
                            self.prefix,
                            object_found_message % (self.object_prefix.lower(), self.report_or_slice.state),
                            account_number=self.account_number,
                            report_platform_id=self.report_or_slice.report_platform_id,
                        )
                    )
                    self.transition_to_started()
            if not assigned:
                object_not_found_message = "No %s to be processed at this time. " "Checking again in %s seconds." % (
                    self.object_prefix.lower() + "s",
                    str(NEW_REPORT_QUERY_INTERVAL),
                )
                LOG.info(format_message(self.prefix, object_not_found_message))

    async def delegate_state(self):
        """Call the correct function based on report state.

        If the function is async, make sure to await it.
        """
        self.run_before_delegate()
        # if the function is async, we must await it
        if self.state_functions.get(self.state):
            if self.state in self.async_states:
                await self.state_functions.get(self.state)()
                # async_to_sync(self.state_functions.get(self.state))
            else:
                async_state_func = sync_to_async(self.state_functions.get(self.state))
                await async_state_func()
        else:
            self.reset_variables()

    def transition_to_started(self):
        """Attempt to change the state to started."""
        self.next_state = self.object_class.STARTED
        options = {"start_processing": True}
        self.update_object_state(options=options)

    #  pylint: disable=too-many-locals, too-many-branches, too-many-statements
    def update_object_state(self, options):  # noqa: C901 (too-complex)
        """
        Update the report processor state and save.

        :param options: <dict> potentially containing the following:
            retry: <enum> Retry.clear=clear count, RETRY.increment=increase count
            retry_type: <str> either time=retry after time,
                git_commit=retry after new commit
            report_json: <dict> dictionary containing the report json
            report_platform_id: <str> string containing report_platform_id
            status: <str> either success or failure based on the report
            source: <str> containing OpenShift cluster ID
            source_metadata: <dict> containing metadata info about the source
            ready_to_archive: <bool> bool regarding archive
        """
        try:
            status_info = Status()
            self.state = self.next_state

            # grab all of the potential options
            retry_type = options.get("retry_type", self.object_class.TIME)
            retry = options.get("retry", RETRY.clear)
            report_json = options.get("report_json")
            report_platform_id = options.get("report_platform_id")
            status = options.get("status")
            source = options.get("source")
            source_metadata = options.get("source_metadata")
            ready_to_archive = options.get("ready_to_archive")
            start_processing = options.get("start_processing")

            update_data = {
                "last_update_time": datetime.now(pytz.utc),
                "state": self.next_state,
                "git_commit": status_info.git_commit,
            }
            # if this is the start of the processing, update the processing
            # start time
            if start_processing:
                update_data["processing_start_time"] = datetime.now(pytz.utc)

            if retry == RETRY.clear:
                # After a successful transaction when we have reached the update
                # point, we want to set the Retry count back to 0 because
                # any future failures should be unrelated
                update_data["retry_count"] = 0
                update_data["retry_type"] = self.object_class.TIME
            elif retry == RETRY.increment:
                retry_count = self.report_or_slice.retry_count
                update_data["retry_count"] = retry_count + 1
                update_data["retry_type"] = retry_type

            # the other choice for retry is RETRY.keep_same in which case we don't
            # want to do anything to the retry count bc we want to preserve as is
            if report_json:
                update_data["report_json"] = json.dumps(report_json)
            if report_platform_id:
                update_data["report_platform_id"] = report_platform_id
            if status:
                update_data["upload_ack_status"] = status
            if source:
                update_data["source"] = source
            if source_metadata:
                update_data["source_metadata"] = json.dumps(source_metadata)
            if ready_to_archive:
                update_data["ready_to_archive"] = ready_to_archive

            state_info = json.loads(self.report_or_slice.state_info)
            state_info.append(self.next_state)
            update_data["state_info"] = json.dumps(state_info)

            serializer = self.object_serializer(instance=self.report_or_slice, data=update_data, partial=True)

            serializer.is_valid(raise_exception=True)
            serializer.save()

        except Exception as error:
            DB_ERRORS.inc()
            self.should_run = False
            LOG.error(
                format_message(
                    self.prefix,
                    "Could not update {} record due to the following error {}.".format(
                        self.object_prefix.lower(), str(error)
                    ),
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            stop_all_event_loops()

    def determine_retry(self, fail_state, current_state, retry_type=Report.TIME):
        """Determine if processor should archive a report based on retry count.

        :param fail_state: <str> the final state if we have reached max retries
        :param current_state: <str> the current state we are in that we want to try again
        :param retry_type: <str> either 'time' or 'commit'
        """
        if (self.report_or_slice.retry_count + 1) >= RETRIES_ALLOWED:
            LOG.error(
                format_message(
                    self.prefix,
                    "This {} has reached the retry limit of {}.".format(
                        self.object_prefix.lower(), str(RETRIES_ALLOWED)
                    ),
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            self.next_state = fail_state
            options = {"retry": RETRY.increment, "retry_type": retry_type, "ready_to_archive": True}
            self.update_object_state(options=options)
        else:
            self.next_state = current_state
            if retry_type == self.object_class.GIT_COMMIT:
                COMMIT_RETRIES.labels(account_number=self.account_number).inc()
                log_message = "Saving the %s to retry when a new commit " "is pushed. Retries: %s" % (
                    self.object_prefix.lower(),
                    str(self.report_or_slice.retry_count + 1),
                )
            else:
                TIME_RETRIES.labels(account_number=self.account_number).inc()
                log_message = "Saving the %s to retry at in %s minutes. " "Retries: %s" % (
                    self.object_prefix.lower(),
                    str(RETRY_TIME),
                    str(self.report_or_slice.retry_count + 1),
                )
            LOG.error(
                format_message(
                    self.prefix,
                    log_message,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )

            options = {"retry": RETRY.increment, "retry_type": retry_type}
            self.update_object_state(options=options)
            self.reset_variables()

    def record_failed_state_metrics(self):
        """Record the metrics based on the report or slice state."""
        if self.state in self.state_to_metric.keys():
            metric = self.state_to_metric.get(self.state)
            metric.labels(account_number=self.account_number).inc()

    def log_time_stats(self, archived_rep):
        """Log the start/completion and processing times of the report."""
        arrival_time = archived_rep.arrival_time
        processing_start_time = archived_rep.processing_start_time
        processing_end_time = archived_rep.processing_end_time
        # format arrival_time
        arrival_date_time = "{}: {}:{}:{:.2f}".format(
            arrival_time.date(), arrival_time.hour, arrival_time.minute, arrival_time.second
        )
        completion_date_time = "{}: {}:{}:{:.2f}".format(
            processing_end_time.date(),
            processing_end_time.hour,
            processing_end_time.minute,
            processing_end_time.second,
        )
        # time in queue & processing in minutes
        total_hours_in_queue = int((processing_start_time - arrival_time).total_seconds() / 3600)
        total_minutes_in_queue = int((processing_start_time - arrival_time).total_seconds() / 60)
        total_seconds_in_queue = int((processing_start_time - arrival_time).total_seconds() % 60)
        time_in_queue = f"{total_hours_in_queue}h {total_minutes_in_queue}m {total_seconds_in_queue}s"
        total_processing_hours = int((processing_end_time - processing_start_time).total_seconds() / 3600)
        total_processing_minutes = int((processing_end_time - processing_start_time).total_seconds() / 60)
        total_processing_seconds = int((processing_end_time - processing_start_time).total_seconds() % 60)
        time_processing = "{}h {}m {}s".format(
            total_processing_hours, total_processing_minutes, total_processing_seconds
        )

        total_processing_time_in_seconds = int((processing_end_time - processing_start_time).total_seconds() % 60)
        REPORT_PROCESSING_LATENCY.observe(total_processing_time_in_seconds)

        report_time_facts = (
            "\nArrival date & time: {} "
            "\nTime spent in queue: {}"
            "\nTime spent processing report: {}"
            "\nCompletion date & time: {}".format(
                arrival_date_time, time_in_queue, time_processing, completion_date_time
            )
        )
        LOG.info(
            format_message(
                "REPORT TIME STATS",
                report_time_facts,
                account_number=self.account_number,
                report_platform_id=self.report_platform_id,
            )
        )

    @DB_ERRORS.count_exceptions()  # noqa: C901 (too-complex)
    @transaction.atomic
    def archive_report_and_slices(self):  # noqa: C901 (too-complex)
        """Archive the report slice objects & associated report."""
        self.prefix = "ARCHIVING"
        if self.object_class == Report:
            report = self.report_or_slice
        else:
            report = self.report_or_slice.report
        all_report_slices = []
        all_slices_ready = True
        try:
            all_report_slices = ReportSlice.objects.all().filter(report=report)
            for report_slice in all_report_slices:
                if not report_slice.ready_to_archive:
                    all_slices_ready = False
                    break
        except ReportSlice.DoesNotExist:
            pass

        if report.ready_to_archive and all_slices_ready:
            # archive the report object
            failed = False
            LOG.info(
                format_message(
                    self.prefix,
                    "Archiving report.",
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            archived_rep_data = {
                "account": report.account,
                "retry_count": report.retry_count,
                "retry_type": report.retry_type,
                "state": report.state,
                "state_info": report.state_info,
                "ready_to_archive": report.ready_to_archive,
                "last_update_time": report.last_update_time,
                "upload_srv_kafka_msg": report.upload_srv_kafka_msg,
                "arrival_time": report.arrival_time,
                "processing_start_time": report.processing_start_time,
                "processing_end_time": datetime.now(pytz.utc),
            }
            if report.upload_ack_status:
                if report.upload_ack_status == FAILURE_CONFIRM_STATUS:
                    failed = True
                    INVALID_REPORTS.labels(account_number=self.account_number).inc()
                archived_rep_data["upload_ack_status"] = report.upload_ack_status
            if report.report_platform_id:
                archived_rep_data["report_platform_id"] = report.report_platform_id
            rep_serializer = ReportArchiveSerializer(data=archived_rep_data)
            rep_serializer.is_valid(raise_exception=True)
            archived_rep = rep_serializer.save()
            LOG.info(
                format_message(
                    self.prefix,
                    "Report successfully archived.",
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )

            failed_states = [Report.FAILED_DOWNLOAD, Report.FAILED_VALIDATION, Report.FAILED_VALIDATION_REPORTING]
            if report.state in failed_states or failed:
                ARCHIVED_FAIL_REPORTS.labels(account_number=self.account_number).inc()
            else:
                ARCHIVED_SUCCESS_REPORTS.labels(account_number=self.account_number).inc()

            # loop through the associated reports & archive them
            for report_slice in all_report_slices:
                archived_slice_data = {
                    "account": report_slice.account,
                    "retry_count": report_slice.retry_count,
                    "retry_type": report_slice.retry_type,
                    "state": report_slice.state,
                    "ready_to_archive": report_slice.ready_to_archive,
                    "state_info": report_slice.state_info,
                    "last_update_time": report_slice.last_update_time,
                    "report_slice_id": report_slice.report_slice_id,
                    "report": archived_rep.id,
                    "source": report_slice.source,
                    "creation_time": report_slice.creation_time,
                    "processing_start_time": report_slice.processing_start_time,
                    "processing_end_time": datetime.now(pytz.utc),
                }
                if report_slice.report_platform_id:
                    archived_slice_data["report_platform_id"] = report_slice.report_platform_id
                if report_slice.report_json:
                    archived_slice_data["report_json"] = report_slice.report_json
                slice_serializer = ReportSliceArchiveSerializer(data=archived_slice_data)
                slice_serializer.is_valid(raise_exception=True)
                slice_serializer.save()
                failed_states = [ReportSlice.FAILED_VALIDATION, ReportSlice.FAILED_METRICS_UPLOAD]
                if report_slice.state in failed_states:
                    ARCHIVED_FAIL_SLICES.labels(account_number=self.account_number).inc()
                else:
                    ARCHIVED_SUCCESS_SLICES.labels(account_number=self.account_number).inc()
                LOG.info(
                    format_message(
                        self.prefix,
                        "Archiving report slice %s." % report_slice.report_slice_id,
                        account_number=self.account_number,
                        report_platform_id=self.report_platform_id,
                    )
                )
            self.record_failed_state_metrics()
            # now delete the report object and it will delete all of the associated
            # report slices
            try:
                Report.objects.get(id=report.id).delete()
            except Report.DoesNotExist:
                pass
            if all_report_slices:
                LOG.info(
                    format_message(
                        self.prefix,
                        "Report slices successfully archived.",
                        account_number=self.account_number,
                        report_platform_id=self.report_platform_id,
                    )
                )
            self.log_time_stats(archived_rep)
            self.reset_variables()

        else:
            LOG.info(
                format_message(
                    self.prefix,
                    "Could not archive report because one or more associated slices" " are still being processed.",
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            self.reset_variables()

    @VALIDATION_LATENCY.time()
    def _validate_report_details(self):  # pylint: disable=too-many-locals
        """
        Verify that the report contents are a valid marketplace report.

        :returns: tuple contain list of valid and invalid hosts
        """
        self.prefix = "VALIDATE REPORT STRUCTURE"
        required_keys = ["report_slice_id"]

        missing_keys = []
        for key in required_keys:
            required_key = self.report_json.get(key)
            if not required_key:
                missing_keys.append(key)

        if missing_keys:
            missing_keys_str = ", ".join(missing_keys)
            raise MKTReportException(
                format_message(
                    self.prefix,
                    "Report is missing required fields: %s." % missing_keys_str,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )

        return True
