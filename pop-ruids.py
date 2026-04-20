"""
MapleStory Worlds Resource ID Populator

This script fetches detailed resource metadata for specific GUIDs from a populate.txt file
by querying the Nexon MapleStory Worlds API.
"""

import asyncio
from typing import Dict, List
import httpx

from maplestory_api import (
    get_request_headers, is_valid_api_response, process_api_item,
    load_json_file, save_json_file, validate_api_token,
    CONCURRENCY, TIMEOUT_SEC, logger
)

async def populate_guids(guids_to_populate: List[str], category_by_guid: Dict[str, str],
                         all_tags: Dict[str, str], all_guids: Dict[str, str]) -> None:
    """
    Populate metadata for specific GUIDs.

    Args:
        guids_to_populate: List of GUIDs to fetch metadata for
        category_by_guid: Optional category metadata loaded from populate.json
        all_tags: Dictionary to store tag->GUID mappings
        all_guids: Dictionary to store GUID->path mappings
    """
    base_url = 'https://mverse-api.nexon.com/resource/v1/search'
    headers = get_request_headers()

    # Filter out GUIDs we already have
    new_guids = [guid for guid in guids_to_populate if guid not in all_guids]
    if not new_guids:
        logger.info('All GUIDs already populated, nothing to scrape')
        return

    guid_count = len(new_guids)
    logger.info(f'Found {guid_count} new GUIDs to scrape')

    if guid_count < 30:
        logger.info(f'GUIDs to process: {new_guids}')

    async with httpx.AsyncClient(headers=headers) as client:
        async def fetch_guid_data(index: int) -> httpx.Response:
            """Fetch data for a single GUID."""
            guid = new_guids[index]
            return await client.get(f'{base_url}/{guid}', timeout=TIMEOUT_SEC)

        # Process GUIDs in batches
        for batch_start in range(0, guid_count, CONCURRENCY):
            batch_end = min(guid_count, batch_start + CONCURRENCY)
            batch_indices = list(range(batch_start, batch_end))

            logger.info(f'Processing batch {batch_start // CONCURRENCY + 1}: indices {batch_indices}')
            responses = await asyncio.gather(*[fetch_guid_data(i) for i in batch_indices], return_exceptions=True)

            for i, response in enumerate(responses):
                _parse_response(response, batch_indices[i], new_guids, category_by_guid, all_tags, all_guids)

def _parse_response(response: httpx.Response, index: int, guids_list: List[str],
                   category_by_guid: Dict[str, str], all_tags: Dict[str, str],
                   all_guids: Dict[str, str]) -> None:
    """
    Parse a single API response and extract resource data.

    Args:
        response: HTTP response object
        index: Index in the guids_list
        guids_list: List of GUIDs being processed
        category_by_guid: GUID->category mappings from populate.json
        all_tags: Dictionary to store tag->GUID mappings
        all_guids: Dictionary to store GUID->path mappings
    """
    if not is_valid_api_response(response):
        logger.error(f"GUID {index} ({guids_list[index] if index < len(guids_list) else 'unknown'}) has invalid response")
        return

    current_guid = guids_list[index] if index < len(guids_list) else ''
    category_tag = category_by_guid.get(current_guid)
    tag_name = f"{category_tag}-{current_guid}" if category_tag and current_guid else None

    data = response.json()
    for item in data['data']['matches']:
        process_api_item(item, all_tags, all_guids, tag_filter=category_tag, name_override=tag_name)



def _load_populate_list(filename: str = 'populate.txt') -> List[str]:
    """Load the list of GUIDs to populate from a text file."""
    guids = []
    try:
        with open(filename, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    guids.append(line)
    except FileNotFoundError:
        logger.error(f"File {filename} not found")
    except IOError as e:
        logger.error(f"Error reading {filename}: {e}")

    return guids


def _load_populate_categories(filename: str = 'populate.json') -> Dict[str, str]:
    """Load GUID->category metadata for populate processing."""
    data = load_json_file(filename, 'populate categories')
    return {
        guid: category
        for guid, category in data.items()
        if isinstance(guid, str) and isinstance(category, str)
    }


def _build_populate_worklist(populate_guids: List[str], category_by_guid: Dict[str, str]) -> List[str]:
    """Merge GUIDs from populate.txt and populate.json while preserving first-seen order."""
    ordered_guids: List[str] = []
    seen_guids = set()

    for guid in populate_guids:
        if guid not in seen_guids:
            seen_guids.add(guid)
            ordered_guids.append(guid)

    for guid in category_by_guid.keys():
        if guid not in seen_guids:
            seen_guids.add(guid)
            ordered_guids.append(guid)

    return ordered_guids


def _load_existing_data() -> tuple[Dict[str, str], Dict[str, str]]:
    """Load existing tags and GUIDs data."""
    all_tags = load_json_file('tags/populate_tags.json', 'populate tags')
    all_guids = load_json_file('guids/populate_guids.json', 'populate guids')
    return all_tags, all_guids


def _save_results(all_tags: Dict[str, str], all_guids: Dict[str, str]) -> None:
    """Save results to JSON files."""
    success_count = 0

    if save_json_file('guids/populate_guids.json', all_guids, 'populate guids'):
        success_count += 1

    if save_json_file('tags/populate_tags.json', all_tags, 'populate tags'):
        success_count += 1

    if success_count > 0:
        logger.info(f"Saved {len(all_tags)} tags and {len(all_guids)} GUIDs")


def main() -> None:
    """Main entry point for the script."""
    try:
        validate_api_token()
    except ValueError:
        return

    populate_list_guids = _load_populate_list()
    category_by_guid = _load_populate_categories()
    guids_to_populate = _build_populate_worklist(populate_list_guids, category_by_guid)
    if not guids_to_populate:
        logger.error("No GUIDs found in populate.txt or populate.json")
        return

    logger.info(
        f"Loaded {len(populate_list_guids)} GUIDs from populate.txt and "
        f"{len(category_by_guid)} GUIDs from populate.json "
        f"({len(guids_to_populate)} unique GUIDs total)"
    )
    all_tags, all_guids = _load_existing_data()

    try:
        asyncio.run(populate_guids(guids_to_populate, category_by_guid, all_tags, all_guids))
    except KeyboardInterrupt:
        logger.info("Interrupted by user - saving current progress")
    except Exception as e:
        logger.error(f"Error during processing: {e}")

    _save_results(all_tags, all_guids)


if __name__ == "__main__":
    main()
