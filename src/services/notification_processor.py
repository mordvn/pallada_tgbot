"""
Notification processor module for managing user subscriptions to schedules.
Handles subscription storage and retrieval using a JSON-based database.
"""

import json
import aiofiles
import logging
from typing import List, Dict
from pathlib import Path

logger = logging.getLogger(__name__)

class NotificationManager:
    """
    Manages user subscriptions to schedules.
    """

    def __init__(self, db_path: str = "database/users.json"):
        """
        Initialize NotificationManager with database path.
        """
        self.db_path = Path(db_path)
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Create the JSON file if it doesn't exist"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.db_path.exists():
            self.db_path.write_text("{}")

    async def _read_db(self) -> Dict:
        """
        Read the database file.
        """
        try:
            async with aiofiles.open(self.db_path, 'r') as f:
                content = await f.read()
                return json.loads(content) if content else {}
        except Exception as e:
            logger.error(f"Error reading database: {e}")
            return {}

    async def _write_db(self, data: Dict) -> None:
        """
        Write to the database file.
        """
        try:
            async with aiofiles.open(self.db_path, 'w') as f:
                await f.write(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Error writing to database: {e}")

    async def subscribe(self, user_id: int, schedule_id: str) -> bool:
        """
        Subscribe a user to a schedule.
        """
        try:
            db = await self._read_db()
            user_id_str = str(user_id)

            if user_id_str not in db:
                db[user_id_str] = []

            if schedule_id not in db[user_id_str]:
                db[user_id_str].append(schedule_id)
                await self._write_db(db)
                return True
            return False
        except Exception as e:
            logger.error(f"Error subscribing user {user_id} to schedule {schedule_id}: {e}")
            return False

    async def unsubscribe(self, user_id: int, schedule_id: str) -> bool:
        """
        Unsubscribe a user from a schedule.
        """
        try:
            db = await self._read_db()
            user_id_str = str(user_id)

            if user_id_str in db and schedule_id in db[user_id_str]:
                db[user_id_str].remove(schedule_id)
                await self._write_db(db)
                return True
            return False
        except Exception as e:
            logger.error(f"Error unsubscribing user {user_id} from schedule {schedule_id}: {e}")
            return False

    async def get_subscribed(self, user_id: int) -> List[str]:
        """
        Get all schedules a user is subscribed to.
        """
        try:
            db = await self._read_db()
            return db.get(str(user_id), [])
        except Exception as e:
            logger.error(f"Error getting subscriptions for user {user_id}: {e}")
            return []

    async def get_subscribers(self, schedule_id: str) -> List[int]:
        """
        Get all users subscribed to a specific schedule.
        """
        try:
            db = await self._read_db()
            subscribers = []

            for user_id_str, subscriptions in db.items():
                if schedule_id in subscriptions:
                    subscribers.append(int(user_id_str))

            return subscribers
        except Exception as e:
            logger.error(f"Error getting subscribers for schedule {schedule_id}: {e}")
            return []
