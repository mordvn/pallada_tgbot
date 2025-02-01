from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup

MAX_WEEKS = 2

def build_keyboard(current_tab: str, current_week: int, current_day: int,
                  max_days: int, schedule_type: str, subscribed: bool) -> InlineKeyboardMarkup:
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
        builder.button(text=f'{current_week}/{MAX_WEEKS}', callback_data='swap_week')
        builder.button(text='<<', callback_data='prev_day')
        builder.button(text=f'{current_day}/{max_days}', callback_data='nop')
        builder.button(text='>>', callback_data='next_day')\

    builder.button(text='Отслеживать 🔔' if not subscribed else 'Отменить 🔕', callback_data='notify_me')

    return builder.adjust(*pattern).as_markup()
