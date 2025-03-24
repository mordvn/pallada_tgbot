from aiogram.fsm.state import State, StatesGroup

class UserStates(StatesGroup):
    """States for user interaction flow"""
    in_group_schedule_view = State()
    in_professor_schedule_view = State()
