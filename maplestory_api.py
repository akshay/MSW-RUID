"""
MapleStory Worlds API Common Utilities

Shared functionality for interacting with the Nexon MapleStory Worlds API,
including authentication, response parsing, and data processing utilities.
"""

import json
import logging
import os
from typing import Dict, List, Optional, Tuple
import httpx

CONCURRENCY = 1
TIMEOUT_SEC = 15.0
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_request_headers() -> Dict[str, str]:
    """Get the standard headers for API requests."""
    api_token = os.getenv('API_TOKEN')
    if not api_token:
        raise ValueError("API_TOKEN environment variable is required")

    return {
        'Accept': 'application/json, text/plain',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br, zstd',
        'Referer': 'https://maplestoryworlds.nexon.com/',
        'Origin': 'https://maplestoryworlds.nexon.com',
        'Sec-Ch-Ua-Platform': '"macOS"',
        'Sec-Ch-Ua': '"Not(A:Brand";v="99", "Chromium";v="133", "Google Chrome";v="133"',
        'X-Mverse-Countrycode': 'CA',
        'X-Mverse-ifwt': api_token,
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36'
    }


def is_valid_api_response(response: httpx.Response) -> bool:
    """Check if an API response is valid."""
    if isinstance(response, Exception):
        logger.error(f"Request failed with exception: {response}")
        return False

    if response.status_code != 200:
        return False

    try:
        data = response.json()
    except json.JSONDecodeError:
        logger.error("Invalid JSON response")
        return False

    if data.get('code') != 0:
        logger.error(f"API error: {data}")
        return False

    if not data.get('data') or not data['data'].get('matches'):
        logger.warning("Response has no matches")
        return False

    return True


def extract_best_tags(tags: List[str], etag: str, hashstr: str, category_tag: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the best image and path tags from item tags.

    Args:
        tags: List of tags from the API response
        etag: The item's display name
        hashstr: The item's hash string
        category_tag: Optional category tag for additional validation

    Returns:
        Tuple of (best_img_name, best_path_name)
    """
    best_img_name = None
    best_path_name = None

    for tag in tags:
        if best_img_name and best_path_name and '/' in best_path_name:
            break

        if tag.endswith('.img'):
            if best_img_name:
                logger.warning(f"Duplicate img for {etag}: {best_img_name}, {tag}")
                continue
            best_img_name = tag
        elif is_valid_path_tag(tag, etag, hashstr, category_tag):
            if not best_path_name or ('/' not in best_path_name and '/' in tag):
                best_path_name = tag
            else:
                pass
                # logger.warning(f"Duplicate path for {etag}: {best_path_name}, {tag}")

    return best_img_name, best_path_name


def is_valid_path_tag(tag: str, etag: str, hashstr: str, category_tag: Optional[str] = None) -> bool:
    """
    Check if a tag is a valid path tag.

    Args:
        tag: The tag to validate
        etag: The item's display name
        hashstr: The item's hash string
        category_tag: Optional category tag for special handling

    Returns:
        True if the tag is valid, False otherwise
    """
    base_validation = (
        tag.isascii() and
        tag != etag and
        tag != hashstr and
        '#' not in tag and
        tag != '???'
    )

    if not base_validation:
        return False

    # Special handling for sound category
    if category_tag == 'sound':
        return True

    # For other categories, reject tags with spaces
    return ' ' not in tag


def should_combine_paths(img_name: str, path_name: str) -> bool:
    """
    Determine if image and path names should be combined.

    Args:
        img_name: The image file name
        path_name: The path name to potentially combine

    Returns:
        True if paths should be combined, False otherwise
    """
    return (path_name and
            ((not img_name.startswith(('map/', 'character/'))) or
             '/' in path_name))


def process_api_item(item: Dict, all_tags: Dict[str, str], all_guids: Dict[str, str],
                    tag_filter: Optional[str] = None, name_override: Optional[str] = None) -> None:
    """
    Process a single resource item from the API response.

    Args:
        item: The item data from API response
        all_tags: Dictionary to store tag->GUID mappings
        all_guids: Dictionary to store GUID->path mappings
        tag_filter: Optional filter to only process items with tags starting with this prefix
    """
    api_name = item.get('dname', '')
    guid = item.get('guid', '')

    if not api_name or not guid:
        logger.warning(f"Item missing etag or guid: {item}")
        return

    # Apply tag filter if specified
    if tag_filter and name_override is None and not api_name.startswith(f"{tag_filter}-"):
        return

    etag = name_override or api_name

    # Skip if we already have this data
    if etag in all_tags and guid in all_guids:
        return

    all_tags[etag] = guid
    best_img_name, best_path_name = extract_best_tags(
        item.get('tags', []),
        api_name,
        item.get('hashstr', ''),
        tag_filter
    )

    if not best_img_name:
        logger.warning(f"No valid img tag found for {etag}")
        return

    if best_path_name and should_combine_paths(best_img_name, best_path_name):
        best_img_name = f"{best_img_name}/{best_path_name}"

    logger.info(f"Found: {etag} -> {guid} -> {best_img_name}")

    # Handle multiple paths for the same GUID
    prefix = f"{all_guids[guid]}," if guid in all_guids else ""
    all_guids[guid] = f"{prefix}{best_img_name}"


def load_json_file(filepath: str, description: str = "data") -> Dict:
    """
    Safely load a JSON file with error handling.

    Args:
        filepath: Path to the JSON file
        description: Description of the file for error messages

    Returns:
        Dictionary containing the loaded data, or empty dict if file doesn't exist or has errors
    """
    if not os.path.isfile(filepath):
        return {}

    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.warning(f"Failed to load {description} from {filepath}: {e}")
        return {}


def save_json_file(filepath: str, data: Dict, description: str = "data") -> bool:
    """
    Safely save data to a JSON file with error handling.

    Args:
        filepath: Path where to save the file
        data: Data to save
        description: Description of the file for error messages

    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure parent directory exists
        parent_dir = os.path.dirname(filepath)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        with open(filepath, 'w') as f:
            json.dump(data, f, indent=4)
        return True
    except IOError as e:
        logger.error(f"Failed to save {description} to {filepath}: {e}")
        return False


def validate_api_token() -> None:
    """
    Validate that the API token is available and properly set.

    Raises:
        ValueError: If API token is not set or invalid
    """
    try:
        get_request_headers()
        logger.info("API token validation successful")
    except ValueError as e:
        logger.error(f"API token validation failed: {e}")
        raise
