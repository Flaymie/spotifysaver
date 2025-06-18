import asyncio
import logging
import sys
import os
from aiogram import Bot, Dispatcher
from aiogram.client.bot import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeDefault, BotCommandScopeAllGroupChats, BotCommandScopeChat, Message

from config import BOT_TOKEN, DOWNLOAD_WORKERS, MAX_QUEUE_SIZE, DOWNLOAD_LIMIT_PER_DAY
from handlers import router # Убрали download_and_send_audio, increment_user_downloads, они будут вызываться из воркера
from database import init_db, can_user_download, get_user_downloads
from middlewares import ThrottlingMiddleware # <--- Импортируем наш middleware

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

# Очередь для скачивания
download_queue = asyncio.Queue(maxsize=MAX_QUEUE_SIZE if MAX_QUEUE_SIZE > 0 else 0) # 0 для asyncio.Queue означает бесконечный размер

# --- Воркер для обработки очереди скачивания ---
# Импортируем сюда, чтобы избежать циклических зависимостей и дать воркеру доступ
async def download_worker_task(name: str, queue: asyncio.Queue, bot_instance: Bot):
    from handlers import download_and_send_audio, increment_user_downloads
    # can_user_download, get_user_downloads уже импортированы глобально в main.py
    # DOWNLOAD_LIMIT_PER_DAY также доступен глобально в этом модуле

    logger.info(f"Воркер {name} запущен")
    while True:
        task_item = None
        original_message, video_id, user_id = None, None, None # Инициализация для блока finally
        try:
            task_item = await queue.get()
            if task_item is None:
                queue.task_done()
                logger.info(f"Воркер {name} получил сигнал завершения.")
                break
            
            original_message, video_id, user_id = task_item
            logger.info(f"Воркер {name} взял из очереди user_id={user_id}, video_id={video_id}")

            # --- Повторная проверка лимита непосредственно перед скачиванием ---
            if not await can_user_download(user_id, DOWNLOAD_LIMIT_PER_DAY):
                current_downloads = await get_user_downloads(user_id)
                logger.warning(f"Воркер {name}: Лимит для user_id={user_id} уже исчерпан ({current_downloads}/{DOWNLOAD_LIMIT_PER_DAY}) перед началом скачивания video_id={video_id}. Задача отменена.")
                # Пытаемся уведомить пользователя, если это возможно и не слишком спамно
                try:
                    await bot_instance.send_message(original_message.chat.id, f"❗️Не удалось начать скачивание трека (ID {video_id[:7]}...): дневной лимит исчерпан.")
                except Exception as notify_e:
                    logger.error(f"Воркер {name}: не удалось уведомить user_id={user_id} об отмене из-за лимита: {notify_e}")
                queue.task_done()
                continue # Переходим к следующей задаче в очереди
            # --- Конец повторной проверки лимита ---
            
            success = await download_and_send_audio(original_message, video_id, user_id)
            if success:
                await increment_user_downloads(user_id) # Инкремент только после УСПЕШНОГО скачивания и отправки
                logger.info(f"Воркер {name}: user_id={user_id}, video_id={video_id} - успех, счетчик обновлен.")
            else:
                logger.warning(f"Воркер {name}: user_id={user_id}, video_id={video_id} - ошибка обработки download_and_send_audio.")
            
            queue.task_done()
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info(f"Воркер {name} отменен.")
            # Если воркер был отменен во время ожидания queue.get(), задача может остаться в очереди.
            # В идеале, при отмене нужно вернуть задачу в очередь или обработать ее.
            # Но для простоты пока просто выходим.
            if task_item: # Если задача была взята, но не завершена
                queue.put_nowait(task_item) # Попытка вернуть в очередь (может вызвать ошибку если очередь полна)
            break
        except Exception as e:
            logger.error(f"Ошибка в воркере {name} при обработке задачи ({task_item}): {e}", exc_info=True)
            if task_item: 
                 # Важно: Если original_message существует, можно попытаться уведомить об ошибке
                 if original_message and hasattr(original_message, 'chat') and hasattr(original_message.chat, 'id'):
                    try:
                        await bot_instance.send_message(original_message.chat.id, "⚙️ При обработке вашего запроса в очереди произошла ошибка. Попробуйте позже.")
                    except Exception as notify_e:
                        logger.error(f"Воркер {name}: не удалось уведомить user_id об ошибке в задаче: {notify_e}")
                 queue.task_done()
            await asyncio.sleep(5) # Пауза перед следующей попыткой, если ошибка не связана с отменой

async def main():
    if not BOT_TOKEN:
        print("Ошибка: Токен бота не найден. Проверьте .env файл.")
        return
    
    if not os.path.exists("downloads"):
        os.makedirs("downloads")
        logger.info("Создана папка для загрузок")
    
    await init_db()
    
    bot = Bot(token=BOT_TOKEN, default_bot_properties=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # --- Регистрация Middleware ---
    # ThrottlingMiddleware будет применяться ко всем Message хендлерам, зарегистрированным в dp и вложенных роутерах
    dp.message.middleware(ThrottlingMiddleware(rate_limit=0.7, burst_limit=3, period=10.0)) 
    # Можно также добавить для callback_query, если нужно
    # dp.callback_query.middleware(ThrottlingMiddleware(rate_limit=1, burst_limit=2, period=5.0)) # отдельные настройки для колбеков

    dp["download_queue"] = download_queue
    dp["bot_instance"] = bot

    await setup_bot_commands(bot)
    dp.include_router(router)
    
    worker_tasks = []
    try:
        logger.info("Бот запускается...")
        await bot.delete_webhook(drop_pending_updates=True)
        
        for i in range(DOWNLOAD_WORKERS):
            task = asyncio.create_task(download_worker_task(f"DownloadWorker-{i+1}", download_queue, bot))
            worker_tasks.append(task)
        logger.info(f"Запущено {DOWNLOAD_WORKERS} воркеров для скачивания.")
        
        # Запуск поллинга
        await dp.start_polling(bot, allowed_updates=[
            "message", "edited_message", "channel_post", "edited_channel_post",
            "callback_query", "inline_query", "chosen_inline_result"
        ])
    except Exception as e:
        logger.critical(f"Критическая ошибка в main loop: {e}", exc_info=True)
    finally:
        logger.info("Начинаем остановку бота...")
        if worker_tasks:
            logger.info("Отменяем задачи воркеров...")
            for task in worker_tasks:
                task.cancel()
            # Даем воркерам время на завершение
            results = await asyncio.gather(*worker_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, asyncio.CancelledError):
                    logger.info(f"Воркер {i+1} успешно отменен.")
                elif isinstance(result, Exception):
                    logger.error(f"Воркер {i+1} завершился с ошибкой: {result}")
                else:
                    logger.info(f"Воркер {i+1} успешно завершен.")
            logger.info("Все воркеры остановлены.")
        
        # Закрываем сессию бота
        if bot and bot.session:
            logger.info("Закрываем сессию бота...")
            await bot.session.close()
            logger.info("Сессия бота закрыта.")
        
        logger.info("Бот остановлен.")

async def setup_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Запустить бота и получить приветствие"),
        BotCommand(command="search", description="Поиск трека (например: /search ...)")
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
    group_commands = [
        BotCommand(command="search", description="🔎 Поиск и скачивание музыки")
    ]
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())
    logger.info("Команды бота установлены.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Программа прервана пользователем (KeyboardInterrupt)")
    except Exception as e:
        logger.critical(f"Неперехваченная ошибка в __main__: {e}", exc_info=True)
    finally:
        logger.info("Программа полностью завершена.")
