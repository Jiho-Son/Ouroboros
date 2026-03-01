"""Cloud storage integration for off-site backups.

Supports S3-compatible storage providers:
- AWS S3
- MinIO
- Backblaze B2
- DigitalOcean Spaces
- Cloudflare R2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class S3Config:
    """Configuration for S3-compatible storage."""

    endpoint_url: str | None  # None for AWS S3, custom URL for others
    access_key: str
    secret_key: str
    bucket_name: str
    region: str = "us-east-1"
    use_ssl: bool = True


class CloudStorage:
    """Upload backups to S3-compatible cloud storage."""

    def __init__(self, config: S3Config) -> None:
        """Initialize cloud storage client.

        Args:
            config: S3 configuration

        Raises:
            ImportError: If boto3 is not installed
        """
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "boto3 is required for cloud storage. Install with: pip install boto3"
            )

        self.config = config
        self.client = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            aws_access_key_id=config.access_key,
            aws_secret_access_key=config.secret_key,
            region_name=config.region,
            use_ssl=config.use_ssl,
        )

    def upload_file(
        self,
        file_path: Path,
        object_key: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> str:
        """Upload a file to cloud storage.

        Args:
            file_path: Local file to upload
            object_key: S3 object key (default: filename)
            metadata: Optional metadata to attach

        Returns:
            S3 object key

        Raises:
            FileNotFoundError: If file doesn't exist
            Exception: If upload fails
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if object_key is None:
            object_key = file_path.name

        extra_args: dict[str, Any] = {}

        # Add server-side encryption
        extra_args["ServerSideEncryption"] = "AES256"

        # Add metadata if provided
        if metadata:
            extra_args["Metadata"] = metadata

        logger.info(
            "Uploading %s to s3://%s/%s", file_path.name, self.config.bucket_name, object_key
        )

        try:
            self.client.upload_file(
                str(file_path),
                self.config.bucket_name,
                object_key,
                ExtraArgs=extra_args,
            )
            logger.info("Upload successful: %s", object_key)
            return object_key
        except Exception as exc:
            logger.error("Upload failed: %s", exc)
            raise

    def download_file(self, object_key: str, local_path: Path) -> Path:
        """Download a file from cloud storage.

        Args:
            object_key: S3 object key
            local_path: Local destination path

        Returns:
            Path to downloaded file

        Raises:
            Exception: If download fails
        """
        local_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("Downloading s3://%s/%s to %s", self.config.bucket_name, object_key, local_path)

        try:
            self.client.download_file(
                self.config.bucket_name,
                object_key,
                str(local_path),
            )
            logger.info("Download successful: %s", local_path)
            return local_path
        except Exception as exc:
            logger.error("Download failed: %s", exc)
            raise

    def list_files(self, prefix: str = "") -> list[dict[str, Any]]:
        """List files in cloud storage.

        Args:
            prefix: Filter by object key prefix

        Returns:
            List of file metadata dictionaries
        """
        try:
            response = self.client.list_objects_v2(
                Bucket=self.config.bucket_name,
                Prefix=prefix,
            )

            if "Contents" not in response:
                return []

            files = []
            for obj in response["Contents"]:
                files.append(
                    {
                        "key": obj["Key"],
                        "size_bytes": obj["Size"],
                        "last_modified": obj["LastModified"],
                        "etag": obj["ETag"],
                    }
                )

            return files
        except Exception as exc:
            logger.error("Failed to list files: %s", exc)
            raise

    def delete_file(self, object_key: str) -> None:
        """Delete a file from cloud storage.

        Args:
            object_key: S3 object key

        Raises:
            Exception: If deletion fails
        """
        logger.info("Deleting s3://%s/%s", self.config.bucket_name, object_key)

        try:
            self.client.delete_object(
                Bucket=self.config.bucket_name,
                Key=object_key,
            )
            logger.info("Deletion successful: %s", object_key)
        except Exception as exc:
            logger.error("Deletion failed: %s", exc)
            raise

    def get_storage_stats(self) -> dict[str, Any]:
        """Get cloud storage statistics.

        Returns:
            Dictionary with storage stats
        """
        try:
            files = self.list_files()

            total_size = sum(f["size_bytes"] for f in files)
            total_count = len(files)

            return {
                "total_files": total_count,
                "total_size_bytes": total_size,
                "total_size_mb": total_size / 1024 / 1024,
                "total_size_gb": total_size / 1024 / 1024 / 1024,
            }
        except Exception as exc:
            logger.error("Failed to get storage stats: %s", exc)
            return {
                "error": str(exc),
                "total_files": 0,
                "total_size_bytes": 0,
            }

    def verify_connection(self) -> bool:
        """Verify connection to cloud storage.

        Returns:
            True if connection is successful
        """
        try:
            self.client.head_bucket(Bucket=self.config.bucket_name)
            logger.info("Cloud storage connection verified")
            return True
        except Exception as exc:
            logger.error("Cloud storage connection failed: %s", exc)
            return False

    def create_bucket_if_not_exists(self) -> None:
        """Create storage bucket if it doesn't exist.

        Raises:
            Exception: If bucket creation fails
        """
        try:
            self.client.head_bucket(Bucket=self.config.bucket_name)
            logger.info("Bucket already exists: %s", self.config.bucket_name)
        except self.client.exceptions.NoSuchBucket:
            logger.info("Creating bucket: %s", self.config.bucket_name)
            if self.config.region == "us-east-1":
                # us-east-1 requires special handling
                self.client.create_bucket(Bucket=self.config.bucket_name)
            else:
                self.client.create_bucket(
                    Bucket=self.config.bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": self.config.region},
                )
            logger.info("Bucket created successfully")
        except Exception as exc:
            logger.error("Failed to verify/create bucket: %s", exc)
            raise

    def enable_versioning(self) -> None:
        """Enable versioning on the bucket.

        Raises:
            Exception: If versioning enablement fails
        """
        try:
            self.client.put_bucket_versioning(
                Bucket=self.config.bucket_name,
                VersioningConfiguration={"Status": "Enabled"},
            )
            logger.info("Versioning enabled for bucket: %s", self.config.bucket_name)
        except Exception as exc:
            logger.error("Failed to enable versioning: %s", exc)
            raise
