import unittest
import g4f

class TestSibGUChatbot(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method"""
        self.request_template = """
        Ты - ИИ-помощник СибГУ (Сибирский государственный университет науки и технологий имени академика М. Ф. Решетнёва), расположенного в Красноярске. Твоя задача - помогать пользователям, предоставляя информацию о университете, его программах, подразделениях и других ресурсах. Отвечай кратко, четко и по делу. Не давай личных оценок и не генерируй лишней информации. Придерживайся следующих принципов:

        • Фокус на СибГУ: Все твои ответы должны касаться только СибГУ (SibSAU) и связанных с ним тем.
        • Простота: Используй простые предложения и избегай сложной терминологии.
        • Конкретика: Если ответ требует конкретной информации (например, номер телефона или адрес), постарайся ее предоставить.
        • Помощь в навигации: Если запрос требует более детальной информации или перенаправления, укажи, куда нужно обратиться (сайт, телефон, отдел).
        • Отказ от избыточного: Если запрос не относится к СибГУ или ты не можешь предоставить информацию, скажи об этом.

        Примеры вопросов, на которые ты должен ответить:

        • "Какие есть факультеты в СибГУ?"
        • "Где находится главный корпус СибГУ?"
        • "Как позвонить в приемную комиссию?"
        • "Какие направления подготовки есть на бакалавриате?"
        • "Где можно посмотреть расписание занятий?"
        • "Есть ли в университете общежития?"
        • "Как поступить в СибГУ?"
        • "Какие мероприятия проходят в университете?"
        • "Кто ректор СибГУ?"
        • "Где можно найти информацию о научной деятельности?"

        Примеры ответов:

        • "Факультеты СибГУ можно найти на официальном сайте университета."
        • "Главный корпус СибГУ расположен по адресу [точный адрес, если известно]. "
        • "Телефон приемной комиссии [номер телефона, если известен]."
        • "Расписание занятий доступно на портале университета."
        • "По вопросам поступления обратитесь в приемную комиссию."

        Если вопрос не относится к СибГУ или ты не можешь предоставить информацию, отвечай так:

        • "Извини, я не могу ответить на этот вопрос. Это не относится к СибГУ."
        • "К сожалению, у меня нет информации по этому вопросу."

        Важные моменты для более слабой нейросети:

        • Ограничь объем ответа: Отвечай кратко, не более 2-3 предложений.
        • Избегай сложных связей: Не нужно делать сложные анализы или сопоставления, отвечай прямо на вопрос.
        • Используй ключевые слова: Ищи ключевые слова в запросе (например, "факультеты", "приемная комиссия", "расписание") и отвечай на них.
        • Дай понять, что не можешь помочь: Если не знаешь ответа, честно скажи об этом, а не пытайся генерировать ответ.

        Внимание: Этот промт ориентирован на помощь в поиске информации о СибГУ. Он не предназначен для выполнения сложных задач или креативных заданий. Его цель - предоставить простую и понятную справочную информацию.

        Запрос от пользователя: {query}
        """

    def test_directory_number_query(self):
        """Test chatbot response for directory number query"""
        query = "дай номер дирекции"
        response = self._get_chatbot_response(query)
        self.assertIsNotNone(response)
        self.assertIsInstance(response, str)

    def test_invalid_query(self):
        """Test chatbot response for invalid query"""
        query = "какая погода в Москве"
        response = self._get_chatbot_response(query)
        self.assertIsNotNone(response)
        # Check for either of the expected response patterns
        self.assertTrue(
            any(phrase in response.lower() for phrase in
                ["не относится к сибгу", "не могу ответить"])
        )

    def _get_chatbot_response(self, query):
        """Helper method to get chatbot response"""
        response = g4f.ChatCompletion.create(
            model=g4f.models.gpt_4,
            provider=g4f.Provider.Yqcloud,
            messages=[{"role": "user", "content": self.request_template.format(query=query)}],
            stream=False  # Changed to False for testing purposes
        )
        return response

from gcsa.event import Event
from gcsa.google_calendar import GoogleCalendar
from gcsa.recurrence import Recurrence, DAILY, SU, SA, WEEKLY
from gcsa.calendar import Calendar
from beautiful_date import Apr, hours, Feb
from gcsa.event import Event
from gcsa.event import Event
from gcsa.serializers.event_serializer import EventSerializer
from gcsa.serializers.calendar_serializer import CalendarSerializer
from gcsa.serializers.acl_rule_serializer import ACLRuleSerializer
from gcsa.acl import AccessControlRule, ACLRole, ACLScopeType
from datetime import datetime
from beautiful_date import Jan, Apr

class TestGoogleCalendarIntegration(unittest.TestCase):
    def setUp(self):
        """Set up test fixtures before each test method"""
        self.gc = GoogleCalendar(
            credentials_path='.credentials/credentials.json',
            authentication_flow_port=8000
        )
        self.group_name = 'БПИ23-01'

        # Configure calendar settings
        settings = self.gc.get_settings()
        settings.format24_hour_time = True
        settings.locale = 'ru'
        settings.timezone = 'Asia/Krasnoyarsk'

    def test_calendar_creation(self):
        """Test creating a new calendar for a group"""
        # Find or create calendar
        group_calendar = None
        for calendar in self.gc.get_calendar_list():
            if calendar.summary == self.group_name:
                group_calendar = calendar
                break

        if group_calendar is None:
            calendar = Calendar(
                self.group_name,
                description=f'Расписание {self.group_name}'
            )
            group_calendar = self.gc.add_calendar(calendar)

        self.assertIsNotNone(group_calendar)
        self.assertEqual(group_calendar.summary, self.group_name)

    def test_event_management(self):
        """Test adding and deleting events"""
        # Get the test calendar
        group_calendar = None
        for calendar in self.gc.get_calendar_list():
            if calendar.summary == self.group_name:
                group_calendar = calendar
                break

        self.assertIsNotNone(group_calendar)

        # Clear existing events
        for event in self.gc.get_events(calendar_id=group_calendar.id):
            self.gc.delete_event(event, calendar_id=group_calendar.id)

        # Create test event
        r = Recurrence.rule(freq=WEEKLY, interval=2)
        start = datetime(year=2025, month=2, day=5, hour=6, minute=30)
        end = start + 2 * hours

        test_event = Event(
            'Test Meeting',
            start=start,
            end=end,
            description='Test event',
            location='Test location',
            recurrence=r
        )

        # Add event
        added_event = self.gc.add_event(test_event, calendar_id=group_calendar.id)
        self.assertIsNotNone(added_event)
        self.assertEqual(added_event.summary, 'Test Meeting')

    def test_calendar_permissions(self):
        """Test setting calendar permissions"""
        # Get the test calendar
        group_calendar = None
        for calendar in self.gc.get_calendar_list():
            if calendar.summary == self.group_name:
                group_calendar = calendar
                break

        self.assertIsNotNone(group_calendar)

        # Set public read access
        rule = AccessControlRule(
            role=ACLRole.READER,
            scope_type=ACLScopeType.DEFAULT
        )
        added_rule = self.gc.add_acl_rule(rule, calendar_id=group_calendar.id)

        self.assertIsNotNone(added_rule)
        self.assertEqual(added_rule.role, ACLRole.READER)

    def test_calendar_url(self):
        """Test getting calendar URL"""
        # Get the test calendar
        group_calendar = None
        for calendar in self.gc.get_calendar_list():
            if calendar.summary == self.group_name:
                group_calendar = calendar
                break

        self.assertIsNotNone(group_calendar)
        calendar_url = f"https://calendar.google.com/calendar/u/0/r?cid={group_calendar.id}"
        self.assertTrue(calendar_url.startswith('https://calendar.google.com'))

#if __name__ == '__main__':
    #unittest.main()
