"""
MapleStory Worlds Resource ID Populator

This script fetches detailed resource metadata for specific GUIDs from a populate.txt file
by querying the Nexon MapleStory Worlds API.
"""

import argparse
import asyncio
from typing import Dict, List, Optional, Set, Tuple
import httpx

from maplestory_api import (
    get_request_headers, is_valid_api_response, process_api_item,
    load_json_file, save_json_file, validate_api_token,
    CONCURRENCY, TIMEOUT_SEC, logger
)


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for category-limited population."""
    parser = argparse.ArgumentParser(description='Populate MapleStory Worlds resource metadata by GUID.')
    parser.add_argument(
        '-c',
        '--category',
        action='append',
        default=[],
        help='Only process these populate categories. Repeat the flag or use comma-separated values.',
    )
    return parser.parse_args()


def _parse_category_filters(raw_values: List[str]) -> Optional[Set[str]]:
    """Normalize repeated/comma-separated category arguments into a set."""
    allowed_categories = {
        category.strip()
        for raw_value in raw_values
        for category in raw_value.split(',')
        if category.strip()
    }
    return allowed_categories or None

def _get_output_paths(category_tag: Optional[str]) -> Tuple[str, str]:
    """Resolve the output JSON paths for a category-backed or generic populate run."""
    output_tag = category_tag or 'populate'
    return f'tags/{output_tag}_tags.json', f'guids/{output_tag}_guids.json'


def _is_fallback_tag(category_tag: Optional[str], tag_name: str, guid: str) -> bool:
    """Return True when a tag is the synthetic category-guid fallback."""
    return bool(category_tag) and tag_name == f'{category_tag}-{guid}'


def _find_existing_tag_name(category_tag: Optional[str], guid: str, all_tags: Dict[str, str]) -> Optional[str]:
    """Return the best existing tag name for a GUID from the current category store."""
    if not category_tag:
        return None

    preferred_tag = None
    fallback_tag = None
    for tag_name, mapped_guid in all_tags.items():
        if mapped_guid != guid or not tag_name.startswith(f'{category_tag}-'):
            continue
        if _is_fallback_tag(category_tag, tag_name, guid):
            fallback_tag = tag_name
        else:
            preferred_tag = tag_name
            break

    return preferred_tag or fallback_tag


def _normalize_tag_store(category_tag: Optional[str], all_tags: Dict[str, str]) -> None:
    """Collapse duplicate GUID mappings, preferring payload-derived tags over fallback names."""
    if not category_tag:
        return

    normalized_tags: Dict[str, str] = {}
    seen_guids: Set[str] = set()
    for tag_name, guid in all_tags.items():
        if guid in seen_guids:
            continue
        preferred_tag = _find_existing_tag_name(category_tag, guid, all_tags) or tag_name
        normalized_tags[preferred_tag] = guid
        seen_guids.add(guid)

    all_tags.clear()
    all_tags.update(normalized_tags)


def _guid_needs_reprocessing(category_tag: Optional[str], guid: str, all_tags: Dict[str, str]) -> bool:
    """Re-fetch GUIDs whose category store still only has a synthetic fallback tag."""
    if not category_tag:
        return False

    tag_name = _find_existing_tag_name(category_tag, guid, all_tags)
    return tag_name is None or _is_fallback_tag(category_tag, tag_name, guid)


def _load_output_store(category_tag: Optional[str],
                       store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str]]]
                       ) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Load and cache the output store for one category."""
    output_tag = category_tag or 'populate'
    if output_tag not in store_cache:
        tags_path, guids_path = _get_output_paths(category_tag)
        all_tags = load_json_file(tags_path, f'{output_tag} tags')
        _normalize_tag_store(category_tag, all_tags)
        store_cache[output_tag] = (
            all_tags,
            load_json_file(guids_path, f'{output_tag} guids'),
        )
    return store_cache[output_tag]


def _filter_new_guids(guids_to_populate: List[str], category_by_guid: Dict[str, str],
                      store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str]]]) -> List[str]:
    """Keep GUIDs that are missing or still need payload-based tag repair."""
    new_guids: List[str] = []

    for guid in guids_to_populate:
        category_tag = category_by_guid.get(guid)
        all_tags, all_guids = _load_output_store(category_tag, store_cache)
        if guid not in all_guids or _guid_needs_reprocessing(category_tag, guid, all_tags):
            new_guids.append(guid)

    return new_guids


def _resolve_tag_name(item: Dict[str, str], category_tag: Optional[str], current_guid: str,
                      all_tags: Dict[str, str]) -> Optional[str]:
    """Resolve the tag name from payload first, then existing non-fallback category tags."""
    if not category_tag or not current_guid:
        return None

    api_name = item.get('dname', '')
    if api_name.startswith(f'{category_tag}-'):
        return api_name

    existing_tag_name = _find_existing_tag_name(category_tag, current_guid, all_tags)
    if existing_tag_name and not _is_fallback_tag(category_tag, existing_tag_name, current_guid):
        return existing_tag_name

    return None


async def populate_guids(guids_to_populate: List[str], category_by_guid: Dict[str, str],
                         store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str]]]) -> None:
    """
    Populate metadata for specific GUIDs.

    Args:
        guids_to_populate: List of GUIDs to fetch metadata for
        category_by_guid: Optional category metadata loaded from populate.json
        store_cache: Cached tag/GUID stores keyed by output category
    """
    base_url = 'https://mverse-api.nexon.com/resource/v1/search'
    headers = get_request_headers()

    # Filter out GUIDs we already have
    new_guids = _filter_new_guids(guids_to_populate, category_by_guid, store_cache)

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
                _parse_response(response, batch_indices[i], new_guids, category_by_guid, store_cache)

def _parse_response(response: httpx.Response, index: int, guids_list: List[str],
                   category_by_guid: Dict[str, str],
                   store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str]]]) -> None:
    """
    Parse a single API response and extract resource data.

    Args:
        response: HTTP response object
        index: Index in the guids_list
        guids_list: List of GUIDs being processed
        category_by_guid: GUID->category mappings from populate.json
        store_cache: Cached tag/GUID stores keyed by output category
    """
    if not is_valid_api_response(response):
        logger.error(f"GUID {index} ({guids_list[index] if index < len(guids_list) else 'unknown'}) has invalid response")
        return

    current_guid = guids_list[index] if index < len(guids_list) else ''
    category_tag = category_by_guid.get(current_guid)
    all_tags, all_guids = _load_output_store(category_tag, store_cache)

    data = response.json()
    for item in data['data']['matches']:
        tag_name = _resolve_tag_name(item, category_tag, current_guid, all_tags)
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


def _build_populate_worklist(populate_guids: List[str], category_by_guid: Dict[str, str],
                             allowed_categories: Optional[Set[str]] = None) -> List[str]:
    """Merge GUIDs from populate.txt and populate.json while preserving first-seen order."""
    ordered_guids: List[str] = []
    seen_guids = set()

    for guid in populate_guids:
        category_tag = category_by_guid.get(guid)
        if allowed_categories is not None and category_tag not in allowed_categories:
            continue
        if guid not in seen_guids:
            seen_guids.add(guid)
            ordered_guids.append(guid)

    for guid, category_tag in category_by_guid.items():
        if allowed_categories is not None and category_tag not in allowed_categories:
            continue
        if guid not in seen_guids:
            seen_guids.add(guid)
            ordered_guids.append(guid)

    return ordered_guids


def _save_results(store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str]]]) -> None:
    """Save all touched output stores."""
    for output_tag, (all_tags, all_guids) in store_cache.items():
        tags_path, guids_path = _get_output_paths(None if output_tag == 'populate' else output_tag)
        success_count = 0

        if save_json_file(guids_path, all_guids, f'{output_tag} guids'):
            success_count += 1

        if save_json_file(tags_path, all_tags, f'{output_tag} tags'):
            success_count += 1

        if success_count > 0:
            logger.info(f"Saved {len(all_tags)} tags and {len(all_guids)} GUIDs for {output_tag}")


def main() -> None:
    """Main entry point for the script."""
    args = _parse_args()

    try:
        validate_api_token()
    except ValueError:
        return

    populate_list_guids = _load_populate_list()
    category_by_guid = _load_populate_categories()
    allowed_categories = _parse_category_filters(args.category)
    guids_to_populate = _build_populate_worklist(populate_list_guids, category_by_guid, allowed_categories)
    if not guids_to_populate:
        if allowed_categories:
            logger.error(f"No GUIDs found for requested categories: {sorted(allowed_categories)}")
        else:
            logger.error("No GUIDs found in populate.txt or populate.json")
        return

    category_filter_suffix = f" with category filter {sorted(allowed_categories)}" if allowed_categories else ''
    logger.info(
        f"Loaded {len(populate_list_guids)} GUIDs from populate.txt and "
        f"{len(category_by_guid)} GUIDs from populate.json "
        f"({len(guids_to_populate)} unique GUIDs total){category_filter_suffix}"
    )
    store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str]]] = {}

    try:
        asyncio.run(populate_guids(guids_to_populate, category_by_guid, store_cache))
    except KeyboardInterrupt:
        logger.info("Interrupted by user - saving current progress")
    except Exception as e:
        logger.error(f"Error during processing: {e}")

    _save_results(store_cache)


if __name__ == "__main__":
    main()
