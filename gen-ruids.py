"""
MapleStory Worlds Resource ID Generator

This script scrapes the Nexon MapleStory Worlds API to collect resource metadata
including GUIDs, tags, and image paths for various game asset categories.
"""

import asyncio
from typing import Dict, Set
import httpx

from maplestory_api import (
    get_request_headers, is_valid_api_response, process_api_item,
    load_json_file, save_json_file, validate_api_token,
    CONCURRENCY, TIMEOUT_SEC, logger
)

COUNT = 100

async def scrape_category(tag: str, all_tags: Dict[str, str], all_guids: Dict[str, str], done_pages: Set[int]) -> None:
    """
    Scrape a specific category for resource data.

    Args:
        tag: Category tag to scrape
        all_tags: Dictionary mapping tags to GUIDs
        all_guids: Dictionary mapping GUIDs to image paths
        done_pages: Set of already processed page numbers
    """
    category = CATEGORIES[tag]
    url = 'https://mverse-api.nexon.com/resource/v1/search'
    base_params = {
        'count': COUNT,
        'page': 1,
        'category': category,
        'sort': 0,
        'resourceIpCode': '',
    }
    headers = get_request_headers()
    page_count = await _get_total_pages(url, base_params, headers)
    if page_count <= 0:
        logger.warning(f"No pages found for category {tag}")
        return

    logger.info(f"Found {page_count} pages for {tag}")

    async with httpx.AsyncClient(headers=headers) as client:
        async def fetch_page(page_num: int) -> httpx.Response:
            params = {**base_params, 'page': page_num + 1}
            return await client.get(url, params=params, timeout=TIMEOUT_SEC)

        for batch_start in range(0, page_count, CONCURRENCY):
            batch_end = min(page_count, batch_start + CONCURRENCY)
            page_indices = [p for p in range(batch_start, batch_end) if p not in done_pages]

            if not page_indices:
                continue

            logger.info(f"Processing batch {batch_start // CONCURRENCY + 1}: pages {page_indices}")
            responses = await asyncio.gather(*[fetch_page(i) for i in page_indices], return_exceptions=True)

            for i, response in enumerate(responses):
                _parse_response(response, page_indices[i], all_tags, all_guids, done_pages, tag)


async def _get_total_pages(url: str, params: Dict, headers: Dict[str, str]) -> int:
    """Get the total number of pages for a category."""
    async with httpx.AsyncClient(headers=headers) as client:
        try:
            response = await client.get(url, params=params, timeout=TIMEOUT_SEC)
            if response.status_code != 200:
                logger.error(f"Failed to get page count: HTTP {response.status_code}")
                return 0

            data = response.json()
            if data.get('code') != 0 or not data.get('data'):
                logger.error(f"API error: {data}")
                return 0

            total_count = data['data'].get('totalMatchCount', 0)
            return (total_count // COUNT) + (1 if total_count % COUNT > 0 else 0)
        except Exception as e:
            logger.error(f"Error getting page count: {e}")
            return 0

def _parse_response(response: httpx.Response, page_index: int, all_tags: Dict[str, str],
                   all_guids: Dict[str, str], done_pages: Set[int], tag: str) -> None:
    """
    Parse a single API response and extract resource data.

    Args:
        response: HTTP response object
        page_index: Index of the processed page
        all_tags: Dictionary to store tag->GUID mappings
        all_guids: Dictionary to store GUID->path mappings
        done_pages: Set to track completed pages
        tag: Current category tag being processed
    """
    if not is_valid_api_response(response):
        return

    done_pages.add(page_index)
    data = response.json()

    for item in data['data']['matches']:
        process_api_item(item, all_tags, all_guids, tag)


def _record_populate_guid(item: Dict, populate_entries: Dict[str, str], category_tag: str) -> None:
    """Store a GUID->category entry for populate metadata generation."""
    guid = item.get('guid', '')
    if not guid:
        logger.warning(f"Populate item missing guid: {item}")
        return

    populate_entries[guid] = category_tag


def _parse_populate_response(response: httpx.Response, page_index: int,
                            populate_entries: Dict[str, str], done_pages: Set[int],
                            category_tag: str) -> None:
    """Parse a populate-category search response into the manifest."""
    if not is_valid_api_response(response):
        return

    done_pages.add(page_index)
    data = response.json()

    for item in data['data']['matches']:
        _record_populate_guid(item, populate_entries, category_tag)


POPULATE_CATEGORIES = {
    'back': '46',
    'tile': '47',
    'object': '48',
    'foothold': '49',
    'rope': '50',
    'ladder': '51',
    'item': '52',
    'monster': '53',
    'npc': '54',
    'trap': '55',
    'chatballoon': '56',
    'nametag': '57',
    'portal': '58',
    'damageskin': '59',
    'maplemap': '60',
    'skeleton': '61',
}

CATEGORIES = {
    'sprite': '0',
    'animationclip': '3',
    # 'atlas': '2',
    'audioclip': '1',
    'sound': '1,19',
    # Character parts:
    'body': '25,26',
    'head': '25,26',
    'hair': '25,27',
    'face': '25,28',
    'cap': '25,29',
    'cape': '25,30',
    'coat': '25,31',
    'glove': '25,32',
    'longcoat': '25,33',
    'pants': '25,34',
    'shoes': '25,35',
    # Accessories:
    'faceaccessory': '25,37',
    'eyeaccessory': '25,38',
    'earaccessory': '25,39',
    'ear': '25,43',
    # Weapons:
    'onehandedweapon': '25,40',
    'twohandedweapon': '25,41',
    'shield': '25,42',
    'subweapon': '25,42',
}


async def scrape_populate_category(tag: str, populate_entries: Dict[str, str], done_pages: Set[int]) -> None:
    """Scrape a populate category and collect GUID->category mappings."""
    category = POPULATE_CATEGORIES[tag]
    url = 'https://mverse-api.nexon.com/resource/v1/search'
    base_params = {
        'count': COUNT,
        'page': 1,
        'category': category,
        'sort': 0,
        'resourceIpCode': '',
    }
    headers = get_request_headers()
    page_count = await _get_total_pages(url, base_params, headers)
    if page_count <= 0:
        logger.warning(f"No pages found for populate category {tag}")
        return

    logger.info(f"Found {page_count} populate pages for {tag}")

    async with httpx.AsyncClient(headers=headers) as client:
        async def fetch_page(page_num: int) -> httpx.Response:
            params = {**base_params, 'page': page_num + 1}
            return await client.get(url, params=params, timeout=TIMEOUT_SEC)

        for batch_start in range(0, page_count, CONCURRENCY):
            batch_end = min(page_count, batch_start + CONCURRENCY)
            page_indices = [p for p in range(batch_start, batch_end) if p not in done_pages]

            if not page_indices:
                continue

            logger.info(f"Processing populate batch {batch_start // CONCURRENCY + 1}: pages {page_indices}")
            responses = await asyncio.gather(*[fetch_page(i) for i in page_indices], return_exceptions=True)

            for i, response in enumerate(responses):
                _parse_populate_response(response, page_indices[i], populate_entries, done_pages, tag)


def _load_existing_data(tag: str) -> tuple[Dict[str, str], Dict[str, str], Set[int]]:
    """Load existing data files for a category."""
    tags_file = f'tags/{tag}_tags.json'
    guids_file = f'guids/{tag}_guids.json'
    done_file = f'done/{tag}_done.json'

    all_tags = load_json_file(tags_file, f"{tag} tags")
    all_guids = load_json_file(guids_file, f"{tag} guids")
    done_pages_data = load_json_file(done_file, f"{tag} done pages")
    done_pages = set(done_pages_data) if isinstance(done_pages_data, list) else set()

    return all_tags, all_guids, done_pages


def _load_populate_data(tag: str) -> tuple[Dict[str, str], Set[int]]:
    """Load existing populate manifest data and resume state for one category."""
    manifest = load_json_file('populate.json', 'populate manifest')
    done_file = f'done/populate_{tag}_done.json'
    done_pages_data = load_json_file(done_file, f"populate {tag} done pages")
    done_pages = set(done_pages_data) if isinstance(done_pages_data, list) else set()
    return manifest, done_pages


def _save_results(tag: str, all_tags: Dict[str, str], all_guids: Dict[str, str], done_pages: Set[int]) -> None:
    """Save results to JSON files."""
    success_count = 0

    if save_json_file(f'guids/{tag}_guids.json', all_guids, f"{tag} guids"):
        success_count += 1

    if save_json_file(f'tags/{tag}_tags.json', all_tags, f"{tag} tags"):
        success_count += 1

    if done_pages and save_json_file(f'done/{tag}_done.json', list(done_pages), f"{tag} done pages"):
        success_count += 1

    if success_count > 0:
        logger.info(f"Saved {len(all_tags)} tags and {len(all_guids)} GUIDs for {tag}")


def _save_populate_results(tag: str, populate_entries: Dict[str, str], done_pages: Set[int]) -> None:
    """Save the populate manifest and resume state for one category."""
    success_count = 0

    if save_json_file('populate.json', populate_entries, 'populate manifest'):
        success_count += 1

    if done_pages and save_json_file(f'done/populate_{tag}_done.json', list(done_pages), f"populate {tag} done pages"):
        success_count += 1

    if success_count > 0:
        logger.info(f"Saved populate manifest with {len(populate_entries)} GUIDs after {tag}")


def main() -> None:
    """Main entry point for the script."""
    try:
        validate_api_token()
    except ValueError:
        return

    for tag in CATEGORIES.keys():
        logger.info(f"Processing category: {tag}")
        all_tags, all_guids, done_pages = _load_existing_data(tag)

        try:
            asyncio.run(scrape_category(tag, all_tags, all_guids, done_pages))
        except KeyboardInterrupt:
            logger.info("Interrupted by user - saving current progress")
        except Exception as e:
            logger.error(f"Error processing {tag}: {e}")

        _save_results(tag, all_tags, all_guids, done_pages)

    for tag in POPULATE_CATEGORIES.keys():
        logger.info(f"Processing populate category: {tag}")
        populate_entries, done_pages = _load_populate_data(tag)

        try:
            asyncio.run(scrape_populate_category(tag, populate_entries, done_pages))
        except KeyboardInterrupt:
            logger.info("Interrupted by user - saving current populate progress")
        except Exception as e:
            logger.error(f"Error processing populate category {tag}: {e}")

        _save_populate_results(tag, populate_entries, done_pages)


if __name__ == "__main__":
    main()
