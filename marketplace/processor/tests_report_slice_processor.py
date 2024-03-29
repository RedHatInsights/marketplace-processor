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
"""Tests the upload message report processor."""
import asyncio
import json
import uuid
from datetime import datetime
from unittest import IsolatedAsyncioTestCase
from unittest.mock import Mock
from unittest.mock import patch

import pytz
from django import db
from prometheus_client import REGISTRY

from api.models import Report
from api.models import ReportArchive
from api.models import ReportSlice
from api.models import ReportSliceArchive
from processor import report_consumer as msg_handler
from processor import report_slice_processor
from processor import tests_report_consumer as test_handler

# from django.test import TestCase


# pylint: disable=too-many-public-methods
# pylint: disable=protected-access,too-many-lines,too-many-instance-attributes
class ReportSliceProcessorTests(IsolatedAsyncioTestCase):
    """Test Cases for the Message processor."""

    def setUp(self):
        """Create test setup."""
        self.payload_url = "http://insights-upload.com/q/file_to_validate"
        self.uuid = uuid.uuid4()
        self.uuid2 = uuid.uuid4()
        self.uuid3 = uuid.uuid4()
        self.uuid4 = uuid.uuid4()
        self.uuid5 = uuid.uuid4()
        self.uuid6 = uuid.uuid4()
        self.uuid7 = uuid.uuid4()
        self.fake_record = test_handler.KafkaMsg(msg_handler.MKT_TOPIC, "http://internet.com")
        self.report_consumer = msg_handler.ReportConsumer()
        self.msg = self.report_consumer.unpack_consumer_record(self.fake_record)
        self.report_json = {
            "report_id": 1,
            "report_type": "insights",
            "status": "completed",
            "report_platform_id": "5f2cc1fd-ec66-4c67-be1b-171a595ce319",
        }
        self.report_record = Report(
            upload_srv_kafka_msg=json.dumps(self.msg),
            account="1234",
            state=Report.NEW,
            state_info=json.dumps([Report.NEW]),
            last_update_time=datetime.now(pytz.utc),
            retry_count=0,
            ready_to_archive=False,
            source=uuid.uuid4(),
            arrival_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc),
        )
        self.report_record.save()

        self.report_slice = ReportSlice(
            report_platform_id=self.uuid,
            report_slice_id=self.uuid2,
            account="13423",
            report_json=json.dumps(self.report_json),
            state=ReportSlice.NEW,
            state_info=json.dumps([ReportSlice.NEW]),
            retry_count=0,
            last_update_time=datetime.now(pytz.utc),
            report=self.report_record,
            ready_to_archive=True,
            source=uuid.uuid4(),
            creation_time=datetime.now(pytz.utc),
            processing_start_time=datetime.now(pytz.utc),
        )
        self.report_slice.save()
        self.report_record.save()
        self.processor = report_slice_processor.ReportSliceProcessor()
        self.processor.report = self.report_slice

    def tearDown(self):
        self.report_slice.delete()
        self.report_record.delete()
        db.connections.close_all()

    def check_variables_are_reset(self):
        """Check that report processor members have been cleared."""
        processor_attributes = [
            self.processor.report_platform_id,
            self.processor.report,
            self.processor.state,
            self.processor.account_number,
            self.processor.upload_message,
            self.processor.status,
            self.processor.report_json,
        ]
        for attribute in processor_attributes:
            self.assertEqual(attribute, None)

    def test_assign_report_slice_new(self):
        """Test the assign report slice function with only a new report slice."""
        self.report_slice.state = ReportSlice.NEW
        self.report_slice.save()
        self.processor.report_or_slice = None
        self.processor.assign_object()
        self.assertEqual(self.processor.report_or_slice, self.report_slice)
        queued_slices = REGISTRY.get_sample_value("queued_report_slices")
        self.assertEqual(queued_slices, 1)

    def test_update_slice_state(self):
        """Test updating the slice state."""
        self.report_slice.save()
        report_json = {
            "report_id": 1,
            "report_type": "deployments",
            "status": "completed",
            "report_platform_id": "5f2cc1fd-ec66-4c67-be1b-171a595ce319",
        }
        self.processor.report_or_slice = self.report_slice
        self.processor.next_state = ReportSlice.VALIDATED
        options = {"report_json": report_json}
        self.processor.update_object_state(options=options)
        self.assertEqual(json.loads(self.report_slice.report_json), report_json)

    def test_transition_to_validated_general_exception(self):
        """Test that when a general exception is raised, we don't pass validation."""
        self.report_slice.state = ReportSlice.RETRY_VALIDATION
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice

        def validate_side_effect():
            """Transition the state to downloaded."""
            raise Exception("Test")

        with patch(
            "processor.report_slice_processor." "ReportSliceProcessor._validate_report_details",
            side_effect=validate_side_effect,
        ):
            self.processor.transition_to_validated()
            self.assertEqual(self.report_slice.state, ReportSlice.RETRY_VALIDATION)
            self.assertEqual(self.report_slice.retry_count, 1)

    def test_transition_to_validated(self):
        """Test that when a general exception is raised, we don't pass validation."""
        self.report_slice.state = ReportSlice.RETRY_VALIDATION
        report_json = {"report_slice_id": "384794738"}
        self.report_slice.report_json = json.dumps(report_json)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.transition_to_validated()
        self.assertEqual(self.report_slice.state, ReportSlice.VALIDATED)
        self.assertEqual(self.report_slice.retry_count, 0)

    def test_transition_to_validated_failed(self):
        """Test report missing slice id."""
        self.report_slice.state = ReportSlice.RETRY_VALIDATION
        report_json = {
            "report_id": 1,
            "status": "completed",
            "report_platform_id": "5f2cc1fd-ec66-4c67-be1b-171a595ce319",
        }
        self.report_slice.report_json = json.dumps(report_json)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.transition_to_validated()
        self.assertEqual(self.report_slice.state, ReportSlice.FAILED_VALIDATION)
        self.assertEqual(self.report_slice.retry_count, 0)
        self.assertEqual(self.report_slice.ready_to_archive, True)

    def test_determine_retry_limit(self):
        """Test the determine retry method when the retry is at the limit."""
        self.report_slice.state = ReportSlice.VALIDATED
        self.report_slice.retry_count = 4
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.determine_retry(ReportSlice.FAILED_METRICS_UPLOAD, ReportSlice.VALIDATED)
        self.assertEqual(self.report_slice.state, ReportSlice.FAILED_METRICS_UPLOAD)

    def test_archive_report_and_slices_in_failed_state(self):
        """Test the archive method in a failed state."""
        report_archives = ReportArchive.objects.all()
        for report_archive in report_archives:
            report_archive.delete()
        self.report_record.ready_to_archive = True
        self.report_record.report_platform_id = str(self.uuid)
        self.report_record.save()
        self.report_slice.ready_to_archive = True
        self.report_slice.report_platform_id = str(self.uuid)
        self.report_slice.report_slice_id = str(self.uuid2)
        self.report_slice.state = ReportSlice.FAILED_METRICS_UPLOAD
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.report_platform_id = str(self.uuid)

        self.processor.archive_report_and_slices()
        # assert the report doesn't exist
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=self.report_record.id)
        # assert the report archive does exist
        archived = ReportArchive.objects.get(account=self.report_record.account)
        archived_slice = ReportSliceArchive.objects.get(report_slice_id=self.report_slice.report_slice_id)
        self.assertEqual(str(archived.report_platform_id), str(self.uuid))
        self.assertEqual(str(archived_slice.report_platform_id), str(self.uuid))
        self.assertIsNotNone(archived_slice.processing_end_time)
        archived_slice.delete()
        archived.delete()
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_archive_report_and_slices_in_success_state(self):
        """Test the archive method in a failed state."""
        report_archives = ReportArchive.objects.all()
        for report_archive in report_archives:
            report_archive.delete()
        self.report_record.ready_to_archive = True
        self.report_record.report_platform_id = str(self.uuid)
        self.report_record.save()
        self.report_slice.ready_to_archive = True
        self.report_slice.report_platform_id = str(self.uuid)
        self.report_slice.report_slice_id = str(self.uuid2)
        self.report_slice.state = ReportSlice.METRICS_UPLOADED
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.report_platform_id = str(self.uuid)

        self.processor.archive_report_and_slices()
        # assert the report doesn't exist
        with self.assertRaises(Report.DoesNotExist):
            Report.objects.get(id=self.report_record.id)
        # assert the report archive does exist
        archived = ReportArchive.objects.get(account=self.report_record.account)
        archived_slice = ReportSliceArchive.objects.get(report_slice_id=self.report_slice.report_slice_id)
        self.assertEqual(str(archived.report_platform_id), str(self.uuid))
        self.assertEqual(str(archived_slice.report_platform_id), str(self.uuid))
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_archive_report_and_slices_not_ready(self):
        """Test the archive method with slice not ready."""
        archives = ReportArchive.objects.filter(account=self.report_record.account)
        for archive in archives:
            archive.delete()
        self.report_record.ready_to_archive = True
        self.report_record.report_platform_id = str(self.uuid)
        self.report_record.save()
        self.report_slice.ready_to_archive = False
        self.report_slice.report_platform_id = str(self.uuid)
        self.report_slice.report_slice_id = str(self.uuid2)
        self.report_slice.save()
        self.processor.report_or_slice = self.report_slice
        self.processor.report_platform_id = str(self.uuid)

        self.processor.archive_report_and_slices()
        # assert the report doesn't exist
        existing = Report.objects.get(id=self.report_record.id)
        # assert the report archive does exist
        with self.assertRaises(ReportArchive.DoesNotExist):
            ReportArchive.objects.get(account=self.report_record.account)
        with self.assertRaises(ReportSliceArchive.DoesNotExist):
            ReportSliceArchive.objects.get(report_slice_id=self.report_slice.report_slice_id)
        self.assertEqual(str(existing.report_platform_id), str(self.uuid))
        # assert the processor was reset
        self.check_variables_are_reset()

    def test_get_minio_client_not_configured(self):
        """Test getting minio client when not configured."""
        report_slice_processor.MINIO_ENDPOINT = None
        report_slice_processor.MINIO_ACCESS_KEY = None
        report_slice_processor.MINIO_SECRET_KEY = None
        processor = report_slice_processor.ReportSliceProcessor()
        minio_client = processor.get_minio_client()
        self.assertIsNone(minio_client)

    def test_get_minio_client_configured(self):
        """Test get minio client when configured."""
        report_slice_processor.MINIO_ENDPOINT = "minio:9001"
        report_slice_processor.MINIO_ACCESS_KEY = "access"
        report_slice_processor.MINIO_SECRET_KEY = "secret"
        processor = report_slice_processor.ReportSliceProcessor()
        minio_client = processor.get_minio_client()
        self.assertIsNotNone(minio_client)

        # call again for branch path where already created
        self.assertIsNotNone(processor.get_minio_client())

    def test_upload_to_object_storage_none_client(self):
        """Test error raised when client is not configured."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_upload_to_objectstore_none_client)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_upload_to_objectstore_none_client(self):
        """Async setup for none client test."""
        report_slice_processor.MINIO_ENDPOINT = None
        report_slice_processor.MINIO_ACCESS_KEY = None
        report_slice_processor.MINIO_SECRET_KEY = None
        with self.assertRaises(report_slice_processor.RetryUploadTimeException):
            await self.processor._upload_to_object_storage()

    def test_upload_to_object_storage_no_bucket(self):
        """Test error raised when bucket does not exist."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_upload_to_objectstore_no_bucket)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_upload_to_objectstore_no_bucket(self):
        """Async setup for no bucket test."""
        mock_minio = Mock()
        mock_minio.bucket_exists.return_value = False
        with patch(
            "processor.report_slice_processor." "ReportSliceProcessor.get_minio_client", return_value=mock_minio
        ):
            with self.assertRaises(report_slice_processor.RetryUploadTimeException):
                await self.processor._upload_to_object_storage()

    def test_upload_to_object_storage_upload_error(self):
        """Test error raised an upload error occurs."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_upload_to_objectstore_error)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_upload_to_objectstore_error(self):
        """Async setup for upload error test."""
        mock_minio = Mock()
        mock_minio.bucket_exists.return_value = True

        # test KafkaConnectionException
        def raise_error():
            """Raise a general error."""
            raise Exception("Test")

        mock_minio.fput_object.side_effect = raise_error
        with patch(
            "processor.report_slice_processor." "ReportSliceProcessor.get_minio_client", return_value=mock_minio
        ):
            with self.assertRaises(Exception):
                await self.processor._upload_to_object_storage()

    def test_upload_to_object_storage_upload_success(self):
        """Test upload success pass."""
        event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(event_loop)
        coro = asyncio.coroutine(self.async_test_upload_to_objectstore)
        event_loop.run_until_complete(coro())
        event_loop.close()

    async def async_test_upload_to_objectstore(self):
        """Async setup for upload error test."""
        mock_minio = Mock()
        mock_minio.bucket_exists.return_value = True
        mock_minio.fput_object.return_value = True
        report_json = {"report_slice_id": "384794738"}
        self.processor.report_or_slice = self.report_slice
        self.processor.report_or_slice.report_json = json.dumps(report_json)
        with patch(
            "processor.report_slice_processor." "ReportSliceProcessor.get_minio_client", return_value=mock_minio
        ):
            try:
                await self.processor._upload_to_object_storage()
            except Exception as err:  # pylint: disable=broad-except
                self.fail(f"Unexpected exception {err}")
