"""
S3 storage helper functions for Archipelago player configuration management.
"""

import os
import json
import subprocess
from typing import Dict, List, Optional
from datetime import datetime


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
            return True
        else:
            print(f"S3 upload error: {result.stderr}")
            return False
    except Exception as e:
        print(f"Error uploading to S3: {e}")
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
        cmd = [
            "aws", "s3", "cp",
            f"s3://{bucket}/{s3_key}",
            local_path
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            return True
        else:
            print(f"S3 download error: {result.stderr}")
            return False
    except Exception as e:
        print(f"Error downloading from S3: {e}")
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
        cmd = [
            "aws", "s3", "rm",
            f"s3://{bucket}/{s3_key}"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            return True
        else:
            print(f"S3 delete error: {result.stderr}")
            return False
    except Exception as e:
        print(f"Error deleting from S3: {e}")
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
        # List objects for this user
        cmd = [
            "aws", "s3api", "list-objects-v2",
            "--bucket", bucket,
            "--prefix", f"{discord_user_id}/",
            "--output", "json"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            return []

        objects = json.loads(result.stdout)

        if "Contents" not in objects:
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

                user_files.append({
                    "s3_key": s3_key,
                    "player_name": metadata.get("player_name", "Unknown"),
                    "game": metadata.get("game", "Unknown"),
                    "upload_date": metadata.get("upload_date", "Unknown"),
                    "description": metadata.get("description", ""),
                    "uploaded": obj.get("LastModified", "Unknown"),
                    "size": obj.get("Size", 0)
                })

        return user_files

    except Exception as e:
        print(f"Error listing S3 files: {e}")
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
    user_files = list_user_files_from_s3(bucket, discord_user_id)

    # Update cache
    cache[discord_user_id] = user_files
    save_cache(cache, cache_file)

    return user_files
