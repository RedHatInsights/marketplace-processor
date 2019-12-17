#
# Copyright (c) 2019 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public License,
# version 3 (GPLv3). There is NO WARRANTY for this software, express or
# implied, including the implied warranties of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. You should have received a copy of GPLv3
# along with this software; if not, see
# https://www.gnu.org/licenses/gpl-3.0.txt.
#

"""Model for report slice progress."""

from django.db import models

from api.report.model import Report, ReportArchive


class AbstractReportSlice(models.Model):
    """Represents report slice information."""

    report_platform_id = models.UUIDField(null=True)
    report_slice_id = models.UUIDField(null=True)
    account = models.CharField(max_length=50, null=True)
    report_json = models.TextField(null=True)
    git_commit = models.CharField(max_length=50, null=True)
    ready_to_archive = models.BooleanField(null=False, default=False)
    source = models.UUIDField(null=True)
    source_metadata = models.TextField(null=True)

    PENDING = 'pending'
    NEW = 'new'
    RETRY_VALIDATION = 'retry_validation'
    FAILED_VALIDATION = 'failed_validation'
    VALIDATED = 'validated'
    STARTED = 'started'
    METRICS_UPLOADED = 'hosts uploaded'
    FAILED_METRICS_UPLOAD = 'failed to upload metrics'
    STATE_CHOICES = ((PENDING, PENDING),
                     (NEW, NEW),
                     (RETRY_VALIDATION, RETRY_VALIDATION),
                     (FAILED_VALIDATION, FAILED_VALIDATION),
                     (VALIDATED, VALIDATED),
                     (STARTED, STARTED),
                     (METRICS_UPLOADED, METRICS_UPLOADED),
                     (FAILED_METRICS_UPLOAD, FAILED_METRICS_UPLOAD))

    state = models.CharField(
        max_length=28,
        choices=STATE_CHOICES,
        default=NEW
    )

    TIME = 'time'
    GIT_COMMIT = 'git commit'
    RETRY_CHOICES = ((TIME, TIME),
                     (GIT_COMMIT, GIT_COMMIT))

    retry_type = models.CharField(
        max_length=10,
        choices=RETRY_CHOICES,
        default=TIME
    )

    state_info = models.TextField(null=True)
    retry_count = models.PositiveSmallIntegerField(null=True)
    last_update_time = models.DateTimeField(null=True)
    creation_time = models.DateTimeField(null=True)
    processing_start_time = models.DateTimeField(null=True)
    processing_end_time = models.DateTimeField(null=True)

    def __str__(self):
        """Convert to string."""
        return '{' + 'report_platform_id:{}, '\
            'report_slice_id: {}, '\
            'account: {}, ' \
            'report_json: {}, '\
            'git_commit: {}, '\
            'ready_to_archive: {}, '\
            'source: {}, '\
            'source_metadata: {}, '\
            'state: {}, '\
            'state_info: {}, '\
            'retry_count: {}, '\
            'retry_type: {}, '\
            'last_update_time: {}, '\
            'creation_time: {}, '\
            'processing_start_time: {}, '\
            'processing_end_time: {} '.format(
                self.report_platform_id,
                self.report_slice_id,
                self.account,
                self.report_json,
                self.git_commit,
                self.ready_to_archive,
                self.source,
                self.source_metadata,
                self.state,
                self.state_info,
                self.retry_count,
                self.retry_type,
                self.last_update_time,
                self.creation_time,
                self.processing_start_time,
                self.processing_end_time) + '}'

    class Meta:
        """Metadata for abstract report slice model."""

        abstract = True


class ReportSlice(AbstractReportSlice):  # pylint: disable=too-many-instance-attributes
    """Represents report slice records."""

    report = models.ForeignKey(Report, null=True, on_delete=models.CASCADE)


class ReportSliceArchive(AbstractReportSlice):
    """Represents report slice archives."""

    report = models.ForeignKey(ReportArchive, null=True, on_delete=models.CASCADE)
