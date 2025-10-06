"""
S3 storage helper functions for Archipelago player configuration management.
"""

import os
import json
import logging
import subprocess
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def upload_to_s3(filepath: str, bucket: str, s3_key: str, metadata: Dict[str, str]) -> bool:
    """
    Upload file to S3 with metadata.

    Args:
        filepath: Local file path to upload
        bucket: S3 bucket name
        s3_key: S3 object key (path in bucket)
        metadata: Dictionary of metadata key-value pairs

    Returns:
        True if upload successful, False otherwise
    """
    try:
        logger.debug(f"Uploading {filepath} to s3://{bucket}/{s3_key}")
        # Build metadata string for AWS CLI
        metadata_str = ",".join([f"{k}={v}" for k, v in metadata.items()])

        # Upload to S3 with metadata
        cmd = [
            "aws", "s3", "cp", filepath,
            f"s3://{bucket}/{s3_key}",
            "--metadata", metadata_str
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info(f"Successfully uploaded to s3://{bucket}/{s3_key}")
            return True
        else:
            logger.error(f"S3 upload error: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error uploading to S3: {e}", exc_info=True)
        return False


def download_from_s3(bucket: str, s3_key: str, local_path: str) -> bool:
    """
    Download file from S3.

    Args:
        bucket: S3 bucket name
        s3_key: S3 object key (path in bucket)
        local_path: Local destination path

    Returns:
        True if download successful, False otherwise
    """
    try:
        logger.debug(f"Downloading s3://{bucket}/{s3_key} to {local_path}")
        cmd = [
            "aws", "s3", "cp",
            f"s3://{bucket}/{s3_key}",
            local_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info(f"Successfully downloaded s3://{bucket}/{s3_key}")
            return True
        else:
            logger.error(f"S3 download error: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error downloading from S3: {e}", exc_info=True)
        return False


def delete_from_s3(bucket: str, s3_key: str) -> bool:
    """
    Delete file from S3.

    Args:
        bucket: S3 bucket name
        s3_key: S3 object key (path in bucket)

    Returns:
        True if deletion successful, False otherwise
    """
    try:
        logger.debug(f"Deleting s3://{bucket}/{s3_key}")
        cmd = [
            "aws", "s3", "rm",
            f"s3://{bucket}/{s3_key}"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            logger.info(f"Successfully deleted s3://{bucket}/{s3_key}")
            return True
        else:
            logger.error(f"S3 delete error: {result.stderr}")
            return False
    except Exception as e:
        logger.error(f"Error deleting from S3: {e}", exc_info=True)
        return False


def list_user_files_from_s3(bucket: str, discord_user_id: str) -> List[Dict]:
    """
    List all files for a user from S3 with metadata.

    Args:
        bucket: S3 bucket name
        discord_user_id: Discord user ID (used as prefix)

    Returns:
        List of dictionaries containing file information and metadata
    """
    try:
        logger.debug(f"Listing S3 files for user {discord_user_id}")
        # List objects for this user
        cmd = [
            "aws", "s3api", "list-objects-v2",
            "--bucket", bucket,
            "--prefix", f"{discord_user_id}/",
            "--output", "json"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            logger.warning(f"Failed to list S3 objects for user {discord_user_id}")
            return []

        objects = json.loads(result.stdout)

        if "Contents" not in objects:
            logger.debug(f"No files found for user {discord_user_id}")
            return []

        user_files = []

        # Get metadata for each file
        for obj in objects["Contents"]:
            s3_key = obj["Key"]

            # Get object metadata
            meta_cmd = [
                "aws", "s3api", "head-object",
                "--bucket", bucket,
                "--key", s3_key,
                "--output", "json"
            ]

            meta_result = subprocess.run(meta_cmd, capture_output=True, text=True)

            if meta_result.returncode == 0:
                meta_data = json.loads(meta_result.stdout)
                metadata = meta_data.get("Metadata", {})

                # Log metadata for debugging
                logger.debug(f"Metadata for {s3_key}: {metadata}")
                logger.debug(f"Metadata keys: {list(metadata.keys())}")

                # AWS CLI converts metadata keys - try different variations
                game_type = (
                    metadata.get("game_type") or
                    metadata.get("gametype") or
                    metadata.get("game-type") or
                    "Unknown"
                )

                user_files.append({
                    "s3_key": s3_key,
                    "player_name": metadata.get("player_name", "Unknown"),
                    "game": metadata.get("game", "Unknown"),
                    "game_type": game_type,
                    "upload_date": metadata.get("upload_date") or metadata.get("uploaddate") or "Unknown",
                    "description": metadata.get("description", ""),
                    "uploaded": obj.get("LastModified", "Unknown"),
                    "size": obj.get("Size", 0)
                })

        logger.info(f"Found {len(user_files)} file(s) for user {discord_user_id}")
        return user_files

    except Exception as e:
        logger.error(f"Error listing S3 files: {e}", exc_info=True)
        return []


def load_cache(cache_file: str) -> Dict:
    """
    Load the local cache file.

    Args:
        cache_file: Path to cache JSON file

    Returns:
        Dictionary containing cached data
    """
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading cache: {e}")
            return {}
    return {}


def save_cache(cache: Dict, cache_file: str) -> None:
    """
    Save the cache to disk.

    Args:
        cache: Cache dictionary to save
        cache_file: Path to cache JSON file
    """
    try:
        with open(cache_file, 'w') as f:
            json.dump(cache, f, indent=2)
    except Exception as e:
        print(f"Error saving cache: {e}")


def refresh_user_cache(cache: Dict, cache_file: str, bucket: str, discord_user_id: str) -> List[Dict]:
    """
    Refresh cache for a specific user by fetching S3 metadata.

    Args:
        cache: Current cache dictionary
        cache_file: Path to cache JSON file
        bucket: S3 bucket name
        discord_user_id: Discord user ID

    Returns:
        List of user's files with metadata
    """
    logger.debug(f"Refreshing cache for user {discord_user_id}")
    user_files = list_user_files_from_s3(bucket, discord_user_id)

    # Update cache
    cache[discord_user_id] = user_files
    save_cache(cache, cache_file)
    logger.debug(f"Cache refreshed for user {discord_user_id} with {len(user_files)} file(s)")

    return user_files
