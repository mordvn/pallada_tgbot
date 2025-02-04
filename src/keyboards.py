from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, CopyTextButton

NUM_MAX_WEEKS = 2

def schedule_pagination_keyboard(current_tab: str, current_week_index: int, current_day_index: int,
                  num_max_days: int, schedule_type: str, subscribed: bool, link: str) -> InlineKeyboardMarkup:
    """
    Build keyboard markup based on schedule type and current view state.
    """
    builder = InlineKeyboardBuilder()
    pattern = [3, 4]  # По умолчанию для верхней панели навигации

    # Построение верхней панели навигации
    tabs = {
        'basic': '..',
        'consultations': 'Консультации',
        'session': 'Сессия'
    }

    if schedule_type == 'professor':
        # Для профессора доступны все три вкладки
        nav_buttons = [
            ('Занятия' if current_tab != 'basic' else '..', 'basic_tab' if current_tab != 'basic' else 'nop'),
            ('Консультации' if current_tab != 'consultations' else '..', 'consultations_tab' if current_tab != 'consultations' else 'nop'),
            ('Сессия' if current_tab != 'session' else '..', 'session_tab' if current_tab != 'session' else 'nop')
        ]
    else:
        # Для студента доступны только две вкладки
        nav_buttons = [
            ('Занятия' if current_tab != 'basic' else '..', 'basic_tab' if current_tab != 'basic' else 'nop'),
            ('Сессия' if current_tab != 'session' else '..', 'session_tab' if current_tab != 'session' else 'nop')
        ]
        pattern = [2, 4]  # Корректируем паттерн для студента

    # Добавляем кнопки навигации
    for text, callback in nav_buttons:
        builder.button(text=text, callback_data=callback)

    # Добавляем кнопки навигации по неделям и дням только для вкладки basic
    if current_tab == 'basic':
        builder.button(text=f'{current_week_index}/{NUM_MAX_WEEKS}', callback_data='swap_week')
        builder.button(text='<<', callback_data='prev_day')
        builder.button(text=f'{current_day_index}/{num_max_days}', callback_data='open_today')
        builder.button(text='>>', callback_data='next_day')\

    builder.button(text='🔔' if not subscribed else '🔕', callback_data='notify_me')
    builder.button(text='📅', callback_data='get_calendar')
    builder.button(text='📊', callback_data='ai_summary')
    builder.button(text='🔁', copy_text=CopyTextButton(text=link))

    return builder.adjust(*pattern).as_markup()


def help_keyboard() -> InlineKeyboardMarkup:
    """
    Build keyboard markup for help command.
    """
    builder = InlineKeyboardBuilder()
    pattern = [1, 2]  # Вертикально кнопочки

    builder.button(text='Задонатить ☕️', url='https://pay.cloudtips.ru/p/190e1668')
    builder.button(text='Разработчик', url='https://t.me/jahagafagshsjaavv')
    builder.button(text='Код проекта', url='https://github.com/unknown81d/pallada_tgbot')

    return builder.adjust(*pattern).as_markup()
