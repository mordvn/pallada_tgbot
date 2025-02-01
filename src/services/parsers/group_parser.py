import logging
import aiohttp
from bs4 import BeautifulSoup
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import re
from pathlib import Path
import json
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)

@dataclass
class Lesson:
    time: str
    name: str
    professor: str
    place: str
    subgroup: Optional[str] = None

@dataclass
class DaySchedule:
    day_name: str
    lessons: List[Lesson] = field(default_factory=list)

@dataclass
class WeekSchedule:
    week_number: int
    days: List[DaySchedule] = field(default_factory=list)

@dataclass
class SessionSchedule:
    days: List[DaySchedule] = field(default_factory=list)

class SourceType(Enum):
    PROXY = "proxy"     # From filesystem cache
    RAW = "raw"         # From network request
    CHANGED = "changed" # When changes detected between cache and new data

@dataclass
class Change:
    field: str
    old_value: Any
    new_value: Any
    lesson_time: str
    day_name: str
    week_number: Optional[int] = None  # None for session schedule

@dataclass
class Schedule:
    group_name: str
    semester: str
    weeks: List[WeekSchedule] = field(default_factory=list)
    session: Optional[SessionSchedule] = None
    source: SourceType = field(default=SourceType.RAW)
    source_date: datetime = field(default_factory=datetime.now)
    changes: List[Change] = field(default_factory=list)

async def _parse_schedule(html_content: str) -> Schedule:
    soup = BeautifulSoup(html_content, 'html.parser')

    # Extract group name and semester info
    title_element = soup.find('h3', class_='text-center bold')
    if not title_element:
        raise ValueError("Could not find title element")

    title_text = title_element.get_text(strip=True)
    parts = title_text.split('"')
    if len(parts) != 3:
        raise ValueError(f"Unexpected title format: {title_text}")

    group_name = parts[1]
    semester = parts[2].strip()
    semester = re.sub(r'г\.', '', semester).strip()

    schedule = Schedule(group_name=group_name, semester=semester)

    # Find all week tabs
    week_tabs = soup.select('ul.nav.nav-pills.navbar-right.n_week li a')

    for week_tab in week_tabs:
        week_number = int(week_tab.text.split()[0])
        week_id = week_tab['href'].replace('#', '')
        week_schedule = WeekSchedule(week_number=week_number)

        # Find the corresponding week content
        week_content = soup.find('div', id=week_id)
        if week_content:
            # Find all days within the week
            day_divs = week_content.find_all('div', class_=lambda x: x and 'day' in x)
            for day_div in day_divs:
                day_name = day_div.find('div', class_='name text-center').text.strip().split()[0]
                day_schedule = DaySchedule(day_name=day_name)

                # Find all lessons within the day
                lesson_lines = day_div.find('div', class_='body').find_all('div', class_='line')
                for lesson_line in lesson_lines:
                    time_div = lesson_line.find('div', class_='time text-center')
                    time = time_div.find(class_="hidden-xs").text.strip().replace("\n", "") if time_div.find(
                        class_="hidden-xs") else time_div.find(class_="visible-xs").text.strip().replace("\n",
                                                                                                         "").replace(
                        "<br>", "-")

                    discipline_div = lesson_line.find('div', class_='discipline')
                    name = discipline_div.find('span', class_='name').text.strip() if discipline_div.find('span',
                                                                                                          class_='name') else 'N/A'
                    professor_link = discipline_div.find('a')
                    professor = professor_link.text.strip() if professor_link else 'N/A'

                    place_link = discipline_div.find('a', title=True)
                    place_separator = " / "
                    if place_link:
                        place_title = place_link['title']
                        place_text = place_link.text
                        place = f"{place_title}{place_separator}{place_text}"
                    else:
                        place = 'N/A'

                    subgroup_element = discipline_div.find('li', class_='bold num_pdgrp')
                    subgroup = subgroup_element.text.strip() if subgroup_element else None

                    lesson = Lesson(time=time, name=name, professor=professor, place=place, subgroup=subgroup)
                    day_schedule.lessons.append(lesson)

                week_schedule.days.append(day_schedule)

        schedule.weeks.append(week_schedule)

    # Parse session schedule
    session_tab = soup.find('div', id='session_tab')
    if session_tab:
        session_schedule = SessionSchedule()
        day_divs = session_tab.find_all('div', class_='day')
        for day_div in day_divs:
            day_name = day_div.find('div', class_='name text-center').text.strip().split()[0]
            day_schedule = DaySchedule(day_name=day_name)

            lesson_lines = day_div.find('div', class_='body').find_all('div', class_='line')
            for lesson_line in lesson_lines:
                time_div = lesson_line.find('div', class_='time text-center')
                if time_div:
                    time_text_element = time_div.find('div')
                    if time_text_element:
                        time = time_text_element.get_text(strip=True).strip()
                        time = time.split(' ')[-1]
                    else:
                        time = ""
                else:
                    time = ""
                # 9.01.2025 11:15 fix for single time

                discipline_div = lesson_line.find('div', class_='discipline')
                name = discipline_div.find('span', class_='name').text.strip() if discipline_div.find('span',
                                                                                                      class_='name') else 'N/A'
                professor_link = discipline_div.find('a')
                professor = professor_link.text.strip() if professor_link else 'N/A'

                place_link = discipline_div.find('a', title=True)
                place_separator = " / "
                if place_link:
                    place_title = place_link['title']
                    place_text = place_link.text
                    place = f"{place_title}{place_separator}{place_text}"
                else:
                    place = 'N/A'

                subgroup_element = discipline_div.find('li', class_='bold num_pdgrp')
                subgroup = subgroup_element.text.strip() if subgroup_element else None
                lesson = Lesson(time=time, name=name, professor=professor, place=place, subgroup=subgroup)
                day_schedule.lessons.append(lesson)
            session_schedule.days.append(day_schedule)
        schedule.session = session_schedule

    return schedule

def _parse_schedule_sync(html_content: str) -> Schedule:
    soup = BeautifulSoup(html_content, 'html.parser')

    # Extract group name and semester info
    title_element = soup.find('h3', class_='text-center bold')
    if not title_element:
        raise ValueError("Could not find title element")

    title_text = title_element.get_text(strip=True)
    parts = title_text.split('"')
    if len(parts) != 3:
        raise ValueError(f"Unexpected title format: {title_text}")

    group_name = parts[1]
    semester = parts[2].strip()
    semester = re.sub(r'г\.', '', semester).strip()

    schedule = Schedule(group_name=group_name, semester=semester)

    # Find all week tabs
    week_tabs = soup.select('ul.nav.nav-pills.navbar-right.n_week li a')

    for week_tab in week_tabs:
        week_number = int(week_tab.text.split()[0])
        week_id = week_tab['href'].replace('#', '')
        week_schedule = WeekSchedule(week_number=week_number)

        # Find the corresponding week content
        week_content = soup.find('div', id=week_id)
        if week_content:
            # Find all days within the week
            day_divs = week_content.find_all('div', class_=lambda x: x and 'day' in x)
            for day_div in day_divs:
                day_name = day_div.find('div', class_='name text-center').text.strip().split()[0]
                day_schedule = DaySchedule(day_name=day_name)

                # Find all lessons within the day
                lesson_lines = day_div.find('div', class_='body').find_all('div', class_='line')
                for lesson_line in lesson_lines:
                    time_div = lesson_line.find('div', class_='time text-center')
                    time = time_div.find(class_="hidden-xs").text.strip().replace("\n", "") if time_div.find(
                        class_="hidden-xs") else time_div.find(class_="visible-xs").text.strip().replace("\n",
                                                                                                         "").replace(
                        "<br>", "-")

                    discipline_div = lesson_line.find('div', class_='discipline')
                    name = discipline_div.find('span', class_='name').text.strip() if discipline_div.find('span',
                                                                                                          class_='name') else 'N/A'
                    professor_link = discipline_div.find('a')
                    professor = professor_link.text.strip() if professor_link else 'N/A'

                    place_link = discipline_div.find('a', title=True)
                    place_separator = " / "
                    if place_link:
                        place_title = place_link['title']
                        place_text = place_link.text
                        place = f"{place_title}{place_separator}{place_text}"
                    else:
                        place = 'N/A'

                    subgroup_element = discipline_div.find('li', class_='bold num_pdgrp')
                    subgroup = subgroup_element.text.strip() if subgroup_element else None

                    lesson = Lesson(time=time, name=name, professor=professor, place=place, subgroup=subgroup)
                    day_schedule.lessons.append(lesson)

                week_schedule.days.append(day_schedule)

        schedule.weeks.append(week_schedule)

    # Parse session schedule
    session_tab = soup.find('div', id='session_tab')
    if session_tab:
        session_schedule = SessionSchedule()
        day_divs = session_tab.find_all('div', class_='day')
        for day_div in day_divs:
            day_name = day_div.find('div', class_='name text-center').text.strip().split()[0]
            day_schedule = DaySchedule(day_name=day_name)

            lesson_lines = day_div.find('div', class_='body').find_all('div', class_='line')
            for lesson_line in lesson_lines:
                time_div = lesson_line.find('div', class_='time text-center')
                if time_div:
                    time_text_element = time_div.find('div')
                    if time_text_element:
                        time = time_text_element.get_text(strip=True).strip()
                        time = time.split(' ')[-1]
                    else:
                        time = ""
                else:
                    time = ""
                # 9.01.2025 11:15 fix for single time

                discipline_div = lesson_line.find('div', class_='discipline')
                name = discipline_div.find('span', class_='name').text.strip() if discipline_div.find('span',
                                                                                                      class_='name') else 'N/A'
                professor_link = discipline_div.find('a')
                professor = professor_link.text.strip() if professor_link else 'N/A'

                place_link = discipline_div.find('a', title=True)
                place_separator = " / "
                if place_link:
                    place_title = place_link['title']
                    place_text = place_link.text
                    place = f"{place_title}{place_separator}{place_text}"
                else:
                    place = 'N/A'

                subgroup_element = discipline_div.find('li', class_='bold num_pdgrp')
                subgroup = subgroup_element.text.strip() if subgroup_element else None
                lesson = Lesson(time=time, name=name, professor=professor, place=place, subgroup=subgroup)
                day_schedule.lessons.append(lesson)
            session_schedule.days.append(day_schedule)
        schedule.session = session_schedule

    return schedule

def _generate_cache_filename(url: str) -> str:
    """Generate a consistent filename for caching based on URL"""
    # Extract group ID from URL
    group_id = url.split('/')[-1]
    return f"group_{group_id}.json"

def _save_schedule_to_cache(schedule: Schedule, directory: Path, filename: str):
    """Save schedule to cache file"""
    cache_path = directory / filename
    with open(cache_path, 'w', encoding='utf-8') as f:
        data = {
            'group_name': schedule.group_name,
            'semester': schedule.semester,
            'weeks': [{'week_number': w.week_number,
                      'days': [{'day_name': d.day_name,
                               'lessons': [vars(l) for l in d.lessons]}
                              for d in w.days]}
                     for w in schedule.weeks],
            'session': {'days': [{'day_name': d.day_name,
                                'lessons': [vars(l) for l in d.lessons]}
                               for d in schedule.session.days]} if schedule.session else None,
            'source': schedule.source.value,
            'source_date': schedule.source_date.isoformat()
        }
        json.dump(data, f, ensure_ascii=False, indent=2)

def _load_schedule_from_cache(cache_path: Path) -> Schedule:
    """Load schedule from cache file"""
    with open(cache_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        schedule = Schedule(
            group_name=data['group_name'],
            semester=data['semester'],
            source=SourceType.PROXY,
            source_date=datetime.fromisoformat(data['source_date'])
        )

        # Reconstruct weeks
        for week_data in data['weeks']:
            week = WeekSchedule(week_number=week_data['week_number'])
            for day_data in week_data['days']:
                day = DaySchedule(day_name=day_data['day_name'])
                for lesson_data in day_data['lessons']:
                    lesson = Lesson(**lesson_data)
                    day.lessons.append(lesson)
                week.days.append(day)
            schedule.weeks.append(week)

        # Reconstruct session if exists
        if data['session']:
            session = SessionSchedule()
            for day_data in data['session']['days']:
                day = DaySchedule(day_name=day_data['day_name'])
                for lesson_data in day_data['lessons']:
                    lesson = Lesson(**lesson_data)
                    day.lessons.append(lesson)
                session.days.append(day)
            schedule.session = session

        return schedule

def _compare_lessons(old_lesson: Lesson, new_lesson: Lesson, day_name: str, week_number: Optional[int] = None) -> List[Change]:
    """Compare two lessons and return list of changes"""
    changes = []
    fields_to_compare = ['time', 'name', 'professor', 'place', 'subgroup']

    for field in fields_to_compare:
        old_value = getattr(old_lesson, field)
        new_value = getattr(new_lesson, field)
        if old_value != new_value:
            changes.append(Change(
                field=field,
                old_value=old_value,
                new_value=new_value,
                lesson_time=old_lesson.time,
                day_name=day_name,
                week_number=week_number
            ))
    return changes

def _compare_schedules(old_schedule: Schedule, new_schedule: Schedule) -> List[Change]:
    """Compare two schedules and return list of changes"""
    changes = []

    # Compare regular weeks
    for old_week, new_week in zip(old_schedule.weeks, new_schedule.weeks):
        for old_day, new_day in zip(old_week.days, new_week.days):
            for old_lesson, new_lesson in zip(old_day.lessons, new_day.lessons):
                changes.extend(_compare_lessons(
                    old_lesson,
                    new_lesson,
                    old_day.day_name,
                    old_week.week_number
                ))

    # Compare session schedule if exists
    if old_schedule.session and new_schedule.session:
        for old_day, new_day in zip(old_schedule.session.days, new_schedule.session.days):
            for old_lesson, new_lesson in zip(old_day.lessons, new_day.lessons):
                changes.extend(_compare_lessons(
                    old_lesson,
                    new_lesson,
                    old_day.day_name
                ))

    return changes

async def get_schedule_from_url(url: str, directory: Optional[str] = None) -> Schedule:
    """
    Fetches schedule from URL or loads from cache if available.
    If changes are detected between cache and new data, returns schedule with CHANGED source
    and list of changes.
    """
    cached_schedule = None

    if directory:
        cache_dir = Path(directory)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / _generate_cache_filename(url)

        if cache_file.exists():
            cached_schedule = _load_schedule_from_cache(cache_file)


    # Fetch new data
    conn = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=conn) as session:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                html_content = await response.text()
                new_schedule = await _parse_schedule(html_content)
                new_schedule.source = SourceType.RAW

                # Compare with cache if exists
                if cached_schedule:
                    changes = _compare_schedules(cached_schedule, new_schedule)
                    if changes:
                        new_schedule.source = SourceType.CHANGED
                        new_schedule.changes = changes
                    else:
                        new_schedule.source = SourceType.PROXY

                # Save to cache, overwriting old cache
                if directory:
                    _save_schedule_to_cache(new_schedule, cache_dir, cache_file.name)

                return new_schedule

        except aiohttp.ClientError as e:
            raise Exception(f"Failed to fetch URL: {e}")

def get_schedule_from_url_sync(url: str, directory: Optional[str] = None) -> Schedule:
    """
    Synchronous version of get_schedule_from_url.
    Fetches schedule from URL or loads from cache if available.
    If changes are detected between cache and new data, returns schedule with CHANGED source
    and list of changes.
    """
    import requests

    cached_schedule = None

    if directory:
        cache_dir = Path(directory)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / _generate_cache_filename(url)

        if cache_file.exists():
            cached_schedule = _load_schedule_from_cache(cache_file)

    try:
        response = requests.get(url, verify=False)
        response.raise_for_status()
        new_schedule = _parse_schedule_sync(response.text)
        new_schedule.source = SourceType.RAW

        # Compare with cache if exists
        if cached_schedule:
            changes = _compare_schedules(cached_schedule, new_schedule)
            if changes:
                new_schedule.source = SourceType.CHANGED
                new_schedule.changes = changes
            else:
                new_schedule.source = SourceType.PROXY

        # Save to cache, overwriting old cache
        if directory:
            _save_schedule_to_cache(new_schedule, cache_dir, cache_file.name)

        return new_schedule

    except requests.exceptions.RequestException as e:
        raise Exception(f"Failed to fetch URL: {e}")


