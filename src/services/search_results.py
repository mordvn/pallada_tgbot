import logging
import asyncio
import json
import os
from typing import Optional, List, Dict, TypedDict
from rapidfuzz import fuzz
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from enum import Enum

from services.parsers import group_parser
from services.parsers import professor_parser

logger = logging.getLogger(__name__)

class SearchResultDict(TypedDict):
    name: str
    type: str
    id: int
    url: str

class SearchResult:
    def __init__(self, name: str, type: str, id: int, url: str):
        self.name = name
        self.type = type
        self.id = id
        self.url = url

    def __repr__(self) -> str:
        return f"SearchResult(name='{self.name}', type='{self.type}', id={self.id}, url='{self.url}')"

    def to_dict(self) -> SearchResultDict:
        """Convert SearchResult to dictionary format"""
        return {
            "name": self.name,
            "type": self.type,
            "id": self.id,
            "url": self.url
        }

def transliterate(text: str) -> str:
    """
    Transliterate text from Cyrillic to Latin characters.
    """

    # Mapping for transliteration# Mapping for transliteration
    CYRILLIC_TO_LATIN: Dict[str, str] = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'kh', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'shch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya'
    }

    return ''.join(CYRILLIC_TO_LATIN.get(char, char) for char in text.lower())

class SourceType(Enum):
    PROXY = "PROXY"  # From filesystem cache
    RAW = "RAW"      # From network request

@dataclass
class SearchResultList:
    results: List[SearchResult] = field(default_factory=list)
    source: SourceType = field(default=SourceType.RAW)
    source_date: datetime = field(default_factory=datetime.now)

    def get_by_search_query(self, query: str) -> Optional[SearchResult]:
        """
        Search for data in the list using fuzzy string matching.
        """
        # Minimum similarity percentage (0-100) required for a match to be considered valid
        MINIMUM_SIMILARITY_PERCENTAGE: int = 30

        if not query or not self.results:
            return None

        best_match_score = 0
        best_match_record = None
        query_lower = query.lower()
        latin_query = transliterate(query_lower)

        for record in self.results:
            name = record['name'].lower()

            # Check for exact match first
            if query_lower == name:
                return SearchResult(**record)

            # Apply fuzzy matching
            latin_name = transliterate(name)
            score = max(
                fuzz.ratio(query_lower, name),
                fuzz.ratio(latin_query, latin_name)
            )

            if score > best_match_score:
                best_match_score = score
                best_match_record = SearchResult(**record)

        if best_match_record and best_match_score > MINIMUM_SIMILARITY_PERCENTAGE:
            return best_match_record

        return None

def _save_to_cache(results: SearchResultList, directory: Path, filename: str):
    """Save search results to cache file"""
    cache_path = directory / filename
    with open(cache_path, 'w', encoding='utf-8') as f:
        data = {
            'results': [vars(r) for r in results.results],
            'source': results.source.value,  # Save enum value
            'source_date': results.source_date.isoformat()
        }
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_from_cache(cache_path: Path) -> SearchResultList:
    """Load search results from cache file"""
    with open(cache_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        results = SearchResultList(
            results=[SearchResult(**r) for r in data['results']],
            source=SourceType(data['source']),  # Convert string back to enum
            source_date=datetime.fromisoformat(data['source_date'])
        )
        return results


# Constants for ID ranges
PROFESSOR_ID_START = 1
PROFESSOR_ID_END = 10000
GROUP_ID_START = 1
GROUP_ID_END = 20000

async def fetch_database(proxy_filepath: Optional[str] = None) -> SearchResultList:
    """
    Asynchronously creates and returns a database of groups and professors.
    If proxy_filepath is provided, attempts to load from file first.
    """
    # Try to load from proxy file if path is provided
    if proxy_filepath and os.path.exists(proxy_filepath):
        try:
            with open(proxy_filepath, 'r', encoding='utf-8') as f:
                return SearchResultList(json.load(f), source=SourceType.PROXY)
        except Exception as e:
            logger.error(f"Failed to load proxy file {proxy_filepath}: {str(e)}")

    # Fetch data from network
    data: List[SearchResultDict] = []

    async def fetch_group(id: int):
        url = f"https://timetable.pallada.sibsau.ru/timetable/group/{id}"
        try:
            schedule = await group_parser.get_schedule_from_url(url)
            return SearchResultDict(
                name=schedule.group_name,
                type="group",
                id=id,
                url=url
            )
        except Exception as e:
            logger.error(f"Error fetching group {id}: {str(e)}")
            return None

    async def fetch_professor(id: int):
        url = f"https://timetable.pallada.sibsau.ru/timetable/professor/{id}"
        try:
            schedule = await professor_parser.get_schedule_from_url(url)
            return SearchResultDict(
                name=schedule.person_name,
                type="professor",
                id=id,
                url=url
            )
        except Exception as e:
            logger.error(f"Error fetching professor {id}: {str(e)}")
            return None

    # Create tasks for all fetches
    tasks = []
    for id in range(GROUP_ID_START, GROUP_ID_END):
        tasks.append(fetch_group(id))
    for id in range(PROFESSOR_ID_START, PROFESSOR_ID_END):
        tasks.append(fetch_professor(id))

    # Wait for all tasks to complete
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out None results and exceptions
    data = [r for r in results if isinstance(r, dict)]

    result_list = SearchResultList(data, source=SourceType.RAW)

    # Save to proxy file if path is provided
    if proxy_filepath:
        try:
            # Create directory only if proxy_filepath has a directory component
            directory = os.path.dirname(proxy_filepath)
            if directory:
                os.makedirs(directory, exist_ok=True)

            with open(proxy_filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save proxy file {proxy_filepath}: {str(e)}")

    return result_list

# Keep the synchronous version for compatibility
def fetch_database_sync(proxy_filepath: Optional[str] = None) -> SearchResultList:
    """
    Synchronously creates and returns a database of groups and professors.
    """
    # Try to load from proxy file if path is provided
    if proxy_filepath and os.path.exists(proxy_filepath):
        try:
            with open(proxy_filepath, 'r', encoding='utf-8') as f:
                return SearchResultList(json.load(f), source=SourceType.PROXY)
        except Exception as e:
            logger.warning(f"Failed to load proxy file {proxy_filepath}: {str(e)}")

    data: List[SearchResultDict] = []

    for id in range(GROUP_ID_START, GROUP_ID_END):
        url = f"https://timetable.pallada.sibsau.ru/timetable/group/{id}"
        try:
            schedule = group_parser.get_schedule_from_url_sync(url)
            data.append(SearchResultDict(
                name=schedule.group_name,
                type="group",
                id=id,
                url=url
            ))
        except Exception as e:
            logger.warning(f"Error fetching group {id}: {str(e)}")
            continue

    for id in range(PROFESSOR_ID_START, PROFESSOR_ID_END):
        url = f"https://timetable.pallada.sibsau.ru/timetable/professor/{id}"
        try:
            schedule = professor_parser.get_schedule_from_url_sync(url)
            data.append(SearchResultDict(
                name=schedule.person_name,
                type="professor",
                id=id,
                url=url
            ))
        except Exception as e:
            logger.warning(f"Error fetching professor {id}: {str(e)}")
            continue

    result_list = SearchResultList(data, source=SourceType.RAW)

    # Save to proxy file if path is provided
    if proxy_filepath:
        try:
            # Create directory only if proxy_filepath has a directory component
            directory = os.path.dirname(proxy_filepath)
            if directory:
                os.makedirs(directory, exist_ok=True)

            with open(proxy_filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save proxy file {proxy_filepath}: {str(e)}")

    return result_list

