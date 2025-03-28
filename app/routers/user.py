import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
)
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.utils.chat_action import ChatActionSender
from aiogram.fsm.context import FSMContext
from aiogram.utils.deep_linking import decode_payload, create_start_link
from aiogram.enums import ParseMode
from aiogram.types import LinkPreviewOptions
import g4f
from gcsa.event import Event
from gcsa.google_calendar import GoogleCalendar
from gcsa.recurrence import Recurrence, WEEKLY
from gcsa.calendar import Calendar
from gcsa.acl import AccessControlRule, ACLRole, ACLScopeType

from states import UserStates
from keyboards import schedule_pagination_keyboard, help_keyboard
from services.notification_processor import NotificationManager
from services.search_results import SearchResultList
from services.parsers import group_parser, professor_parser

import asyncio
from functools import partial
from collections import defaultdict
import random

logger = logging.getLogger(__name__)

user_router = Router()

DAYS_OF_WEEK = {
    0: 'Понедельник',
    1: 'Вторник',
    2: 'Среда',
    3: 'Четверг',
    4: 'Пятница',
    5: 'Суббота',
    6: 'Воскресенье'
}

TIME_TO_EMOJI = {
    "08:00": "1️⃣",
    "09:40": "2️⃣",
    "11:30": "3️⃣",
    "13:30": "4️⃣",
    "15:10": "5️⃣",
    "16:50": "6️⃣",
    "18:30": "7️⃣",
    "20:10": "8️⃣"
}

request_template = """
Ты - помощник для студентов и преподавателей, анализирующий расписание занятий и дающий полезные рекомендации.

Вот расписание занятий на день, представленное в формате:

<День недели> - <Номер недели>
<Название предмета>
<Номер пары> <Время начала> - <Время окончания> | <Тип занятия>
<Место проведения>
<ФИО преподавателя>/<Группы>

{schedule}

Проанализируй это расписание и предоставь результат в следующем формате:

*Общий анализ:*

•   Количество пар. / Количество предметов.
•   Типы занятий (лекции, лабораторные, практики).
•   Общее описание загрузки (насколько насыщенный день) + "Цвет дня (3 эмодзи нужного цвета по теме пар)" (4 и больше пар это красный цвет, 3 пары это оранжевый, 2 пары это желтый, 1 пара это зеленый)

*Ключевые моменты:*

•   Особое внимание обрати на пары, которые идут подряд по одному предмету, с разницей в 10-20 минут, если они есть. Отметь, что это может быть как плюсом, так и минусом
•   Обрати внимание на перемещение между корпусами. Укажи, если есть такие перемещения, и на то, что нужно учитывать время на дорогу.
•   Определи, есть ли какие-либо особенности в распределении типов занятий (например, все лабораторные утром, а лекции вечером).
•   Отметь, если есть разрывы между занятиями, которые можно использовать для отдыха или самостоятельной работы.

*Рекомендации (основанные на анализе расписания):*

•   Дай практические советы по подготовке к конкретным типам занятий (например, заранее повторить теорию для лабораторных, подготовиться к практическим заданиям).
•   Дай советы по управлению временем с учетом перемещений между корпусами.
•   Предложи способы оптимизации дня (например, использовать разрывы между занятиями с пользой, приносить с собой воду и перекус).
•   Дай общие советы по поддержанию продуктивности в течение дня.

В анализе используй краткие, четкие и понятные формулировки, избегай сложных и пространных выражений.
Направь анализ таким образом, чтобы он был максимально полезен и практичен для студента при это не душным, который будет это читать
Используй эмоджи. (например цифры кубиками)
"""

MAPS_SEARCH_TEMPLATE = "https://2gis.ru/krasnoyarsk/search/{query}"

GOOGLE_CALENDAR_CREDS_PATH = '.credentials/credentials.json'

calendar_locks = defaultdict(lambda: None)  # Global dictionary to track calendar creation locks

PROGRESS_EMOJIS = ['🎓', '📚', '✏️', '📝', '📖', '🎯', '💡', '⭐️', '📊', '🔍', '📌', '📎', '🎨', '🎬', '🎮', '🎲']
PROGRESS_BAR_LENGTH = 10

def _format_place(place: str) -> str:
    """Format place string from 'корп. "Н" каб. "205"' to 'Н-205'"""
    try:
        # Extract values in quotes using string operations
        parts = place.split('"')
        if len(parts) >= 4:  # Ensure we have both building and room
            building = parts[1].strip()
            room = parts[3].strip()
            return f"{building}-{room}"
        return place  # Return original if can't parse
    except Exception:
        return place  # Return original if any error occurs

async def _render_schedule(message: Message, user_id: int, state: FSMContext, notifyer: NotificationManager, update: bool = False) -> None:
    """
    Unified render function for both group and professor schedules.
    """
    try:
        data = await state.get_data()
        schedule_type = data.get('type')

        if not schedule_type:
            logger.error("Schedule type not found in state data")
            await message.answer("Internal error: missing schedule type")
            return

        render_funcs = {
            'group': _render_group_schedule,
            'professor': _render_professor_schedule
        }

        render_func = render_funcs.get(schedule_type)
        if render_func:
            await render_func(message, user_id, state, notifyer, update)
        else:
            logger.error(f"Unknown schedule type: {schedule_type}")
            await message.answer("Internal error: invalid schedule type")
    except Exception as e:
        logger.error(f"Error rendering schedule: {str(e)}", exc_info=True)
        await message.answer("Failed to render schedule")

async def _render_group_schedule(message: Message, user_id: int, state: FSMContext, notifyer: NotificationManager, update: bool = False) -> None:
    """
    Render group schedule with current state data.
    """
    data = await state.get_data()
    current_tab = data.get('current_tab')
    current_week_index = data.get('current_week_index')
    current_day_index = data.get('current_day_index')
    schedule = data.get('schedule')
    num_max_days = data.get('num_max_days')

    responses = []
    responses.append(f"<a href=\'{await create_start_link(bot = message.bot, payload=schedule.group_name, encode=True)}\'>{schedule.group_name}</a> {schedule.semester}")
    if schedule.source == group_parser.SourceType.PROXY:
        responses.append(f"🔄 Расписание загружено из кэша")
    responses.append(f"")

    if current_tab == 'basic' and schedule.weeks:
        week = schedule.weeks[current_week_index - 1]
        day = week.days[current_day_index - 1]

        # Check if this is today, tomorrow, or yesterday
        current_date = datetime.now()
        current_weekday = current_date.weekday()
        current_week_number = current_date.isocalendar()[1] % 2  # Get 0 or 1 for even/odd week

        is_today = (
            (current_week_number == (current_week_index - 1)) and
            DAYS_OF_WEEK[current_weekday] == day.day_name
        )
        is_tomorrow = (
            (current_week_number == (current_week_index - 1) and
             DAYS_OF_WEEK[(current_weekday + 1) % 7] == day.day_name) or
            (current_week_number != (current_week_index - 1) and
             current_weekday == 6 and
             DAYS_OF_WEEK[0] == day.day_name)
        )
        is_yesterday = (
            (current_week_number == (current_week_index - 1) and
             DAYS_OF_WEEK[(current_weekday - 1) % 7] == day.day_name) or
            (current_week_number != (current_week_index - 1) and
             current_weekday == 0 and
             DAYS_OF_WEEK[6] == day.day_name)
        )

        day_suffix = ""
        if is_today:
            day_suffix = " (Сегодня)"
        elif is_tomorrow:
            day_suffix = " (Завтра)"
        elif is_yesterday:
            day_suffix = " (Вчера)"

        responses.append(f"<b>{day.day_name}{day_suffix}</b> - <b>{week.week_number} Неделя</b>")
        responses.append(f"")

        for lesson in day.lessons:
            lesson_subgroup_text = f"  |  {lesson.subgroup}" if lesson.subgroup else ""
            lesson_type_text = f"  |  {lesson.type}" if lesson.type else ""

            lesson_text = [
                f"{lesson.name.capitalize()}",
                f"<b>{TIME_TO_EMOJI.get(lesson.time.split('-')[0].strip(), '')} {lesson.time}</b>{lesson_type_text}{lesson_subgroup_text}",
                f"{_format_place(lesson.place.split(' / ')[1])} <a href='{MAPS_SEARCH_TEMPLATE.format(query=lesson.place.split(' / ')[0])}'>📍</a>",
                f"<a href='{await create_start_link(bot = message.bot, payload=lesson.professor, encode=True)}'>{lesson.professor}</a>",
            ]
            responses.append("\n".join(lesson_text) + "\n")

    elif current_tab == 'session' and schedule.session:
        responses.append("<b>Расписание сессии:</b>")
        responses.append(f"")
        for day in schedule.session.days:
            # Get relative day label (вчера/сегодня/завтра)
            day_suffix = ""
            today = datetime.now().strftime("%A").lower()
            if day.day_name.lower() == today:
                day_suffix = " (Сегодня)"
            elif day.day_name.lower() == (datetime.now() + timedelta(days=1)).strftime("%A").lower():
                day_suffix = " (Завтра)"
            elif day.day_name.lower() == (datetime.now() - timedelta(days=1)).strftime("%A").lower():
                day_suffix = " (Вчера)"

            responses.append(f"<b>{day.day_name}{day_suffix}:</b>")
            responses.append(f"")
            for lesson in day.lessons:
                lesson_subgroup_text = f"  |  {lesson.subgroup}" if lesson.subgroup else ""
                lesson_type_text = f"  |  {lesson.type}" if lesson.type else ""

                lesson_text = [
                    f"{lesson.name.capitalize()}",
                    f"<b>{lesson.time}</b>{lesson_type_text}{lesson_subgroup_text}",
                    f"{_format_place(lesson.place.split(' / ')[1])} <a href='{MAPS_SEARCH_TEMPLATE.format(query=lesson.place.split(' / ')[0])}'>📍</a>",
                    f"<a href='{await create_start_link(bot = message.bot, payload=lesson.professor, encode=True)}'>{lesson.professor}</a>",
                ]
                responses.append("\n".join(lesson_text) + "\n")
    else:
        responses.append("Расписание занятий отсутствует")


    subscribed = data.get('schedule').group_name in await notifyer.get_subscribed(user_id)
    name = f"{data['schedule'].group_name if data['type'] == 'group' else data['schedule'].person_name}"
    link = await create_start_link(message.bot, name, encode=True)

    if not update:
        await message.answer('⚠️ Внимание! Вышла новая версия бота! @sibsau_timetable_bot')
        await message.answer(
            "\n".join(responses),
            reply_markup=schedule_pagination_keyboard(current_tab, current_week_index, current_day_index, num_max_days, 'group', subscribed, link),
            parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    else:
        await message.edit_text(
            "\n".join(responses),
            reply_markup=schedule_pagination_keyboard(current_tab, current_week_index, current_day_index, num_max_days, 'group', subscribed, link),
            parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )

async def _render_professor_schedule(message: Message, user_id: int, state: FSMContext, notifyer: NotificationManager, update: bool = False) -> None:
    """
    Render professor schedule with current state data.
    """
    data = await state.get_data()
    current_tab = data['current_tab']
    current_week_index = data['current_week_index']
    current_day_index = data['current_day_index']
    schedule = data['schedule']
    num_max_days = data['num_max_days']

    responses = []
    responses.append(f"<a href=\'{await create_start_link(bot = message.bot, payload=schedule.person_name, encode=True)}\'>{schedule.person_name}</a> - {schedule.academic_year}")
    if schedule.source == professor_parser.SourceType.PROXY:
        responses.append(f"🔄 Расписание загружено из кэша")

    responses.append(f"")

    if current_tab == 'basic' and schedule.weeks:
        week = schedule.weeks[current_week_index - 1]
        day = week.days[current_day_index - 1]

        # Check if this is today, tomorrow, or yesterday
        current_date = datetime.now()
        current_weekday = current_date.weekday()
        current_week_number = current_date.isocalendar()[1] % 2  # Get 0 or 1 for even/odd week

        is_today = (
            (current_week_number == (current_week_index - 1)) and
            DAYS_OF_WEEK[current_weekday] == day.day_name
        )
        is_tomorrow = (
            (current_week_number == (current_week_index - 1) and
             DAYS_OF_WEEK[(current_weekday + 1) % 7] == day.day_name) or
            (current_week_number != (current_week_index - 1) and
             current_weekday == 6 and
             DAYS_OF_WEEK[0] == day.day_name)
        )
        is_yesterday = (
            (current_week_number == (current_week_index - 1) and
             DAYS_OF_WEEK[(current_weekday - 1) % 7] == day.day_name) or
            (current_week_number != (current_week_index - 1) and
             current_weekday == 0 and
             DAYS_OF_WEEK[6] == day.day_name)
        )

        day_suffix = ""
        if is_today:
            day_suffix = " (Сегодня)"
        elif is_tomorrow:
            day_suffix = " (Завтра)"
        elif is_yesterday:
            day_suffix = " (Вчера)"

        responses.append(f"<b>{day.day_name}{day_suffix}</b> - <b>{week.week_number} Неделя</b>")
        responses.append(f"")

        for lesson in day.lessons:
            # Create links for each group
            groups = lesson.groups if isinstance(lesson.groups, list) else [lesson.groups]
            group_links = []
            for group in groups:
                link = f"<a href='{await create_start_link(bot = message.bot, payload=group, encode=True)}'>{group}</a>"
                group_links.append(link)

            lesson_subgroup_text = f"  |  {lesson.subgroup}" if lesson.subgroup else ""
            lesson_type_text = f"  |  {lesson.type}" if lesson.type else ""
            responses.append(
                f"{lesson.name.capitalize()}\n"
                f"<b>{TIME_TO_EMOJI.get(lesson.time.split('-')[0].strip(), '')} {lesson.time}</b>{lesson_type_text}{lesson_subgroup_text}\n"
                f"{_format_place(lesson.place.split(' / ')[1])} <a href='{MAPS_SEARCH_TEMPLATE.format(query=lesson.place.split(' / ')[0])}'>📍</a>\n"
                f"{', '.join(group_links)}\n"
            )

    elif current_tab == 'consultations' and schedule.consultations:
        responses.append("<b>Расписание консультаций:</b>")
        responses.append(f"")

        for day in schedule.consultations.days:
            # Get relative day label (вчера/сегодня/завтра)
            day_label = ""
            today = datetime.now().strftime("%A").lower()
            if day.day_name.lower() == today:
                day_label = " (сегодня)"
            elif day.day_name.lower() == (datetime.now() + timedelta(days=1)).strftime("%A").lower():
                day_label = " (завтра)"
            elif day.day_name.lower() == (datetime.now() - timedelta(days=1)).strftime("%A").lower():
                day_label = " (вчера)"

            responses.append(f"<b>{day.day_name}{day_label}</b>")
            responses.append(f"")
            for lesson in day.lessons:
                # Create links for each group
                groups = lesson.groups if isinstance(lesson.groups, list) else [lesson.groups]
                group_links = []
                for group in groups:
                    link = f"<a href='{await create_start_link(bot = message.bot, payload=group, encode=True)}'>{group}</a>"
                    group_links.append(link)

                lesson_subgroup_text = f"  |  {lesson.subgroup}" if lesson.subgroup else ""
                lesson_type_text = f"  |  {lesson.type}" if lesson.type else ""
                responses.append(
                    f"{lesson.name.capitalize()}\n"
                    f"<b>{lesson.time}</b>{lesson_type_text}{lesson_subgroup_text}\n"
                    f"{_format_place(lesson.place.split(' / ')[1])} <a href='{MAPS_SEARCH_TEMPLATE.format(query=lesson.place.split(' / ')[0])}'>📍</a>\n"
                    f"{', '.join(group_links)}\n"
                )

    elif current_tab == 'session' and schedule.session:
        responses.append("<b>Расписание сессии:</b>")
        responses.append(f"")

        for day in schedule.session.days:
            # Get relative day label (вчера/сегодня/завтра)
            day_suffix = ""
            today = datetime.now().strftime("%A").lower()
            if day.day_name.lower() == today:
                day_suffix = " (Сегодня)"
            elif day.day_name.lower() == (datetime.now() + timedelta(days=1)).strftime("%A").lower():
                day_suffix = " (Завтра)"
            elif day.day_name.lower() == (datetime.now() - timedelta(days=1)).strftime("%A").lower():
                day_suffix = " (Вчера)"

            responses.append(f"<b>{day.day_name}{day_suffix}:</b>")
            responses.append(f"")

            for lesson in day.lessons:
                # Create links for each group
                groups = lesson.groups if isinstance(lesson.groups, list) else [lesson.groups]
                group_links = []
                for group in groups:
                    link = f"<a href='{await create_start_link(bot = message.bot, payload=group, encode=True)}'>{group}</a>"
                    group_links.append(link)

                lesson_subgroup_text = f"  |  {lesson.subgroup}" if lesson.subgroup else ""
                lesson_type_text = f"  |  {lesson.type}" if lesson.type else ""
                responses.append(
                    f"{lesson.name.capitalize()}\n"
                    f"<b>{lesson.time}</b>{lesson_type_text}{lesson_subgroup_text}\n"
                    f"{_format_place(lesson.place.split(' / ')[1])} <a href='{MAPS_SEARCH_TEMPLATE.format(query=lesson.place.split(' / ')[0])}'>📍</a>\n"
                    f"{', '.join(group_links)}\n"
                )
    else:
        responses.append("Расписание занятий отсутствует")

    subscribed = data.get('schedule').person_name in await notifyer.get_subscribed(user_id)
    name = f"{data['schedule'].group_name if data['type'] == 'group' else data['schedule'].person_name}"
    link = await create_start_link(message.bot, name, encode=True)
    if not update:
        await message.answer('⚠️ Внимание! Вышла новая версия бота! @sibsau_timetable_bot')
        await message.answer(
            "\n".join(responses),
            reply_markup=schedule_pagination_keyboard(current_tab, current_week_index, current_day_index, num_max_days, 'professor', subscribed, link),
            parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    else:
        await message.edit_text(
            "\n".join(responses),
            reply_markup=schedule_pagination_keyboard(current_tab, current_week_index, current_day_index, num_max_days, 'professor', subscribed, link),
            parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )

async def _calculate_current_day(schedule, week_number: int) -> Tuple[int, int, int]:
    """
    Calculate the current day index based on schedule and week number.
    Returns tuple of (current_day_index, num_max_days, week_number).
    """
    if not schedule.weeks:
        return 1, 1, week_number

    available_days = [day.day_name for day in schedule.weeks[week_number-1].days if day.lessons]
    num_max_days = len(available_days)
    if num_max_days == 0:
        return 1, 1, week_number

    current_day_index = datetime.now().isoweekday() - 1
    current_day_name = DAYS_OF_WEEK[current_day_index]

    if current_day_name in available_days:
        return available_days.index(current_day_name) + 1, num_max_days, week_number

    # Find next available day
    # First check remaining days in current week
    for day in range(current_day_index + 1, 7):
        if DAYS_OF_WEEK[day] in available_days:
            return available_days.index(DAYS_OF_WEEK[day]) + 1, num_max_days, week_number

    # If not found, switch to next week and check from beginning
    next_week = 2 if week_number == 1 else 1
    next_week_days = [day.day_name for day in schedule.weeks[next_week-1].days if day.lessons]

    if next_week_days:
        return 1, len(next_week_days), next_week

    # If still nothing found, return first available day in current week
    return 1, num_max_days, week_number

@user_router.callback_query(F.data, UserStates.in_group_schedule_view)
@user_router.callback_query(F.data, UserStates.in_professor_schedule_view)
async def process_callback(callback: CallbackQuery, state: FSMContext, notifyer: NotificationManager) -> None:
    """
    Universal callback query handler for all keyboard actions.
    """
    try:
        data = await state.get_data()
        action = callback.data

        if not data:
            logger.error("No state data found in callback handler")
            await callback.answer("Session expired, please restart", show_alert=True)
            return

        if action == 'nop':
            await callback.answer()
            return

        no_rerender = False
        answer = ""

        # Process different action types
        if action in ['basic_tab', 'session_tab', 'consultations_tab']:
            data['current_tab'] = action.replace('_tab', '')
            logger.debug(f"Switched to tab: {data['current_tab']}")

        elif action == 'swap_week':
            data['current_week_index'] = 2 if data['current_week_index'] == 1 else 1
            current_day_index, num_max_days, week_number = await _calculate_current_day(data['schedule'], data['current_week_index'])
            data['num_max_days'] = num_max_days
            data['current_day_index'] = min(data['current_day_index'], num_max_days)
            logger.debug(f"Swapped to week: {data['current_week_index']}")

        elif action == 'open_today':
            # Calculate current week number (1 for even week, 2 for odd week)
            current_date = datetime.now()
            current_week = current_date.isocalendar()[1]
            current_week_number = 1 if current_week % 2 == 0 else 2

            new_current_day_index, new_num_max_days, new_week_number = await _calculate_current_day(
                data['schedule'],
                current_week_number  # Use current_week_number instead of data['current_week_index']
            )

            if new_current_day_index == data['current_day_index'] and current_week_number == data['current_week_index']:
                no_rerender = True

            data['current_day_index'] = new_current_day_index
            data['current_week_index'] = current_week_number  # Use current_week_number instead of new_week_number
            logger.debug(f"Changed to day: {data['current_day_index']} and week: {data['current_week_index']}")

        elif action in ['prev_day', 'next_day']:
            day_delta = -1 if action == 'prev_day' else 1
            new_day_index = data['current_day_index'] + day_delta

            # If we hit the boundary, switch weeks (@martin_elcoff idea)
            if new_day_index < 1:
                # Switch to previous week's last day
                data['current_week_index'] = 2 if data['current_week_index'] == 1 else 1
                current_day_index, num_max_days, week_number = await _calculate_current_day(data['schedule'], data['current_week_index'])
                data['num_max_days'] = num_max_days
                data['current_day_index'] = num_max_days
            elif new_day_index > data['num_max_days']:
                # Switch to next week's first day
                data['current_week_index'] = 2 if data['current_week_index'] == 1 else 1
                current_day_index, num_max_days, week_number = await _calculate_current_day(data['schedule'], data['current_week_index'])
                data['num_max_days'] = num_max_days
                data['current_day_index'] = 1
            else:
                data['current_day_index'] = new_day_index
                logger.debug(f"Changed to day: {data['current_day_index']}")

        elif action == 'notify_me':
            schedule_id = data['schedule'].group_name if data['type'] == 'group' else data['schedule'].person_name
            is_subscribed = schedule_id in await notifyer.get_subscribed(callback.from_user.id)

            if is_subscribed:
                await notifyer.unsubscribe(callback.from_user.id, schedule_id)
                answer = "Функция отслеживания отменена"
            else:
                await notifyer.subscribe(callback.from_user.id, schedule_id)
                answer = "Функция отслеживания зарегистрирована"

            logger.info(f"User {callback.from_user.id} {'unsubscribed from' if is_subscribed else 'subscribed to'} {schedule_id}")
            no_rerender = False # need to rerender keyboard

        elif action == 'ai_summary':
            # Check if enough time has passed since last request
            last_request_time = data.get('ai_request_delay')
            current_time = datetime.now()

            if last_request_time:
                time_diff = (current_time - last_request_time).total_seconds()
                TIMEOUT = 20
                if time_diff < TIMEOUT:  # 20 seconds cooldown
                    await callback.answer(f"Подождите {int(TIMEOUT - time_diff)} секунд перед следующим анализом", show_alert=True)
                    return

            # Update last request time
            data['ai_request_delay'] = current_time
            await state.update_data(data)

            async with ChatActionSender.typing(bot=callback.message.bot, chat_id=callback.message.chat.id):
                no_rerender = True
                schedule = data['schedule']
                current_tab = data['current_tab']

                # Prepare schedule text based on current tab and view
                schedule_text = ""
                if current_tab == 'basic' and schedule.weeks:
                    week = schedule.weeks[data['current_week_index'] - 1]
                    day = week.days[data['current_day_index'] - 1]

                    schedule_text = f"{day.day_name} - {week.week_number} Неделя\n\n"
                    for lesson in day.lessons:
                        if data['type'] == 'group':
                            professor_text = f"{lesson.professor}\n" if hasattr(lesson, 'professor') else ""
                            schedule_text += (
                                f"{lesson.name}\n"
                                f"{lesson.time} | {lesson.type if lesson.type else ''}\n"
                                f"{lesson.place}\n"
                                f"{professor_text}\n"
                            )
                        else:  # professor schedule
                            groups = lesson.groups if isinstance(lesson.groups, list) else [lesson.groups]
                            schedule_text += (
                                f"{lesson.name}\n"
                                f"{lesson.time} | {lesson.type if lesson.type else ''}\n"
                                f"{lesson.place}\n"
                                f"{', '.join(groups)}\n\n"
                            )
                elif current_tab == 'session' and schedule.session:
                    schedule_text = "Расписание сессии:\n\n"
                    for day in schedule.session.days:
                        schedule_text += f"{day.day_name}:\n"
                        for lesson in day.lessons:
                            professor_text = f"{lesson.professor}\n" if hasattr(lesson, 'professor') else ""
                            schedule_text += (
                                f"{lesson.name}\n"
                                f"{lesson.time} | {lesson.type if lesson.type else ''}\n"
                                f"{lesson.place}\n"
                                f"{professor_text}\n"
                            )
                elif current_tab == 'consultations' and hasattr(schedule, 'consultations') and schedule.consultations:
                    schedule_text = "Расписание консультаций:\n\n"
                    for day in schedule.consultations.days:
                        schedule_text += f"{day.day_name}:\n"
                        for lesson in day.lessons:
                            groups = lesson.groups if isinstance(lesson.groups, list) else [lesson.groups]
                            schedule_text += (
                                f"{lesson.name}\n"
                                f"{lesson.time} | {lesson.type if lesson.type else ''}\n"
                                f"{lesson.place}\n"
                                f"{', '.join(groups)}\n\n"
                            )

                # if not schedule_text:
                #     await callback.message.answer("Нет данных для анализа")
                #     return

                response = g4f.ChatCompletion.create(
                    model=g4f.models.gpt_4,
                    provider=g4f.Provider.Yqcloud,
                    messages=[{"role": "user", "content": request_template.format(schedule=schedule_text)}],
                    stream=True,
                )
                msg = None
                response_text = ""
                chunk = ""
                chunk_size = 100  # Update every ~100 characters

                for message in response:
                    if isinstance(message, str):
                        chunk += message
                        response_text += message

                        # Only update when we have a substantial chunk of text
                        if len(chunk) >= chunk_size:
                            try:
                                if msg is None:
                                    msg = await callback.message.answer(response_text, parse_mode=ParseMode.MARKDOWN)
                                else:
                                    await msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)
                                chunk = ""  # Reset chunk after successful update
                            except Exception as e:
                                logger.debug(f"Failed to update message: {e}")
                                continue

                # Final update to ensure we show the complete message
                if response_text:
                    try:
                        if msg is None:
                            msg = await callback.message.answer(response_text, parse_mode=ParseMode.MARKDOWN)
                        else:
                            await msg.edit_text(response_text, parse_mode=ParseMode.MARKDOWN)
                    except Exception as e:
                        logger.debug(f"Failed to update final message: {e}")

        elif action == 'get_calendar':
            # Check if enough time has passed since last calendar request
            calendar_request_time = data.get('calendar_request_delay')
            current_time = datetime.now()
            CALENDAR_TIMEOUT = 300  # 5 min seconds cooldown
            CALENDAR_LOCK_TIMEOUT = 300  # 5 min seconds lock timeout

            if calendar_request_time:
                time_diff = (current_time - calendar_request_time).total_seconds()
                if time_diff < CALENDAR_TIMEOUT:
                    await callback.answer(
                        f"Подождите {int(CALENDAR_TIMEOUT - time_diff)} секунд перед следующим созданием календаря",
                        show_alert=True
                    )
                    return

            # Update last calendar request time
            data['calendar_request_delay'] = current_time
            await state.update_data(data)

            schedule = data['schedule']
            calendar_name = schedule.group_name if data['type'] == 'group' else schedule.person_name

            # Check global calendar lock
            if calendar_locks[calendar_name]:
                lock_time = calendar_locks[calendar_name]
                if (current_time - lock_time).total_seconds() < CALENDAR_LOCK_TIMEOUT:
                    await callback.answer(
                        "Календарь уже создается другим пользователем. Пожалуйста, подождите минуту и попробуйте снова.",
                        show_alert=True
                    )
                    return

            # Set global lock for this calendar
            calendar_locks[calendar_name] = current_time

            no_rerender = True
            await callback.answer()

            # Send initial progress message
            progress_message = await callback.message.answer(
                "Создание календаря...\n\n" + "⬜️" * PROGRESS_BAR_LENGTH + "\n\nПодготовка..."
            )

            async with ChatActionSender.typing(bot=callback.message.bot, chat_id=callback.message.chat.id):
                try:
                    target_calendar = await _create_google_calendar(calendar_name, schedule, data['type'], progress_message)

                    # Get shareable link
                    calendar_link = f"https://calendar.google.com/calendar/u/0/r?cid={target_calendar.id}"

                    await progress_message.edit_text(
                        f"✅ Календарь успешно создан!\n\n"
                        f"Ссылка на календарь: {calendar_link}\n\n"
                        "Инструкция:\n"
                        "1. Откройте ссылку\n"
                        "2. Нажмите '+ Добавить календарь'\n"
                        "3. Календарь появится в вашем списке\n\n"
                        "Чтобы обновить календарь, нажмите на значок календаря 📅 еще раз"
                    )

                except Exception as e:
                    logger.error(f"Error creating calendar: {e}", exc_info=True)
                    await progress_message.edit_text("❌ Не удалось создать календарь. Попробуйте позже.")
                finally:
                    # Clear the lock when done
                    calendar_locks[calendar_name] = None

        await state.update_data(data)

        try:
            if answer != "":
                await callback.answer(answer, show_alert=True)
            else:
                await callback.answer()
        except Exception:
            pass

        if not no_rerender:
            await _render_schedule(callback.message, callback.from_user.id, state, notifyer=notifyer, update=True)

    except Exception as e:
        logger.error(f"Error processing callback {callback.data}: {str(e)}", exc_info=True)
        await callback.answer("Failed to process action", show_alert=True)

async def _process_text(search_query: str, message: Message, search_results: SearchResultList, notifyer: NotificationManager, state: FSMContext) -> None:
    """
    Process text input to find and display schedule.

    Handles both group and professor schedule requests:
    """
    if not search_query:
        await message.answer('Пожалуйста, введите название группы или фамилию преподавателя')
        return

    async with ChatActionSender.typing(bot=message.bot, chat_id=message.chat.id):
        try:
            result = search_results.get_by_search_query(search_query)
            if not result:
                logger.info(f"No results found for query: {search_query}")
                await message.answer('Такой группы или преподавателя не найдено')
                return


            if result.type == 'group':
                schedule = await group_parser.get_schedule_from_url(result.url, "cache")

                # Check for and notify about schedule changes
                if schedule.source == group_parser.SourceType.CHANGED:
                    change_messages = []
                    for change in schedule.changes:
                        if change.week_number:
                            change_messages.append(
                                f"Неделя {change.week_number}, {change.day_name}, {change.lesson_time}:\n"
                                f"  {change.field}: {change.old_value} -> {change.new_value}"
                            )
                        else:
                            change_messages.append(
                                f"Расписание сессии, {change.day_name}, {change.lesson_time}:\n"
                                f"  {change.field}: {change.old_value} -> {change.new_value}"
                            )

                    # Send notifications to all subscribers
                    subscribers = await notifyer.get_subscribers(schedule.group_name)
                    if subscribers:
                        change_notification = (
                            f"🔔 Обнаружены изменения в расписании группы {schedule.group_name}:\n\n" +
                            "\n\n".join(change_messages)
                        )
                        for subscriber_id in subscribers:
                            try:
                                await message.bot.send_message(
                                    chat_id=subscriber_id,
                                    text=change_notification,
                                    parse_mode=ParseMode.HTML
                                )
                            except Exception as e:
                                logger.error(f"Failed to send notification to {subscriber_id}: {e}")

                current_date = datetime.now()
                current_week_ = current_date.isocalendar()[1]
                week_is_even = 1 if current_week_ % 2 == 0 else 2  # 1 для четной недели, 2 для нечетной

                current_day_index, num_max_days, week_number = await _calculate_current_day(schedule, week_is_even)

                await state.set_state(UserStates.in_group_schedule_view)
                await state.update_data(
                    current_tab='basic',
                    current_week_index=week_number,
                    current_day_index=current_day_index,
                    max_weeks=len(schedule.weeks),
                    num_max_days=num_max_days,
                    schedule=schedule,
                    type='group'
                )

                await _render_schedule(message, message.from_user.id, state, notifyer=notifyer)

            elif result.type == 'professor':
                schedule = await professor_parser.get_schedule_from_url(result.url, "cache")

                # Check for and notify about schedule changes
                if schedule.source == professor_parser.SourceType.CHANGED:
                    change_messages = []
                    for change in schedule.changes:
                        if change.week_number:
                            change_messages.append(
                                f"Неделя {change.week_number}, {change.day_name}, {change.lesson_time}:\n"
                                f"  {change.field}: {change.old_value} -> {change.new_value}"
                            )
                        else:
                            change_messages.append(
                                f"Расписание сессии, {change.day_name}, {change.lesson_time}:\n"
                                f"  {change.field}: {change.old_value} -> {change.new_value}"
                            )

                    # Send notifications to all subscribers
                    subscribers = await notifyer.get_subscribers(schedule.person_name)
                    if subscribers:
                        change_notification = (
                            f"🔔 Обнаружены изменения в расписании преподавателя {schedule.person_name}:\n\n" +
                            "\n\n".join(change_messages)
                        )
                        for subscriber_id in subscribers:
                            try:
                                await message.bot.send_message(
                                    chat_id=subscriber_id,
                                    text=change_notification,
                                    parse_mode=ParseMode.HTML
                                )
                            except Exception as e:
                                logger.error(f"Failed to send notification to {subscriber_id}: {e}")

                current_date = datetime.now()
                current_week_ = current_date.isocalendar()[1]
                week_is_even = 1 if current_week_ % 2 == 0 else 2  # 1 для четной недели, 2 для нечетной

                current_day_index, num_max_days, week_number = await _calculate_current_day(schedule, week_is_even)

                await state.set_state(UserStates.in_professor_schedule_view)
                await state.update_data(
                    current_tab='basic',
                    current_week_index=week_number,
                    current_day_index=current_day_index,
                    max_weeks=len(schedule.weeks),
                    num_max_days=num_max_days,
                    schedule=schedule,
                    type='professor'
                )

                await _render_schedule(message, message.from_user.id, state, notifyer=notifyer)

        except Exception as e:
            logger.error(f"Error processing schedule for query '{search_query}': {e}")
            await message.answer('Не удалось получить расписание')

@user_router.callback_query(F.data)
async def process_callback(callback: CallbackQuery, state: FSMContext):
    """Handle callback queries"""
    await callback.answer()

@user_router.message(CommandStart(deep_link=True))
@user_router.message(CommandStart())
async def process_cmd_start(message: Message, command: CommandObject, search_results: SearchResultList, notifyer: NotificationManager, state: FSMContext) -> None:
    """Handle /start command"""
    deep_link = command.args
    if deep_link:
        try:
            payload = decode_payload(deep_link) # fix crash if link is modified
        except Exception as e:
            logger.error(f"Error decoding deep link: {e}")
            await message.answer('Неверная ссылка: ссылка повреждена')
            return

        if payload:
            await _process_text(payload, message, search_results, notifyer, state)
        else:
            await message.answer('Неверная ссылка: ссылка пустая')
    else:
        await message.answer('⚠️ Внимание! Вышла новая версия бота! @sibsau_timetable_bot')
        await message.answer('Напиши название группы или фамилию преподавателя, как ты делал(а) это на сайте')

@user_router.message(Command('help'))
async def process_cmd_help(message: Message) -> None:
    """Handle /help command"""
    help_text = (
        'Как пользоваться ботом:\n\n'
        '1. Напишите название группы или фамилию преподавателя\n'
        f'Например: <a href="{await create_start_link(bot = message.bot, payload="bpi2201", encode=True)}">bpi2201</a>, <a href="{await create_start_link(bot = message.bot, payload="тынченко вв", encode=True)}">тынченко вв</a>, <a href="{await create_start_link(bot = message.bot, payload="тынченко св", encode=True)}">тынченко св</a>\n\n'
        '2. В расписании доступны следующие функции:\n'
        '• Переключение между вкладками (Основное/Сессия/Консультации)\n'
        '• Переключение недель (Левый свитч х/2)\n'
        '• Переключение между днями недели (Стрелки влево/вправо)\n'
        '• Кнопка открытия сегодняшнего дня (Кнопка между стрелками x/x)\n'
        '• Отслеживание изменений (Кнопка 🔔)\n'
        '• Получение ссылки на Google-календарь (Кнопка 📅)\n'
        '• AI-анализ расписания (Кнопка 📊)\n'
        '• Скопировать ссылку на расписание (Кнопка 🔁)\n\n'
        '3. Функция отслеживания (beta):\n'
        '• Нажмите на кнопку Отслеживать 🔔 чтобы включить уведомления\n'
        '• Получайте уведомления об изменениях в расписании\n'
        '• Отслеживание работает для всех вкладок (основное, консультации, сессия) и всех дней\n'
        '• Отслеживайте несколько групп или преподавателей одновременно\n\n'
        '4. AI-функции (beta):\n'
        '• Нажмите на кнопку AI Анализ 📊 для анализа расписания\n'
        '• Получите краткую сводку по расписанию\n'
        '• AI анализирует только текущую открытую вкладку и день\n'
        '• AI поможет выделить важные моменты и особенности расписания\n\n'
        '5. Google Календарь (beta):\n'
        '• Нажмите на кнопку 📅 для экспорта расписания\n'
        '• Получите ссылку на календарь с вашим расписанием\n'
        '• Календарь автоматически обновляется при изменениях\n'
        '• Синхронизируйте с телефоном или компьютером\n'
        '• Доступны напоминания о парах\n\n'
        '6. Быстрая навигация:\n'
        '• Нажимайте на названия групп в расписании преподавателя\n'
        '• Нажимайте на имена преподавателей в расписании группы\n'
        '• Нажимайте на 📍 чтобы открыть местоположение в 2GIS\n'
        '• Ссылку можно сохранить, чтобы кликом открывать расписание\n\n'
        'Бот стремится автоматически показать текущий или ближайший следующий день при открытии расписания.\n\n'
    )
    await message.answer('⚠️ Внимание! Вышла новая версия бота! @sibsau_timetable_bot')
    await message.answer(help_text, reply_markup=help_keyboard(), parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True))

@user_router.message(F.text)
async def process_text(message: Message, search_results: SearchResultList, notifyer: NotificationManager, state: FSMContext):
    """Handle text input"""
    await _process_text(message.text, message, search_results, notifyer, state)

async def run_in_executor(func, *args, **kwargs):
    """Helper function to run blocking code in executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(func, *args, **kwargs))

async def _create_google_calendar(calendar_name, schedule, schedule_type, progress_message):
    """Asynchronous wrapper for Google Calendar operations."""
    # Initialize Google Calendar

    # Update progress
    await _update_progress(progress_message, 0.1, "Инициализация календаря...")

    gc = await run_in_executor(GoogleCalendar, credentials_path=GOOGLE_CALENDAR_CREDS_PATH, authentication_flow_port=8000)


    await _update_progress(progress_message, 0.2, "Настройка параметров...")

    # Set calendar settings
    settings = await run_in_executor(gc.get_settings)
    settings.format24_hour_time = True
    settings.locale = 'ru'
    settings.timezone = 'Asia/Krasnoyarsk'


    await _update_progress(progress_message, 0.3, "Создание календаря...")

    # Find or create calendar
    calendars = await run_in_executor(gc.get_calendar_list)
    target_calendar = None
    for calendar in calendars:
        if calendar.summary == calendar_name:
            target_calendar = calendar
            break

    if target_calendar is None:
        calendar = Calendar(
            calendar_name,
            description=f'Расписание {calendar_name}'
        )
        target_calendar = await run_in_executor(gc.add_calendar, calendar)
        logger.info(f"Created new calendar: {calendar_name}")
    else:
        logger.info(f"Using existing calendar: {calendar_name}")


    await _update_progress(progress_message, 0.5, "Очистка старых событий... ~30 секунд")

    # Clear existing events
    events = await run_in_executor(gc.get_events, calendar_id=target_calendar.id)
    for event in events:
        await run_in_executor(gc.delete_event, event, calendar_id=target_calendar.id)
    logger.info("Cleared existing events")


    await _update_progress(progress_message, 0.7, "Добавление расписания... ~30 секунд")

    # Add regular schedule events
    if schedule.weeks:
        # Determine semester dates
        current_date = datetime.now()
        if 9 <= current_date.month <= 12:  # First semester
            semester_start = datetime(current_date.year, 9, 1)
            semester_end = datetime(current_date.year, 12, 30)
        else:  # Second semester
            semester_start = datetime(current_date.year, 2, 10)
            semester_end = datetime(current_date.year, 5, 31)

        # Process each week's schedule
        for week_idx, week in enumerate(schedule.weeks, 1):
            for day in week.days:
                # Get day of week index (0-6)
                day_idx = list(DAYS_OF_WEEK.keys())[list(DAYS_OF_WEEK.values()).index(day.day_name)]

                # Calculate first occurrence of this weekday in the semester
                days_until = (day_idx - semester_start.weekday()) % 7
                first_date = semester_start + timedelta(days=days_until)

                # If this is second week's schedule, add 7 days
                if week_idx != 2:
                    first_date += timedelta(days=7)

                for lesson in day.lessons:
                    # Parse lesson time
                    time_start, time_end = lesson.time.split('-')
                    hour_start, minute_start = map(int, time_start.strip().split(':'))
                    hour_end, minute_end = map(int, time_end.strip().split(':'))

                    # Set event times and adjust by -4 hours
                    event_start = first_date.replace(hour=hour_start, minute=minute_start) - timedelta(hours=4)
                    event_end = first_date.replace(hour=hour_end, minute=minute_end) - timedelta(hours=4)

                    # Create location string with combined info
                    location_parts = []
                    location_parts.append(_format_place(lesson.place.split(' / ')[1]))
                    if lesson.type:
                        location_parts.append(lesson.type)

                    if schedule_type == 'group':
                        location_parts.append(lesson.professor)
                    else:
                        groups = lesson.groups if isinstance(lesson.groups, list) else [lesson.groups]
                        location_parts.append(', '.join(groups))

                    location = ' | '.join(location_parts)

                    # Create recurrence rule (every 2 weeks)
                    recurrence = Recurrence.rule(
                        freq=WEEKLY,
                        interval=2,
                        until=semester_end
                    )

                    # Create and add event
                    event = Event(
                        f"{lesson.name.capitalize()}{f' ({lesson.subgroup})' if lesson.subgroup else ''}",
                        start=event_start,
                        end=event_end,
                        location=location,
                        recurrence=recurrence
                    )
                    await run_in_executor(gc.add_event, event, calendar_id=target_calendar.id)
                    logger.info(f"Added event: {lesson.name} on {day.day_name} at {lesson.time} (Week {week_idx})")

    await _update_progress(progress_message, 1.0, "Готово!")

    # Make calendar public
    rule = AccessControlRule(
        role=ACLRole.READER,
        scope_type=ACLScopeType.DEFAULT
    )
    await run_in_executor(gc.add_acl_rule, rule, calendar_id=target_calendar.id)

    return target_calendar

async def _update_progress(message, progress, status_text):
    """Update progress bar message with random emojis."""
    filled = int(progress * PROGRESS_BAR_LENGTH)
    empty = PROGRESS_BAR_LENGTH - filled

    # Generate progress bar with random emojis for filled portion
    bar = ''
    for _ in range(filled):
        bar += random.choice(PROGRESS_EMOJIS)
    bar += '⬜️' * empty

    text = f"[{bar}]\n{status_text}"
    await message.edit_text(text)

