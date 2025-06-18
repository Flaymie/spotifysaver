import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
GENIUS_ACCESS_TOKEN = os.getenv("GENIUS_ACCESS_TOKEN")

# Папка для временных файлов
DOWNLOADS_DIR = "downloads"
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Настройки пагинации
RESULTS_PER_PAGE = 5

# Дневной лимит скачиваний на пользователя
DOWNLOAD_LIMIT_PER_DAY = 5

# Настройки очереди скачивания
MAX_QUEUE_SIZE = 100  # Максимальное количество треков в очереди (0 - безлимитно)
DOWNLOAD_WORKERS = 1   # Количество одновременных скачиваний 