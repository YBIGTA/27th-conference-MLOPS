"""Integration tests for S3 upload using moto."""

import tempfile
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from src.collector.s3_uploader import S3Uploader


@pytest.fixture
def aws_credentials():
    """Mock AWS credentials for moto."""
    import os

    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def s3_bucket(aws_credentials):
    """Create mock S3 bucket."""
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket_name = "test-bucket"
        s3.create_bucket(Bucket=bucket_name)
        yield bucket_name


def test_upload_file_success(s3_bucket):
    """Test successful file upload."""
    with mock_aws():
        uploader = S3Uploader(bucket_name=s3_bucket, region="us-east-1")

        # Create test file
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("test content\n")
            temp_path = Path(f.name)

        try:
            result = uploader.upload_file(
                local_path=temp_path, s3_key="test/file.txt", encryption="AES256"
            )

            assert result is True

            # Verify file exists in S3
            s3 = boto3.client("s3", region_name="us-east-1")
            response = s3.head_object(Bucket=s3_bucket, Key="test/file.txt")
            assert response["ServerSideEncryption"] == "AES256"

        finally:
            temp_path.unlink()


def test_upload_json_success(s3_bucket):
    """Test successful JSON upload."""
    with mock_aws():
        uploader = S3Uploader(bucket_name=s3_bucket, region="us-east-1")

        test_data = {"key": "value", "number": 123, "nested": {"field": "data"}}

        result = uploader.upload_json(
            data=test_data, s3_key="test/data.json", encryption="AES256"
        )

        assert result is True

        # Verify JSON in S3
        s3 = boto3.client("s3", region_name="us-east-1")
        response = s3.get_object(Bucket=s3_bucket, Key="test/data.json")
        content = response["Body"].read().decode("utf-8")

        import json

        loaded_data = json.loads(content)
        assert loaded_data == test_data


def test_upload_file_nonexistent(s3_bucket):
    """Test upload of nonexistent file."""
    with mock_aws():
        uploader = S3Uploader(bucket_name=s3_bucket, region="us-east-1")

        result = uploader.upload_file(
            local_path=Path("/nonexistent/file.txt"), s3_key="test/file.txt"
        )

        assert result is False


def test_upload_with_encryption(s3_bucket):
    """Test that encryption is applied."""
    with mock_aws():
        uploader = S3Uploader(bucket_name=s3_bucket, region="us-east-1")

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
            f.write("encrypted content\n")
            temp_path = Path(f.name)

        try:
            uploader.upload_file(
                local_path=temp_path, s3_key="test/encrypted.txt", encryption="AES256"
            )

            # Verify encryption
            s3 = boto3.client("s3", region_name="us-east-1")
            response = s3.head_object(Bucket=s3_bucket, Key="test/encrypted.txt")
            assert "ServerSideEncryption" in response
            assert response["ServerSideEncryption"] == "AES256"

        finally:
            temp_path.unlink()


def test_multiple_uploads(s3_bucket):
    """Test multiple file uploads."""
    with mock_aws():
        uploader = S3Uploader(bucket_name=s3_bucket, region="us-east-1")

        # Upload multiple files
        for i in range(5):
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
                f.write(f"content {i}\n")
                temp_path = Path(f.name)

            try:
                result = uploader.upload_file(temp_path, f"test/file{i}.txt")
                assert result is True
            finally:
                temp_path.unlink()

        # Verify all files exist
        s3 = boto3.client("s3", region_name="us-east-1")
        response = s3.list_objects_v2(Bucket=s3_bucket, Prefix="test/")
        assert response["KeyCount"] == 5
