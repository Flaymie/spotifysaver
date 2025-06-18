import aiosqlite
import logging
from datetime import datetime, timedelta

DATABASE_PATH = 'user_data.db'

logger = logging.getLogger(__name__)

async def init_db():
    """Инициализирует базу данных и создает таблицу, если она не существует."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS user_limits (
                    user_id INTEGER PRIMARY KEY,
                    downloads_today INTEGER DEFAULT 0,
                    last_download_date TEXT
                )
            ''')
            await db.commit()
        logger.info("База данных успешно инициализирована.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации БД: {e}")

async def get_user_downloads(user_id: int):
    """Получает количество скачиваний пользователя за сегодня и дату последнего сброса."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            async with db.execute("SELECT downloads_today, last_download_date FROM user_limits WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    downloads_today, last_download_str = row
                    if last_download_str:
                        last_download_date = datetime.strptime(last_download_str, '%Y-%m-%d').date()
                        if last_download_date < datetime.now().date():
                            await db.execute("UPDATE user_limits SET downloads_today = 0, last_download_date = ? WHERE user_id = ?", (datetime.now().strftime('%Y-%m-%d'), user_id))
                            await db.commit()
                            return 0
                    return downloads_today
                else:
                    await db.execute("INSERT INTO user_limits (user_id, downloads_today, last_download_date) VALUES (?, 0, ?)", (user_id, datetime.now().strftime('%Y-%m-%d')))
                    await db.commit()
                    return 0
    except Exception as e:
        logger.error(f"Ошибка при получении данных пользователя {user_id} из БД: {e}")
        return None

async def increment_user_downloads(user_id: int):
    """Увеличивает счетчик скачиваний пользователя."""
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            current_downloads = await get_user_downloads(user_id)
            if current_downloads is None:
                return False

            await db.execute("UPDATE user_limits SET downloads_today = downloads_today + 1, last_download_date = ? WHERE user_id = ?", (datetime.now().strftime('%Y-%m-%d'), user_id))
            await db.commit()
            logger.info(f"Счетчик скачиваний для пользователя {user_id} увеличен.")
            return True
    except Exception as e:
        logger.error(f"Ошибка при увеличении счетчика для пользователя {user_id}: {e}")
        return False

async def can_user_download(user_id: int, limit: int = 5) -> bool:
    """Проверяет, может ли пользователь скачать еще один трек."""
    downloads_today = await get_user_downloads(user_id)
    if downloads_today is None:
        return False
    return downloads_today < limit