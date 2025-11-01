"""S3 upload functionality with retry logic."""

import logging
import time
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


class S3Uploader:
    """Handles S3 file uploads with retry logic."""

    def __init__(
        self,
        bucket_name: str,
        region: Optional[str] = None,
        max_retries: int = 3,
        retry_backoff_base: float = 2.0,
    ):
        """Initialize S3 uploader.

        Args:
            bucket_name: S3 bucket name
            region: AWS region (None for default)
            max_retries: Maximum number of retry attempts
            retry_backoff_base: Base for exponential backoff
        """
        self.bucket_name = bucket_name
        self.max_retries = max_retries
        self.retry_backoff_base = retry_backoff_base

        # Initialize boto3 client
        if region:
            self.s3_client = boto3.client("s3", region_name=region)
        else:
            self.s3_client = boto3.client("s3")

        logger.info(f"S3Uploader initialized for bucket: {bucket_name}")

    def upload_file(
        self,
        local_path: Path,
        s3_key: str,
        encryption: str = "AES256",
    ) -> bool:
        """Upload file to S3 with retry logic.

        Args:
            local_path: Local file path
            s3_key: S3 object key
            encryption: Server-side encryption type

        Returns:
            True if successful, False otherwise
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"Uploading {local_path} to s3://{self.bucket_name}/{s3_key} (attempt {attempt})")

                self.s3_client.upload_file(
                    str(local_path),
                    self.bucket_name,
                    s3_key,
                    ExtraArgs={"ServerSideEncryption": encryption},
                )

                logger.info(f"Successfully uploaded {s3_key}")
                return True

            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "Unknown")
                logger.error(f"Upload failed (attempt {attempt}/{self.max_retries}): {error_code} - {e}")

                if attempt < self.max_retries:
                    backoff_time = self.retry_backoff_base ** attempt
                    logger.info(f"Retrying in {backoff_time:.1f} seconds...")
                    time.sleep(backoff_time)
                else:
                    logger.error(f"Failed to upload {s3_key} after {self.max_retries} attempts")
                    return False

            except Exception as e:
                logger.error(f"Unexpected error during upload: {e}")
                return False

        return False

    def upload_json(
        self,
        data: dict,
        s3_key: str,
        encryption: str = "AES256",
    ) -> bool:
        """Upload JSON data directly to S3.

        Args:
            data: Dictionary to upload as JSON
            s3_key: S3 object key
            encryption: Server-side encryption type

        Returns:
            True if successful, False otherwise
        """
        import json

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"Uploading JSON to s3://{self.bucket_name}/{s3_key} (attempt {attempt})")

                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=json.dumps(data, indent=2).encode("utf-8"),
                    ContentType="application/json",
                    ServerSideEncryption=encryption,
                )

                logger.info(f"Successfully uploaded JSON {s3_key}")
                return True

            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "Unknown")
                logger.error(f"Upload failed (attempt {attempt}/{self.max_retries}): {error_code} - {e}")

                if attempt < self.max_retries:
                    backoff_time = self.retry_backoff_base ** attempt
                    logger.info(f"Retrying in {backoff_time:.1f} seconds...")
                    time.sleep(backoff_time)
                else:
                    logger.error(f"Failed to upload {s3_key} after {self.max_retries} attempts")
                    return False

            except Exception as e:
                logger.error(f"Unexpected error during JSON upload: {e}")
                return False

        return False
