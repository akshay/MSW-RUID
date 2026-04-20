"""
MapleStory Worlds Resource ID Populator

This script fetches detailed resource metadata for specific GUIDs from a populate.txt file
by querying the Nexon MapleStory Worlds API.
"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
import httpx

from maplestory_api import (
    get_request_headers, is_valid_api_response, process_api_item,
    load_json_file, save_json_file, validate_api_token,
    CONCURRENCY, TIMEOUT_SEC, logger, rate_limited_get
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


def _build_guid_tag_name_index(category_tag: Optional[str], all_tags: Dict[str, str]) -> Dict[str, str]:
    """Build a GUID->tag index, preferring payload-derived tags over fallback names."""
    guid_tag_names: Dict[str, str] = {}

    for tag_name, guid in all_tags.items():
        current_tag_name = guid_tag_names.get(guid)
        if current_tag_name is None:
            guid_tag_names[guid] = tag_name
            continue

        if _is_fallback_tag(category_tag, current_tag_name, guid) and not _is_fallback_tag(category_tag, tag_name, guid):
            guid_tag_names[guid] = tag_name

    return guid_tag_names


def _normalize_tag_store(category_tag: Optional[str], all_tags: Dict[str, str]) -> None:
    """Collapse duplicate GUID mappings, preferring payload-derived tags over fallback names."""
    if not category_tag:
        return

    guid_tag_names = _build_guid_tag_name_index(category_tag, all_tags)
    normalized_tags = {
        tag_name: guid
        for guid, tag_name in guid_tag_names.items()
    }
    all_tags.clear()
    all_tags.update(normalized_tags)


def _guid_needs_reprocessing(category_tag: Optional[str], guid: str,
                             known_tag_guids: Set[str],
                             guid_tag_names: Dict[str, str]) -> bool:
    """Re-fetch GUIDs whose category store still only has a synthetic fallback tag."""
    if not category_tag:
        return False

    if guid not in known_tag_guids:
        return True

    tag_name = guid_tag_names.get(guid)
    return tag_name is None or _is_fallback_tag(category_tag, tag_name, guid)


def _load_output_store(category_tag: Optional[str],
                       store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str], Set[str], Dict[str, str]]]
                       ) -> Tuple[Dict[str, str], Dict[str, str], Set[str], Dict[str, str]]:
    """Load and cache the output store for one category."""
    output_tag = category_tag or 'populate'
    if output_tag not in store_cache:
        tags_path, guids_path = _get_output_paths(category_tag)
        all_tags = load_json_file(tags_path, f'{output_tag} tags')
        _normalize_tag_store(category_tag, all_tags)
        guid_tag_names = _build_guid_tag_name_index(category_tag, all_tags)
        store_cache[output_tag] = (
            all_tags,
            load_json_file(guids_path, f'{output_tag} guids'),
            set(guid_tag_names.keys()),
            guid_tag_names,
        )
    return store_cache[output_tag]


def _filter_new_guids(guids_to_populate: List[str], category_by_guid: Dict[str, str],
                      store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str], Set[str], Dict[str, str]]]) -> List[str]:
    """Keep GUIDs that are missing or still need payload-based tag repair."""
    new_guids: List[str] = []
    total_guids = len(guids_to_populate)
    logger.info(f'Filtering {total_guids} candidate GUIDs against existing output stores')

    for index, guid in enumerate(guids_to_populate, start=1):
        category_tag = category_by_guid.get(guid)
        _, all_guids, known_tag_guids, guid_tag_names = _load_output_store(category_tag, store_cache)
        if guid not in all_guids or _guid_needs_reprocessing(category_tag, guid, known_tag_guids, guid_tag_names):
            new_guids.append(guid)
        if index % 10000 == 0 or index == total_guids:
            logger.info(f'Filtered {index}/{total_guids} candidate GUIDs')

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
                         store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str], Set[str], Dict[str, str]]]) -> None:
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
            return await rate_limited_get(client, f'{base_url}/{guid}', timeout=TIMEOUT_SEC)

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
                   store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str], Set[str], Dict[str, str]]]) -> None:
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
    all_tags, all_guids, _, _ = _load_output_store(category_tag, store_cache)

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


def _iter_json_object_string_items(filepath: Path):
    """Yield string key/value pairs from generated single-line-per-entry JSON objects."""
    if not filepath.is_file():
        return

    with open(filepath, 'r') as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line in {'{', '}', '{}'}:
                continue
            if line.endswith(','):
                line = line[:-1]

            try:
                item = json.loads(f'{{{line}}}')
            except json.JSONDecodeError as exc:
                logger.warning(f'Failed to parse JSON entry from {filepath}: {exc}')
                continue

            if len(item) != 1:
                continue

            key, value = next(iter(item.items()))
            if isinstance(key, str) and isinstance(value, str):
                yield key, value


def _discover_missing_store_guids(allowed_categories: Optional[Set[str]] = None,
                                  tags_dir: str = 'tags',
                                  guids_dir: str = 'guids') -> Dict[str, str]:
    """Find GUIDs that exist in category tag stores but not in their GUID output store."""
    discovered_guids: Dict[str, str] = {}

    for tag_path in sorted(Path(tags_dir).glob('*_tags.json')):
        category_tag = tag_path.name.removesuffix('_tags.json')
        if allowed_categories is not None and category_tag not in allowed_categories:
            continue

        guid_path = Path(guids_dir) / f'{category_tag}_guids.json'
        logger.info(f'Scanning {category_tag} tag store for missing GUID entries')
        known_guids = {guid for guid, _ in _iter_json_object_string_items(guid_path)}
        category_missing = 0

        for _, guid in _iter_json_object_string_items(tag_path):
            if guid in known_guids or guid in discovered_guids:
                continue
            discovered_guids[guid] = category_tag
            category_missing += 1

        if category_missing > 0:
            logger.info(f'Discovered {category_missing} missing GUID entries in {category_tag}')

    return discovered_guids


def _build_populate_worklist(populate_guids: List[str], discovered_store_guids: Dict[str, str],
                             populate_categories: Dict[str, str],
                             allowed_categories: Optional[Set[str]] = None) -> List[str]:
    """Merge GUIDs from populate.txt, discovered store gaps, and populate.json by priority."""
    ordered_guids: List[str] = []
    seen_guids = set()

    for guid in populate_guids:
        category_tag = discovered_store_guids.get(guid) or populate_categories.get(guid)
        if allowed_categories is not None and category_tag not in allowed_categories:
            continue
        if guid not in seen_guids:
            seen_guids.add(guid)
            ordered_guids.append(guid)

    for guid, category_tag in discovered_store_guids.items():
        if allowed_categories is not None and category_tag not in allowed_categories:
            continue
        if guid not in seen_guids:
            seen_guids.add(guid)
            ordered_guids.append(guid)

    for guid, category_tag in populate_categories.items():
        if allowed_categories is not None and category_tag not in allowed_categories:
            continue
        if guid not in seen_guids:
            seen_guids.add(guid)
            ordered_guids.append(guid)

    return ordered_guids


def _save_results(store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str], Set[str], Dict[str, str]]]) -> None:
    """Save all touched output stores."""
    for output_tag, (all_tags, all_guids, _, _) in store_cache.items():
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
    allowed_categories = _parse_category_filters(args.category)
    populate_categories = _load_populate_categories()
    discovered_store_guids = _discover_missing_store_guids(allowed_categories=allowed_categories)
    category_by_guid = dict(populate_categories)
    for guid, category_tag in discovered_store_guids.items():
        category_by_guid.setdefault(guid, category_tag)

    guids_to_populate = _build_populate_worklist(
        populate_list_guids,
        discovered_store_guids,
        populate_categories,
        allowed_categories,
    )
    if not guids_to_populate:
        if allowed_categories:
            logger.error(f"No GUIDs found for requested categories: {sorted(allowed_categories)}")
        else:
            logger.error("No GUIDs found in populate.txt, populate.json, or existing category tag stores")
        return

    category_filter_suffix = f" with category filter {sorted(allowed_categories)}" if allowed_categories else ''
    logger.info(
        f"Loaded {len(populate_list_guids)} GUIDs from populate.txt and "
        f"{len(populate_categories)} GUIDs from populate.json and "
        f"discovered {len(discovered_store_guids)} missing GUID entries from category tags "
        f"({len(guids_to_populate)} unique GUIDs total){category_filter_suffix}"
    )
    store_cache: Dict[str, Tuple[Dict[str, str], Dict[str, str], Set[str], Dict[str, str]]] = {}

    try:
        asyncio.run(populate_guids(guids_to_populate, category_by_guid, store_cache))
    except KeyboardInterrupt:
        logger.info("Interrupted by user - saving current progress")
    except Exception as e:
        logger.error(f"Error during processing: {e}")

    _save_results(store_cache)


if __name__ == "__main__":
    main()
