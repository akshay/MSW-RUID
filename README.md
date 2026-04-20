# MapleStory Worlds Resource ID Tools

This repository contains Python tools for scraping and managing resource metadata from the MapleStory Worlds API. These tools help collect GUIDs, tags, and image paths for various game assets.

## Overview

The tools consist of two main scripts:

- **`gen-ruids.py`**: Bulk scraper that discovers resources by category
- **`pop-ruids.py`**: Targeted scraper that fetches metadata for specific GUIDs

Both scripts interact with the Nexon MapleStory Worlds API to collect resource information including:
- Resource GUIDs (Globally Unique Identifiers)
- Display names (tags)
- Image file paths
- Asset metadata

## Prerequisites

### Python Dependencies

```bash
pip install httpx asyncio
```

### Environment Setup

Set your API token as an environment variable:

```bash
export API_TOKEN="your_api_token_here"
```

The API token should be obtained from the MapleStory Worlds platform and is required for authenticating with the Nexon API.
In the Network tab in Google Chrome (or other browser), you can obtain it from the `X-Mverse-ifwt` header.
You must be logged into the [MapleStory Worlds API](https://maplestoryworlds.nexon.com/en/resource?page=1&category=0&subCategory=-1&type=text) to obtain this header.

## Scripts

### gen-ruids.py - Category-based Resource Generator

This script scrapes resources by category, discovering all available assets within specified categories.
It also builds `populate.json` for hidden resource categories that need GUID-specific population later.

#### Features

- **Category-based scraping**: Processes different asset categories (sprites, sounds, character parts, etc.)
- **Concurrent processing**: Uses async/await with configurable concurrency for efficient API usage
- **Resume capability**: Tracks completed pages and can resume interrupted scraping sessions

#### Usage

```bash
python gen-ruids.py
```

#### Configuration

Edit the `CATEGORIES` dictionary in the script to enable/disable specific categories:

```python
CATEGORIES = {
    'sprite': '0',           # Enable sprite category
    # 'sound': '1,19',      # Uncomment to enable sound category
    # 'hair': '25,27',      # Uncomment to enable hair category
    # Add other categories as needed
}
```

#### Output Files

For each category (e.g., 'sprite'):
- `tags/sprite_tags.json`: Maps display names to GUIDs
- `guids/sprite_guids.json`: Maps GUIDs to image paths
- `done/sprite_done.json`: Tracks completed pages for resume functionality

For populate-only categories (for example `chatballoon`, `nametag`, `damageskin`):
- `populate.json`: Maps GUIDs to category names used by `pop-ruids.py`
- `done/populate_<category>_done.json`: Tracks completed populate-manifest pages

### pop-ruids.py - GUID-specific Resource Populator

This script fetches detailed metadata for specific GUIDs listed in `populate.txt`, discovered in `populate.json`, and auto-detected from category tag stores when a GUID is missing from the matching `guids/*_guids.json` file.

This is useful for the following categories, which are not listed on the MapleStory Worlds website:
- Name tag RUIDs
- Chat balloon RUIDs
- Tileset RUIDs

#### Usage

1. Run `gen-ruids.py` when you want to refresh `populate.json` for hidden categories.

2. Optionally create or edit `populate.txt` with additional GUIDs to process:

```text
# This is a comment - lines starting with # are ignored
guid-1234-5678-9abc-def0
guid-abcd-efgh-ijkl-mnop
# Another comment
guid-qrst-uvwx-yz12-3456
```

3. Run the script:

```bash
python pop-ruids.py
```

To limit processing to specific manifest categories:

```bash
python pop-ruids.py --category back
python pop-ruids.py --category back,object
python pop-ruids.py -c back -c object
```

#### Output Files

- `tags/<category>_tags.json`: Manifest-backed GUIDs are written to their category file, such as `tags/back_tags.json`
- `guids/<category>_guids.json`: Manifest-backed GUIDs are written to their category file, such as `guids/back_guids.json`
- `tags/populate_tags.json` and `guids/populate_guids.json`: Fallback outputs for GUIDs that come only from `populate.txt` and have no category in `populate.json`

When a GUID has category metadata in `populate.json`, `pop-ruids.py` preserves the API tag name when it already matches the category, such as `portal-1`.
`pop-ruids.py` processes the union of GUIDs from `populate.txt`, `populate.json`, and any `tags/*_tags.json` entries whose GUID is still missing from the matching `guids/*_guids.json`.
When `--category` is provided, only GUIDs whose discovered category matches the requested values are processed, whether that category came from `populate.json` or an existing tag store.

## Configuration

### Performance Tuning

Both scripts include configurable parameters:

```python
COUNT = 100          # Items per API request page
CONCURRENCY = 8      # Number of concurrent requests
TIMEOUT_SEC = 15.0   # Request timeout in seconds
```

## Directory Structure

```
ruid/
├── maplestory_api.py         # Shared API utilities module
├── gen-ruids.py              # Category-based scraper
├── pop-ruids.py              # GUID-specific scraper
├── populate.json             # GUID->category manifest for populate flow
├── populate.txt              # Input file for pop-ruids.py
├── tags/                     # Tag-to-GUID mappings
│   ├── sprite_tags.json
│   ├── back_tags.json
│   └── populate_tags.json
├── guids/                    # GUID-to-path mappings
│   ├── sprite_guids.json
│   ├── back_guids.json
│   └── populate_guids.json
└── done/                     # Progress tracking
    └── sprite_done.json
```

## API Reference

### MapleStory Worlds API

The scripts interact with the Nexon MapleStory Worlds API:

- **Base URL**: `https://mverse-api.nexon.com/resource/v1/search`
- **Authentication**: Bearer token via `X-Mverse-ifwt` header
- **Rate Limiting**: Implemented via concurrent request limits

### Data Format

#### Tags JSON Format
```json
{
  "sprite-example-001": "guid-1234-5678-9abc-def0",
  "sprite-example-002": "guid-abcd-efgh-ijkl-mnop"
}
```

#### GUIDs JSON Format
```json
{
  "guid-1234-5678-9abc-def0": "character/face/001.img/default",
  "guid-abcd-efgh-ijkl-mnop": "map/background/forest.img"
}
```

## Error Handling

The scripts include comprehensive error handling for:

- **Network issues**: Automatic retry and timeout handling
- **API errors**: Graceful handling of API response errors
- **Interruption**: Ctrl+C handling with progress saving

## Contributing

Feel free to raise issues in case any logic is incorrect.

## License

This project is intended for research and development purposes.

## Troubleshooting

**HTTP timeout errors / JSON decode errors**
- Usually indicates rate limiting - reduce `CONCURRENCY` to lower network load
- Verify API endpoint is accessible and the API_TOKEN is valid
