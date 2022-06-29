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
"""ReportConsumer class for saving & acking uploaded messages."""
import asyncio
import json
import logging
import threading
from datetime import datetime

import pytz
from asgiref.sync import sync_to_async
from confluent_kafka import Consumer
from kafka.errors import KafkaError
from prometheus_client import Counter

from api.models import Report
from api.serializers import ReportSerializer
from config.settings.base import INSIGHTS_KAFKA_ADDRESS
from config.settings.base import INSIGHTS_KAFKA_TOPIC
from config.settings.base import INSIGHTS_KAFKA_USERNAME
from config.settings.base import INSIGHTS_KAFKA_PASSWORD
from config.settings.base import INSIGHTS_KAFKA_SEC_PROT
from config.settings.base import INSIGHTS_KAFKA_SASL_MECH
from processor.processor_utils import format_message
from processor.processor_utils import PROCESSOR_INSTANCES
from processor.processor_utils import stop_all_event_loops
from processor.processor_utils import UPLOAD_REPORT_CONSUMER_LOOP

LOG = logging.getLogger(__name__)

REPORT_PENDING_QUEUE = asyncio.Queue()
MKT_TOPIC = INSIGHTS_KAFKA_TOPIC

MSG_UPLOADS = Counter("marketplace_message_uploads", "Number of messages uploaded to mkt topic", ["account_number"])


KAFKA_ERRORS = Counter("marketplace_kafka_errors", "Number of Kafka errors")
DB_ERRORS = Counter("marketplace_db_errors", "Number of db errors")
OBJECTSTORE_ERRORS = Counter("marketplace_objectstore_errors", "Number of objectstore errors")
INVALID_UPLOADS = Counter("marketplace_invalid_uploads", "Number of invalid uploads")
NONJSON_UPLOADS = Counter("marketplace_invalid_content_uploads", "Number of uploads with invalid content")
UPLOAD_EXTRACT_RETRIES = Counter("marketplace_upload_extract_retries", "Number of retries errors extracting uploads")
UPLOAD_EXTRACT_FAILS = Counter("marketplace_upload_extract_failures", "Number of failures extracting uploads")


class MKTReportException(Exception):
    """Use to report errors during mkt report processing."""

    pass


class MKTKafkaMsgException(Exception):
    """Use to report errors with kafka message.

    Used when we think the kafka message is useful
    in debugging.  Error with external services
    (connected via kafka).
    """

    pass


class KafkaMsgHandlerError(Exception):
    """Kafka msg handler error."""

    pass


def get_consumer():
    """Create a Kafka consumer."""
    if INSIGHTS_KAFKA_SEC_PROT is None or INSIGHTS_KAFKA_SASL_MECH is None 
    or INSIGHTS_KAFKA_USERNAME is None or INSIGHTS_KAFKA_PASSWORD is None:
        consumer = Consumer(#old connection method
        {"bootstrap.servers": INSIGHTS_KAFKA_ADDRESS,
        "group.id": "mkt-group",
        "queued.max.messages.kbytes": 1024,
        "enable.auto.commit": False}, logger=LOG)
    else:
        consumer = Consumer(#new connection method
        {
        "bootstrap.servers": INSIGHTS_KAFKA_ADDRESS,
        "group.id": "mkt-group",
        "queued.max.messages.kbytes": 1024,
        "enable.auto.commit": False
        "security_protocol": INSIGHTS_KAFKA_SEC_PROT,
        "sasl_mechanism": INSIGHTS_KAFKA_SASL_MECH,
        "sasl_plain_username": INSIGHTS_KAFKA_USERNAME,
        "sasl_plain_password": INSIGHTS_KAFKA_PASSWORD
        },
        logger=LOG
        )
    consumer.subscribe([MKT_TOPIC])
    return consumer


class ReportConsumer:
    """Class for saving and acking uploaded reports."""

    def __init__(self):
        """Create a report consumer."""
        self.should_run = True
        self.prefix = "REPORT CONSUMER"
        self.account_number = None
        self.upload_message = None
        self.consumer = None

    @KAFKA_ERRORS.count_exceptions()
    def run(self, loop):
        """Worker thread function to run the asyncio event loop.

        :param None
        :returns None
        """
        loop.create_task(self.loop_save_message_and_ack())

        try:
            log_message = "Upload report listener started.  Waiting for messages..."
            loop.run_until_complete(self.listen_for_messages(REPORT_PENDING_QUEUE, log_message))
        except KafkaMsgHandlerError as err:
            KAFKA_ERRORS.inc()
            LOG.info("Stopping kafka worker thread.  Error: %s", str(err))
        except Exception:  # pylint: disable=broad-except
            pass

    async def loop_save_message_and_ack(self):
        """Run the report consumer in a loop."""
        while self.should_run:
            consumer_record = await REPORT_PENDING_QUEUE.get()
            await self.save_message_and_ack(consumer_record)
            return

    async def save_message_and_ack(self, consumer_record):
        """Save and ack the uploaded kafka message."""
        self.prefix = "SAVING MESSAGE"
        if consumer_record.topic() == MKT_TOPIC:
            try:
                missing_fields = []
                self.upload_message = self.unpack_consumer_record(consumer_record)
                # rh_account is being deprecated so we use it as a backup if
                # account is not there
                rh_account = self.upload_message.get("rh_account")
                request_id = self.upload_message.get("request_id")
                self.account_number = self.upload_message.get("account", rh_account)
                if not self.account_number:
                    missing_fields.append("account")
                if not request_id:
                    missing_fields.append("request_id")
                if missing_fields:
                    raise MKTKafkaMsgException(
                        format_message(
                            self.prefix, "Message missing required field(s): %s." % ", ".join(missing_fields)
                        )
                    )
                try:
                    uploaded_report = {
                        "upload_srv_kafka_msg": json.dumps(self.upload_message),
                        "account": self.account_number,
                        "request_id": request_id,
                        "state": Report.NEW,
                        "state_info": json.dumps([Report.NEW]),
                        "last_update_time": datetime.now(pytz.utc),
                        "arrival_time": datetime.now(pytz.utc),
                        "retry_count": 0,
                    }
                    report_serializer = ReportSerializer(data=uploaded_report)
                    report_serializer.is_valid(raise_exception=True)
                    async_save = sync_to_async(report_serializer.save)
                    await async_save()
                    MSG_UPLOADS.labels(account_number=self.account_number).inc()
                    LOG.info(format_message(self.prefix, "Upload service message saved. Ready for processing."))
                    self.consumer.commit()
                except KafkaError as kerror:
                    KAFKA_ERRORS.inc()
                    LOG.error(
                        format_message(
                            self.prefix,
                            "The following error occurred while trying to save and " "commit the message: %s" % kerror,
                        )
                    )
                    stop_all_event_loops()
                except Exception as error:  # pylint: disable=broad-except
                    DB_ERRORS.inc()
                    LOG.error(
                        format_message(
                            self.prefix,
                            "The following error occurred while trying to save and " "commit the message: %s" % error,
                        )
                    )
                    stop_all_event_loops()
            except MKTKafkaMsgException as message_error:
                LOG.error(
                    format_message(
                        self.prefix, f"Error processing records.  Message: {consumer_record}, Error: {message_error}"
                    )
                )
                self.consumer.commit()
        else:
            LOG.debug(format_message(self.prefix, f"Message not on {MKT_TOPIC} topic: {consumer_record}"))

    def unpack_consumer_record(self, consumer_record):
        """Decode the uploaded message and return it in JSON format."""
        self.prefix = "NEW REPORT UPLOAD"
        try:
            json_message = json.loads(consumer_record.value().decode("utf-8"))
            message = "received on %s topic" % consumer_record.topic()
            # rh_account is being deprecated so we use it as a backup if
            # account is not there
            rh_account = json_message.get("rh_account")
            self.account_number = json_message.get("account", rh_account)
            request_id = json_message.get("request_id")
            LOG.info(format_message(self.prefix, message, account_number=self.account_number, request_id=request_id))
            LOG.debug(
                format_message(
                    self.prefix,
                    "Message: %s" % str(consumer_record),
                    account_number=self.account_number,
                    request_id=request_id,
                )
            )
            return json_message
        except ValueError:
            raise MKTKafkaMsgException(format_message(self.prefix, "Upload service message not JSON."))

    @KAFKA_ERRORS.count_exceptions()
    async def listen_for_messages(self, async_queue, log_message):
        """Listen for messages on the mkt topic.

        Once a message from one of these topics arrives, we add
        them to the passed in queue.
        :param consumer : Kafka consumer
        :returns None
        """
        if self.consumer is not None:
            self.consumer.close()
        self.consumer = get_consumer()

        LOG.info(log_message)
        try:
            # Consume messages
            while self.should_run:
                msg = self.consumer.poll(timeout=1.0)
                if msg is None:
                    continue
                if msg.error():
                    KAFKA_ERRORS.inc()
                    LOG.error(format_message(self.prefix, "The following error occurred: %s" % msg.error()))
                    stop_all_event_loops()
                    break
                await async_queue.put(msg)
                await self.loop_save_message_and_ack()
        except Exception as err:  # pylint: disable=broad-except
            KAFKA_ERRORS.inc()
            LOG.error(format_message(self.prefix, "The following error occurred: %s" % err))
            stop_all_event_loops()
        finally:
            # Will leave consumer group; perform autocommit if enabled.
            if self.consumer:
                self.consumer.close()


def create_upload_report_consumer_loop(loop):
    """Initialize the report consumer class and run."""
    report_consumer = ReportConsumer()
    PROCESSOR_INSTANCES.append(report_consumer)
    report_consumer.run(loop)


def initialize_upload_report_consumer():  # pragma: no cover
    """
    Create asyncio tasks and daemon thread to run event loop.

    :param None
    :returns None
    """
    event_loop_thread = threading.Thread(
        target=create_upload_report_consumer_loop, args=(UPLOAD_REPORT_CONSUMER_LOOP,)
    )
    event_loop_thread.daemon = True
    event_loop_thread.start()
