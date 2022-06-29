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
"""Report Processor."""
import asyncio
import json
import logging
import tarfile
import threading
from datetime import datetime
from http import HTTPStatus
from io import BytesIO
from threading import Thread

import pytz
import requests
from asgiref.sync import sync_to_async
from confluent_kafka import KafkaException
from confluent_kafka import Producer
from django.db import transaction

from api.models import Report
from api.models import ReportSlice
from api.models import Status
from api.serializers import ReportSerializer
from api.serializers import ReportSliceSerializer
from config.settings.base import INSIGHTS_KAFKA_ADDRESS
from config.settings.base import INSIGHTS_KAFKA_USERNAME
from config.settings.base import INSIGHTS_KAFKA_PASSWORD
from config.settings.base import INSIGHTS_KAFKA_SEC_PROT
from config.settings.base import INSIGHTS_KAFKA_SASL_MECH
from config.settings.base import RETRIES_ALLOWED
from config.settings.base import RETRY_TIME
from processor.abstract_processor import AbstractProcessor
from processor.abstract_processor import DOWNLOAD_REPORTS
from processor.abstract_processor import FAILED_TO_DOWNLOAD
from processor.abstract_processor import FAILED_TO_VALIDATE
from processor.abstract_processor import RETRY
from processor.processor_utils import format_message
from processor.processor_utils import PROCESSOR_INSTANCES
from processor.processor_utils import REPORT_PROCESSING_LOOP
from processor.processor_utils import stop_all_event_loops
from processor.report_consumer import DB_ERRORS
from processor.report_consumer import INVALID_UPLOADS
from processor.report_consumer import KAFKA_ERRORS
from processor.report_consumer import MKTReportException
from processor.report_consumer import NONJSON_UPLOADS
from processor.report_consumer import UPLOAD_EXTRACT_FAILS
from processor.report_consumer import UPLOAD_EXTRACT_RETRIES

LOG = logging.getLogger(__name__)
VALIDATION_TOPIC = "platform.upload.validation"
SUCCESS_CONFIRM_STATUS = "success"
FAILURE_CONFIRM_STATUS = "failure"
RETRIES_ALLOWED = int(RETRIES_ALLOWED)
RETRY_TIME = int(RETRY_TIME)


class FailDownloadException(Exception):
    """Use to report download errors that should not be retried."""

    pass


class RetryDownloadException(Exception):
    """Use to report download errors that should be retried."""

    pass


class FailExtractException(Exception):
    """Use to report extract errors that should not be retried."""

    pass


class RetryExtractException(Exception):
    """Use to report extract errors that should be retried."""

    pass


class AIOProducer:
    """Class for handing asyncio messages"""

    def __init__(self, configs, loop=None):
        self._loop = loop or asyncio.get_event_loop()
        self._producer = Producer(configs)
        self._cancelled = False
        self._poll_thread = Thread(target=self._poll_loop)
        self._poll_thread.start()

    def _poll_loop(self):
        while not self._cancelled:
            self._producer.poll(0.1)

    def close(self):
        self._cancelled = True
        self._poll_thread.join()

    def send_and_wait(self, topic, value):
        """
        An awaitable produce method.
        """
        result = self._loop.create_future()

        def ack(err, msg):
            if err:
                self._loop.call_soon_threadsafe(result.set_exception, KafkaException(err))
            else:
                self._loop.call_soon_threadsafe(result.set_result, msg)

        self._producer.produce(topic, value, on_delivery=ack)
        return result

    def send_with_callback(self, topic, value, on_delivery):
        """
        A produce method in which delivery notifications are made available
        via both the returned future and on_delivery callback (if specified).
        """
        result = self._loop.create_future()

        def ack(err, msg):
            if err:
                self._loop.call_soon_threadsafe(result.set_exception, KafkaException(err))
            else:
                self._loop.call_soon_threadsafe(result.set_result, msg)
            if on_delivery:
                self._loop.call_soon_threadsafe(on_delivery, err, msg)

        self._producer.produce(topic, value, on_delivery=ack)
        return result


class ReportProcessor(AbstractProcessor):  # pylint: disable=too-many-instance-attributes
    """Class for processing report that have been created."""

    def __init__(self):
        """Create a report processor."""
        state_functions = {
            Report.NEW: self.transition_to_started,
            Report.STARTED: self.transition_to_downloaded,
            Report.DOWNLOADED: self.transition_to_validated,
            Report.VALIDATED: self.transition_to_validation_reported,
            Report.VALIDATION_REPORTED: self.archive_report_and_slices,
            Report.FAILED_DOWNLOAD: self.archive_report_and_slices,
            Report.FAILED_VALIDATION: self.archive_report_and_slices,
            Report.FAILED_VALIDATION_REPORTING: self.archive_report_and_slices,
        }
        state_metrics = {Report.FAILED_DOWNLOAD: FAILED_TO_DOWNLOAD, Report.FAILED_VALIDATION: FAILED_TO_VALIDATE}
        self.async_states = [Report.STARTED, Report.VALIDATED]
        self.producer = None
        super().__init__(
            pre_delegate=self.pre_delegate,
            state_functions=state_functions,
            state_metrics=state_metrics,
            async_states=self.async_states,
            object_prefix="REPORT",
            object_class=Report,
            object_serializer=ReportSerializer,
        )

    def pre_delegate(self):
        """Call the correct function based on report state.

        If the function is async, make sure to await it.
        """
        self.state = self.report_or_slice.state
        self.account_number = self.report_or_slice.account
        self.upload_message = json.loads(self.report_or_slice.upload_srv_kafka_msg)
        if self.report_or_slice.report_platform_id:
            self.report_platform_id = self.report_or_slice.report_platform_id
        if self.report_or_slice.upload_ack_status:
            self.status = self.report_or_slice.upload_ack_status

    async def transition_to_downloaded(self):
        """Attempt to download report, extract json, and create slices.

        As long as we have one valid slice, we set the status to success.
        """
        self.prefix = "ATTEMPTING DOWNLOAD"
        report_download_failed_msg = "The report could not be downloaded due to the following error: %s."
        LOG.info(
            format_message(
                self.prefix,
                "Attempting to download the report and extract the json. "
                'State is "%s".' % self.report_or_slice.state,
                account_number=self.account_number,
            )
        )
        try:
            report_tar_gz = self._download_report()
            options = await self._extract_and_create_slices(report_tar_gz)
            self.next_state = Report.DOWNLOADED
            # update the report or slice with downloaded info
            async_update = sync_to_async(self.update_object_state)(options=options)
            await async_update
            # self.deduplicate_reports()
            DOWNLOAD_REPORTS.labels(account_number=self.account_number).inc()
        except (FailDownloadException, FailExtractException) as err:
            LOG.error(
                format_message(self.prefix, report_download_failed_msg % err, account_number=self.account_number)
            )
            self.next_state = Report.FAILED_DOWNLOAD
            options = {"ready_to_archive": True}
            async_update = sync_to_async(self.update_object_state)(options=options)
            await async_update
        except (RetryDownloadException, RetryExtractException) as err:
            LOG.error(
                format_message(self.prefix, report_download_failed_msg % err, account_number=self.account_number)
            )
            self.determine_retry(Report.FAILED_DOWNLOAD, Report.STARTED)

    @DB_ERRORS.count_exceptions()
    def transition_to_validated(self):
        """Validate that the report contents & move to validated state."""
        self.prefix = "ATTEMPTING VALIDATE"
        LOG.info(
            format_message(
                self.prefix,
                'Validating the report contents. State is "%s".' % self.report_or_slice.state,
                account_number=self.account_number,
            )
        )
        # find all associated report slices
        report_slices = ReportSlice.objects.filter(report=self.report_or_slice)
        self.status = SUCCESS_CONFIRM_STATUS
        for report_slice in report_slices:
            try:
                self.report_json = json.loads(report_slice.report_json)
                self._validate_report_details()
                # Here we want to update the report state of the actual report slice
                options = {"state": ReportSlice.NEW}
                self.update_slice_state(options=options, report_slice=report_slice)
            except MKTReportException:
                # if any MKTReportExceptions occur, we know that the report is not valid
                # but has been successfully validated
                # that means that this slice is invalid and only awaits being archived
                self.status = FAILURE_CONFIRM_STATUS
                options = {"state": ReportSlice.FAILED_VALIDATION, "ready_to_archive": True}
                self.update_slice_state(options=options, report_slice=report_slice)
            except Exception as error:  # pylint: disable=broad-except
                # This slice blew up validation - we want to retry it later,
                # which means it enters our odd state of retrying validation
                LOG.error(format_message(self.prefix, "The following error occurred: %s." % str(error)))
                self.status = FAILURE_CONFIRM_STATUS
                options = {"state": ReportSlice.RETRY_VALIDATION, "retry": RETRY.increment}
                self.update_slice_state(options=options, report_slice=report_slice)
        if self.status == "failure":
            LOG.warning(
                format_message(
                    self.prefix,
                    'The uploaded report was invalid. Status set to "%s".' % self.status,
                    account_number=self.account_number,
                )
            )
        self.next_state = Report.VALIDATED
        options = {"status": self.status}
        self.update_object_state(options=options)

    async def transition_to_validation_reported(self):
        """Upload the validation status & move to validation reported state."""
        self.prefix = "ATTEMPTING STATUS UPLOAD"
        LOG.info(
            format_message(
                self.prefix,
                f'Uploading validation status "{self.status}". State is "{self.state}".',
                account_number=self.account_number,
                report_platform_id=self.report_platform_id,
            )
        )
        try:
            message_hash = self.upload_message["request_id"]
            await self._send_confirmation(message_hash)
            self.next_state = Report.VALIDATION_REPORTED
            options = {"ready_to_archive": True}
            async_update = sync_to_async(self.update_object_state)(options=options)
            await async_update
            LOG.info(
                format_message(
                    self.prefix,
                    "Status successfully uploaded.",
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            if self.status == FAILURE_CONFIRM_STATUS:
                options = {"retry": RETRY.keep_same, "ready_to_archive": True}
                async_update = sync_to_async(self.update_object_state)(options=options)
                await async_update
                async_archive = sync_to_async(self.archive_report_and_slices)
                await async_archive()
        except Exception as error:  # pylint: disable=broad-except
            LOG.error(
                format_message(
                    self.prefix,
                    "The following error occurred: %s." % str(error),
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            await sync_to_async(self.determine_retry)(Report.FAILED_VALIDATION_REPORTING, Report.VALIDATED)

    @DB_ERRORS.count_exceptions()
    def create_report_slice(self, options):
        """Create report slice.

        :param report_json: <dict> the report info in json format
        :param report_slice_id: <str> the report slice id
        :returns boolean regarding whether or not the slice was created.
        """
        report_json = options.get("report_json")
        report_slice_id = options.get("report_slice_id")
        source = options.get("source")
        source_metadata = options.get("source_metadata")
        LOG.info(
            format_message(
                self.prefix,
                "Creating report slice %s" % report_slice_id,
                account_number=self.account_number,
                report_platform_id=self.report_platform_id,
            )
        )

        # first we should see if any slices exist with this slice id & report_platform_id
        # if they exist we will not create the slice
        created = False
        existing_report_slices = ReportSlice.objects.filter(
            report_platform_id=self.report_platform_id, report_slice_id=report_slice_id
        )

        if existing_report_slices.count() > 0:
            LOG.error(
                format_message(
                    self.prefix,
                    "a report slice with the report_platform_id %s and report_slice_id %s "
                    "already exists." % (self.report_platform_id, report_slice_id),
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            return created

        report_slice = {
            "state": ReportSlice.PENDING,
            "account": self.account_number,
            "state_info": json.dumps([ReportSlice.PENDING]),
            "last_update_time": datetime.now(pytz.utc),
            "retry_count": 0,
            "report_json": json.dumps(report_json),
            "report_platform_id": self.report_platform_id,
            "report_slice_id": report_slice_id,
            "report": self.report_or_slice.id,
            "source": source,
            "source_metadata": json.dumps(source_metadata),
            "creation_time": datetime.now(pytz.utc),
        }
        slice_serializer = ReportSliceSerializer(data=report_slice)
        if slice_serializer.is_valid(raise_exception=True):
            slice_serializer.save()
            LOG.info(
                format_message(
                    self.prefix,
                    "Successfully created report slice %s" % report_slice_id,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
        return True

    # pylint: disable=too-many-arguments,too-many-locals
    def update_slice_state(self, options, report_slice):  # noqa: C901 (too-complex)
        """
        Update the report processor state and save.

        :param options: <dict> dictionary potentially containing the following:
            report_slice: <ReportSlice> the report slice to update
            state: <str> the state to update to
            retry: <enum> Retry.clear=clear count, RETRY.increment=increase count
            retry_type: <str> either time=retry after time,
                git_commit=retry after new commit
            report_json: <dict> dictionary containing the report json
            report_platform_id: <str> string containing report_platform_id
            ready_to_archive: <bool> boolean on whether or not to archive
        """
        try:
            state = options.get("state")
            retry_type = options.get("retry_type")
            retry = options.get("retry", RETRY.clear)
            ready_to_archive = options.get("ready_to_archive")
            status_info = Status()
            report_slice.last_update_time = datetime.now(pytz.utc)
            report_slice.state = state
            report_slice.git_commit = status_info.git_commit
            report_slice_data = {
                "last_update_time": datetime.now(pytz.utc),
                "state": state,
                "git_commit": status_info.git_commit,
            }
            if not retry_type:
                retry_type = ReportSlice.TIME
            if retry == RETRY.clear:
                # After a successful transaction when we have reached the update
                # point, we want to set the retry count back to 0 because
                # any future failures should be unrelated
                report_slice_data["retry_count"] = 0
                report_slice_data["retry_type"] = ReportSlice.TIME
            elif retry == RETRY.increment:
                current_count = report_slice.retry_count
                report_slice_data["retry_count"] = current_count + 1
                report_slice_data["retry_type"] = ReportSlice.TIME
            # the other choice for retry is RETRY.keep_same in which case we don't
            # want to do anything to the retry count bc we want to preserve as is
            if ready_to_archive:
                report_slice_data["ready_to_archive"] = ready_to_archive
            state_info = json.loads(report_slice.state_info)
            state_info.append(state)
            report_slice_data["state_info"] = json.dumps(state_info)
            serializer = ReportSliceSerializer(instance=report_slice, data=report_slice_data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            LOG.info(
                format_message(
                    self.prefix,
                    "Successfully updated report slice %s" % report_slice.report_slice_id,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
        except Exception as error:  # pylint: disable=broad-except
            DB_ERRORS.inc()
            self.should_run = False
            LOG.error(
                format_message(
                    self.prefix,
                    "Could not update report slice record due to the following error %s." % str(error),
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            stop_all_event_loops()

    def _download_report(self):
        """
        Download report.

        :returns content: The tar.gz binary content or None if there are errors.
        """
        self.prefix = "REPORT DOWNLOAD"
        try:
            report_url = self.upload_message.get("url", None)
            if not report_url:
                raise FailDownloadException(
                    format_message(
                        self.prefix,
                        "kafka message missing report url.  Message: %s" % self.upload_message,
                        account_number=self.account_number,
                    )
                )

            LOG.info(format_message(self.prefix, "downloading %s" % report_url, account_number=self.account_number))
            download_response = requests.get(report_url)
            if download_response.status_code != HTTPStatus.OK:
                raise RetryDownloadException(
                    format_message(
                        self.prefix,
                        "HTTP status code {} returned for URL {}.  Message: {}".format(
                            download_response.status_code, report_url, self.upload_message
                        ),
                        account_number=self.account_number,
                    )
                )

            LOG.info(
                format_message(
                    self.prefix, "successfully downloaded TAR %s" % report_url, account_number=self.account_number
                )
            )
            return download_response.content
        except FailDownloadException as fail_err:
            raise fail_err

        except requests.exceptions.HTTPError as err:
            raise RetryDownloadException(
                format_message(
                    self.prefix,
                    f"Unexpected http error for URL {report_url}. Error: {err}",
                    account_number=self.account_number,
                )
            )

        except Exception as err:
            raise RetryDownloadException(
                format_message(
                    self.prefix,
                    f"Unexpected error for URL {report_url}. Error: {err}",
                    account_number=self.account_number,
                )
            )

    async def validate_metadata_file(self, tar, metadata):  # noqa: C901 (too-complex)
        """Validate the contents of the metadata file.

        :param tar: the tarfile object.
        :param metadata: metadata file object.
        :returns: report_slice_ids
        """
        LOG.info(
            format_message(
                self.prefix,
                "Attempting to decode the file %s" % metadata.name,
                account_number=self.account_number,
                report_platform_id=self.report_platform_id,
            )
        )
        metadata_file = tar.extractfile(metadata)
        try:
            metadata_str = metadata_file.read().decode("utf-8")
        except UnicodeDecodeError as error:
            decode_error_message = (
                "Attempting to decode the file"
                " %s resulted in the following error: %s. Discarding file." % (metadata_file.name, error)
            )
            LOG.exception(
                format_message(
                    self.prefix,
                    decode_error_message,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            return {}
        LOG.info(
            format_message(
                self.prefix,
                "Successfully decoded the file %s" % metadata.name,
                account_number=self.account_number,
                report_platform_id=self.report_platform_id,
            )
        )
        metadata_json = json.loads(metadata_str)
        required_keys = ["report_id", "source", "report_slices"]
        missing_keys = []
        for key in required_keys:
            required_key = metadata_json.get(key)
            if not required_key:
                missing_keys.append(key)

        if missing_keys:
            missing_keys_str = ", ".join(missing_keys)
            raise FailExtractException(
                format_message(
                    self.prefix,
                    "Metadata is missing required fields: %s." % missing_keys_str,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )

        self.report_platform_id = metadata_json.get("report_id")
        source = metadata_json.get("source", "")
        # we should save the above information into the report object
        options = {"report_platform_id": self.report_platform_id, "source": source}

        source_metadata = metadata_json.get("source_metadata")
        # if source_metadata exists, we should log it
        if source_metadata:
            LOG.info(
                format_message(
                    self.prefix,
                    "The following source metadata was uploaded: %s" % source_metadata,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
            options["source_metadata"] = source_metadata
        self.next_state = Report.DOWNLOADED
        async_update = sync_to_async(self.update_object_state)(options)
        await async_update
        valid_slice_ids = {}
        report_slices = metadata_json.get("report_slices", {})
        for report_slice_id, _ in report_slices.items():
            valid_slice_ids[report_slice_id] = True
        return valid_slice_ids, options

    # pylint: disable=too-many-branches, too-many-statements
    async def _extract_and_create_slices(self, report_tar_gz):  # noqa: C901 (too-complex)
        """Extract metrics from tar.gz file.

        :param report_tar_gz: A hexstring or BytesIO tarball
            saved in memory with gzip compression.
        :returns: metrics as dict
        """
        self.prefix = "EXTRACT REPORT FROM TAR"
        try:  # pylint: disable=too-many-nested-blocks
            tar = tarfile.open(fileobj=BytesIO(report_tar_gz), mode="r:gz")
            files = tar.getmembers()
            json_files = []
            metadata_file = None
            for file in files:
                # First we need to Find the metadata file
                if "/metadata.json" in file.name or file.name == "metadata.json":
                    metadata_file = file
                # Next we want to add all .json files to our list
                elif ".json" in file.name:
                    json_files.append(file)
            if json_files and metadata_file:
                try:
                    valid_slice_ids, options = await self.validate_metadata_file(tar, metadata_file)
                    report_names = []
                    for report_id, _ in valid_slice_ids.items():
                        for file in json_files:
                            if report_id in file.name:
                                matches_metadata = True
                                mismatch_message = ""
                                report_slice = tar.extractfile(file)
                                LOG.info(
                                    format_message(
                                        self.prefix,
                                        "Attempting to decode the file %s" % file.name,
                                        account_number=self.account_number,
                                        report_platform_id=self.report_platform_id,
                                    )
                                )
                                try:
                                    report_slice_string = report_slice.read().decode("utf-8")
                                except UnicodeDecodeError as error:
                                    decode_error_message = (
                                        "Attempting to decode the file"
                                        " %s resulted in the following error: %s. "
                                        "Discarding file." % (file.name, error)
                                    )
                                    LOG.exception(
                                        format_message(
                                            self.prefix,
                                            decode_error_message,
                                            account_number=self.account_number,
                                            report_platform_id=self.report_platform_id,
                                        )
                                    )
                                    continue
                                LOG.info(
                                    format_message(
                                        self.prefix,
                                        "Successfully decoded the file %s" % file.name,
                                        account_number=self.account_number,
                                        report_platform_id=self.report_platform_id,
                                    )
                                )
                                report_slice_json = json.loads(report_slice_string)
                                report_slice_id = report_slice_json.get("report_slice_id", "")
                                if report_slice_id != report_id:
                                    matches_metadata = False
                                    invalid_report_id = (
                                        "Metadata & filename reported the "
                                        '"report_slice_id" as %s but the "report_slice_id" '
                                        "inside the JSON has a value of %s. " % (report_id, report_slice_id)
                                    )
                                    mismatch_message += invalid_report_id
                                if not matches_metadata:
                                    mismatch_message += (
                                        "Metadata must match report slice data. "
                                        "Discarding the report slice as invalid. "
                                    )
                                    LOG.warning(
                                        format_message(
                                            self.prefix,
                                            mismatch_message,
                                            account_number=self.account_number,
                                            report_platform_id=self.report_platform_id,
                                        )
                                    )
                                    continue
                                slice_options = {
                                    "report_json": report_slice_json,
                                    "report_slice_id": report_slice_id,
                                    "source": options.get("source"),
                                    "source_metadata": options.get("source_metadata", {}),
                                }
                                async_rs = sync_to_async(self.create_report_slice)(slice_options)
                                created = await async_rs
                                if created:
                                    report_names.append(report_id)

                    if not report_names:
                        raise FailExtractException(
                            format_message(
                                self.prefix,
                                "Report contained no valid report slices.",
                                account_number=self.account_number,
                            )
                        )
                    LOG.info(
                        format_message(
                            self.prefix,
                            "successfully extracted & created report slices",
                            account_number=self.account_number,
                            report_platform_id=self.report_platform_id,
                        )
                    )
                    return options

                except ValueError as error:
                    NONJSON_UPLOADS.inc()
                    raise FailExtractException(
                        format_message(
                            self.prefix,
                            "Report is not valid JSON. Error: %s" % str(error),
                            account_number=self.account_number,
                        )
                    )
            INVALID_UPLOADS.inc()
            raise FailExtractException(
                format_message(
                    self.prefix,
                    "Tar does not contain valid JSON metadata & report files.",
                    account_number=self.account_number,
                )
            )
        except FailExtractException as mkt_err:
            raise mkt_err
        except tarfile.ReadError as err:
            UPLOAD_EXTRACT_FAILS.inc()
            raise FailExtractException(
                format_message(
                    self.prefix, "Unexpected error reading tar.gz: %s" % str(err), account_number=self.account_number
                )
            )
        except Exception as err:
            UPLOAD_EXTRACT_RETRIES.inc()
            raise RetryExtractException(
                format_message(
                    self.prefix, "Unexpected error reading tar.gz: %s" % str(err), account_number=self.account_number
                )
            )

    @KAFKA_ERRORS.count_exceptions()
    async def _send_confirmation(self, file_hash):  # pragma: no cover
        """
        Send kafka validation message to Insights Upload service.

        When a new file lands for topic 'mkt' we must validate it
        so that it will be made permanently available to other
        apps listening on the 'available' topic.
        :param: file_hash (String): Hash for file being confirmed.
        :returns None
        """
        self.prefix = "REPORT VALIDATION STATE ON KAFKA"
        if self.producer is not None:
            self.producer.close()
        self.producer = AIOProducer(
            {"bootstrap.servers": INSIGHTS_KAFKA_ADDRESS, 
            "message.timeout.ms": 1000, 
            "security_protocol": INSIGHTS_KAFKA_SEC_PROT,
            "sasl_mechanism": INSIGHTS_KAFKA_SASL_MECH,
            "sasl_plain_username": INSIGHTS_KAFKA_USERNAME,
            "sasl_plain_password": INSIGHTS_KAFKA_PASSWORD}, loop=REPORT_PROCESSING_LOOP
        )
        try:
            validation = {"hash": file_hash, "request_id": self.report_or_slice.request_id, "validation": self.status}
            msg = bytes(json.dumps(validation), "utf-8")
            await self.producer.send_and_wait(VALIDATION_TOPIC, msg)
            LOG.info(
                format_message(
                    self.prefix,
                    "Send %s validation status to file upload on kafka" % self.status,
                    account_number=self.account_number,
                    report_platform_id=self.report_platform_id,
                )
            )
        except Exception as err:  # pylint: disable=broad-except
            KAFKA_ERRORS.inc()
            LOG.error(format_message(self.prefix, "The following error occurred: %s" % err))
            stop_all_event_loops()
        finally:
            self.producer.close()

    @DB_ERRORS.count_exceptions()
    @transaction.atomic
    def deduplicate_reports(self):
        """If a report with the same id already exists, archive the new report."""
        try:
            existing_reports = Report.objects.filter(report_platform_id=self.report_platform_id)
            if existing_reports.count() > 1:
                LOG.error(
                    format_message(
                        self.prefix,
                        "a report with the report_platform_id %s already exists."
                        % self.report_or_slice.report_platform_id,
                        account_number=self.account_number,
                        report_platform_id=self.report_platform_id,
                    )
                )
                self.archive_report_and_slices()
        except Report.DoesNotExist:
            pass


def asyncio_report_processor_thread(loop):  # pragma: no cover
    """
    Worker thread function to run the asyncio event loop.

    Creates a report processor and calls the run method.

    :param loop: event loop
    :returns None
    """
    processor = ReportProcessor()
    PROCESSOR_INSTANCES.append(processor)
    try:
        loop.run_until_complete(processor.run())
    except Exception:  # pylint: disable=broad-except
        pass


def initialize_report_processor():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    Calls the report processor thread.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(target=asyncio_report_processor_thread, args=(REPORT_PROCESSING_LOOP,))
    event_loop_thread.daemon = True
    event_loop_thread.start()
