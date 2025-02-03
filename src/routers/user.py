import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from aiogram import Router, F
from aiogram.types import (
    CallbackQuery,
    Message,
    LabeledPrice,
    PreCheckoutQuery,
)
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.utils.chat_action import ChatActionSender
from aiogram.fsm.context import FSMContext
from aiogram.utils.deep_linking import decode_payload, create_start_link
from aiogram.enums import ParseMode
from aiogram.types import LinkPreviewOptions
from states import UserStates
from keyboards import schedule_pagination_keyboard, help_keyboard
from services.notification_processor import NotificationManager
from services.search_results import SearchResultList
from services.parsers import group_parser, professor_parser

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
                f"{lesson.place.split(' / ')[1]}",
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
                    f"{lesson.place.split(' / ')[1]}",
                    f"<a href='{await create_start_link(bot = message.bot, payload=lesson.professor, encode=True)}'>{lesson.professor}</a>",
                ]
                responses.append("\n".join(lesson_text) + "\n")
    else:
        responses.append("Расписание занятий отсутствует")


    subscribed = data.get('schedule').group_name in await notifyer.get_subscribed(user_id)
    if not update:
        await message.answer(
            "\n".join(responses),
            reply_markup=schedule_pagination_keyboard(current_tab, current_week_index, current_day_index, num_max_days, 'group', subscribed),
            parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    else:
        await message.edit_text(
            "\n".join(responses),
            reply_markup=schedule_pagination_keyboard(current_tab, current_week_index, current_day_index, num_max_days, 'group', subscribed),
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
                f"{lesson.place.split(' / ')[1]}\n"
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
                    f"{lesson.place.split(' / ')[1]}\n"
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
                    f"{lesson.place.split(' / ')[1]}\n"
                    f"{', '.join(group_links)}\n"
                )
    else:
        responses.append("Расписание занятий отсутствует")

    subscribed = data.get('schedule').person_name in await notifyer.get_subscribed(user_id)
    if not update:
        await message.answer(
            "\n".join(responses),
            reply_markup=schedule_pagination_keyboard(current_tab, current_week_index, current_day_index, num_max_days, 'professor', subscribed),
            parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
    else:
        await message.edit_text(
            "\n".join(responses),
            reply_markup=schedule_pagination_keyboard(current_tab, current_week_index, current_day_index, num_max_days, 'professor', subscribed),
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

        await state.update_data(data)

        if answer != "":
            await callback.answer(answer, show_alert=True)
        else:
            await callback.answer()

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
        '• Кнопка открытия сегодняшнего дня (Свитч между стрелками x/x)\n'
        '• Отслеживание изменений (Кнопка Отслеживать 🔔)\n\n'
        '3. Функция отслеживания:\n'
        '• Нажмите на кнопку Отслеживать🔔 чтобы включить уведомления\n'
        '• Получайте уведомления об изменениях в расписании (основное, консультации, сессия)\n'
        '• Отслеживайте несколько групп или преподавателей одновременно\n\n'
        '4. Быстрая навигация:\n'
        '• Нажимайте на названия групп в расписании преподавателя\n'
        '• Нажимайте на имена преподавателей в расписании группы\n\n'
        '• Ссылку можно сохранить, чтобы кликом открывать расписание\n\n'
        'Бот стремится автоматически показать текущий или ближайший следующий день при открытии расписания.\n\n'
    )
    await message.answer(help_text, reply_markup=help_keyboard(), parse_mode=ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True))

@user_router.message(F.text)
async def process_text(message: Message, search_results: SearchResultList, notifyer: NotificationManager, state: FSMContext):
    """Handle text input"""
    await _process_text(message.text, message, search_results, notifyer, state)
