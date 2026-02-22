"""
BCK Manager - S3 Client Module
Handles all interactions with S3-compatible object storage.
"""

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError


class S3Client:
    """Wrapper around boto3 S3 client for S3-compatible storage."""

    def __init__(self, endpoint_url, access_key, secret_key, region, logger):
        self.endpoint_url = endpoint_url
        self.region = region
        self.logger = logger

        try:
            self._client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name=region,
                config=BotoConfig(
                    retries={"max_attempts": 3, "mode": "standard"},
                    connect_timeout=30,
                    read_timeout=60,
                ),
            )
            self.logger.info(f"S3 client initialized for endpoint: {endpoint_url}")
        except Exception as e:
            self.logger.error(f"Failed to initialize S3 client: {e}")
            raise

    def list_buckets(self):
        """List all buckets on the endpoint."""
        try:
            response = self._client.list_buckets()
            buckets = response.get("Buckets", [])
            self.logger.info(f"Listed {len(buckets)} bucket(s) on {self.endpoint_url}")
            return buckets
        except (ClientError, EndpointConnectionError) as e:
            self.logger.error(f"Failed to list buckets: {e}")
            raise

    def list_objects(self, bucket, prefix="", max_keys=1000):
        """List objects in a bucket with optional prefix filter.

        Args:
            bucket: S3 bucket name.
            prefix: Key prefix to filter objects.
            max_keys: Maximum number of objects to return.
                      Use 0 to list ALL objects (no limit).
        """
        try:
            objects = []
            paginator = self._client.get_paginator("list_objects_v2")

            pagination_config = {}
            if max_keys > 0:
                pagination_config["MaxItems"] = max_keys

            page_iterator = paginator.paginate(
                Bucket=bucket,
                Prefix=prefix,
                PaginationConfig=pagination_config,
            )
            for page in page_iterator:
                for obj in page.get("Contents", []):
                    objects.append(obj)

            self.logger.info(
                f"Listed {len(objects)} object(s) in s3://{bucket}/{prefix}"
            )
            return objects
        except ClientError as e:
            self.logger.error(f"Failed to list objects in {bucket}/{prefix}: {e}")
            raise

    def upload_file(self, local_path, bucket, key):
        """Upload a local file to S3."""
        try:
            self.logger.info(f"Uploading {local_path} -> s3://{bucket}/{key}")
            self._client.upload_file(local_path, bucket, key)
            self.logger.info(f"Upload complete: s3://{bucket}/{key}")
            return True
        except (ClientError, FileNotFoundError) as e:
            self.logger.error(f"Upload failed for {local_path}: {e}")
            raise

    def download_file(self, bucket, key, local_path):
        """Download a file from S3 to a local path."""
        try:
            self.logger.info(f"Downloading s3://{bucket}/{key} -> {local_path}")
            self._client.download_file(bucket, key, local_path)
            self.logger.info(f"Download complete: {local_path}")
            return True
        except ClientError as e:
            self.logger.error(f"Download failed for s3://{bucket}/{key}: {e}")
            raise

    def get_object_info(self, bucket, key):
        """Get metadata about a specific object."""
        try:
            response = self._client.head_object(Bucket=bucket, Key=key)
            return {
                "size": response.get("ContentLength", 0),
                "last_modified": response.get("LastModified"),
                "etag": response.get("ETag", ""),
            }
        except ClientError as e:
            self.logger.error(f"Failed to get info for s3://{bucket}/{key}: {e}")
            raise

    def delete_object(self, bucket, key):
        """Delete an object from S3."""
        try:
            self._client.delete_object(Bucket=bucket, Key=key)
            self.logger.info(f"Deleted s3://{bucket}/{key}")
            return True
        except ClientError as e:
            self.logger.error(f"Failed to delete s3://{bucket}/{key}: {e}")
            raise

    def test_connection(self):
        """Test connection to the S3 endpoint."""
        try:
            self._client.list_buckets()
            return True
        except (ClientError, EndpointConnectionError, NoCredentialsError) as e:
            self.logger.error(f"Connection test failed for {self.endpoint_url}: {e}")
            return False
