import os
import asyncio
import time
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from aiogram import Router, F, Bot
from aiogram.types import (
    Message, CallbackQuery, FSInputFile, 
    InlineQuery, InlineQueryResultArticle, InputTextMessageContent,
    InlineQueryResultAudio, InputMessageContent, InputFile
)
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardButton

from keyboards import get_search_results_keyboard, get_video_id_by_key, get_track_keyboard
from utils import search_youtube, download_audio, is_youtube_url, is_spotify_url, get_spotify_track_info, is_valid_youtube_id, get_lyrics_for_track
from config import RESULTS_PER_PAGE, DOWNLOAD_LIMIT_PER_DAY, MAX_QUEUE_SIZE
from database import can_user_download, increment_user_downloads, get_user_downloads

# Настройка логирования
logger = logging.getLogger(__name__)

router = Router()

class SearchStates(StatesGroup):
    searching = State()

# Словарь для хранения результатов поиска для каждого пользователя
user_search_results = {}

# Словарь для кэширования результатов поиска
search_cache = {}
# Время жизни кэша в секундах (30 минут)
CACHE_TTL = 1800

# Добавляем ThreadPoolExecutor для выполнения тяжелых задач
thread_pool = ThreadPoolExecutor(max_workers=4)

async def clear_user_cache(user_id, delay=CACHE_TTL):
    """Очистка кэша пользователя через указанное время"""
    await asyncio.sleep(delay)
    if user_id in user_search_results:
        del user_search_results[user_id]
    
    # Очищаем кэш поиска для пользователя
    keys_to_delete = []
    for key in search_cache.keys():
        if key.startswith(f"{user_id}_"):
            keys_to_delete.append(key)
    
    for key in keys_to_delete:
        if key in search_cache:
            del search_cache[key]

@router.message(Command("start"))
async def cmd_start(message: Message, download_queue: asyncio.Queue, bot_instance: Bot):
    if message.chat.type != "private": return
    command_args = message.text.split()
    if len(command_args) > 1 and command_args[1].startswith("download_"):
        video_id = command_args[1].replace("download_", "")
        user_id = message.from_user.id
        if is_valid_youtube_id(video_id):
            if not await can_user_download(user_id, DOWNLOAD_LIMIT_PER_DAY):
                limit_msg = f"⚠️ <b>Лимит исчерпан</b>\nВы достигли дневного лимита скачиваний ({await get_user_downloads(user_id)}/{DOWNLOAD_LIMIT_PER_DAY})."
                await message.answer(limit_msg, parse_mode="HTML")
                return
            try:
                # Передаем message, а не callback.message, так как это прямой вызов
                await download_queue.put((message, video_id, user_id))
                await message.answer(f"▶️ <b>Трек добавлен в очередь</b>\nПозиция: {download_queue.qsize()}\nОжидайте загрузку...", parse_mode="HTML")
            except asyncio.QueueFull:
                await message.answer(f"😕 <b>Очередь переполнена</b>\nВ данный момент в очереди максимальное количество треков ({MAX_QUEUE_SIZE}).\nПопробуйте позже.", parse_mode="HTML")
            return
    
    bot_info = await bot_instance.get_me()
    bot_username = bot_info.username
    await message.answer(
        "<b>🎵 SpotifySaver Bot</b>\n\n"
        "Привет! Я помогу тебе скачать музыку из YouTube и Spotify.\n\n"
        "<b>Как использовать:</b>\n"
        "• Отправь мне <b>ссылку</b> YouTube/Spotify\n"
        "• Или просто напиши <b>название трека</b>\n"
        "• Используй инлайн-режим: <code>@" + bot_username + " название трека</code>\n\n"
        "<b>Я найду и отправлю тебе трек в формате MP3!</b>",
        parse_mode="HTML"
    )
    
    # Добавляем информацию о работе в группах
    bot_link = f"https://t.me/{bot_username}?startgroup=start"
    builder = InlineKeyboardBuilder()
    builder.add(InlineKeyboardButton(
        text="➕ Добавить бота в группу",
        url=bot_link
    ))
    
    await message.answer(
        "<b>💬 Работа в группах</b>\n\n"
        "Бот также работает в групповых чатах!\n"
        "Используйте команду <code>/search</code> для поиска музыки в группах.",
        reply_markup=builder.as_markup(),
        parse_mode="HTML"
    )

# Добавляем отдельный обработчик для команды /start в группах
@router.message(Command("start"), F.chat.type != "private")
async def cmd_start_group(message: Message, bot_instance: Bot):
    logger.info(f"Команда /start в группе {message.chat.id}")
    
    bot_info = await bot_instance.get_me()
    bot_username = bot_info.username
    
    # Отправляем информацию о боте в группу
    await message.reply(
        f"<b>🎵 SpotifySaver Bot</b>\n\n"
        f"Привет! Я помогу скачать музыку прямо в этом чате.\n\n"
        f"<b>Как использовать:</b>\n"
        f"• Используйте команду <code>/search название_трека</code>\n"
        f"• Например: <code>/search Imagine Dragons - Believer</code>\n\n"
        f"• Или используйте инлайн-режим: <code>@{bot_username} название_трека</code>",
        parse_mode="HTML"
    )

# Этот обработчик теперь ТОЛЬКО для личных сообщений
@router.message(F.text & ~F.text.startswith('/'), F.chat.type == "private")
async def handle_text_or_link(message: Message, state: FSMContext, download_queue: asyncio.Queue):
    user_id = message.from_user.id
    query = message.text.strip()
    
    logger.info(f"Пользователь {user_id} (чат {message.chat.id}) отправил: {query}")
    
    reply_func = message.reply if message.chat.type != "private" else message.answer
    
    is_direct_download_link = False
    video_id_to_download = None

    if is_youtube_url(query) or is_spotify_url(query):
        if not await can_user_download(user_id, DOWNLOAD_LIMIT_PER_DAY):
            limit_msg = f"<b>⚠️ Лимит исчерпан</b>\n\nВы достигли дневного лимита скачиваний ({await get_user_downloads(user_id)}/{DOWNLOAD_LIMIT_PER_DAY})."
            await reply_func(limit_msg, parse_mode="HTML")
            return
        
        progress_msg = await reply_func("<b>⏳ Обрабатываю ссылку...</b>", parse_mode="HTML")
        try:
            if is_youtube_url(query):
                results = await asyncio.to_thread(search_youtube, query, 1)
                if results and is_valid_youtube_id(results[0]['id']):
                    video_id_to_download = results[0]['id']
                    is_direct_download_link = True
                else:
                    await progress_msg.edit_text("<b>❌ Ошибка</b>\n\nНе удалось найти YouTube видео по этой ссылке.", parse_mode="HTML")
                    return
            elif is_spotify_url(query):
                spotify_track_name = await asyncio.to_thread(get_spotify_track_info, query)
                if spotify_track_name:
                    await progress_msg.edit_text(
                        f"<b>🎵 Трек из Spotify</b>\n\n"
                        f"<b>Название:</b> {spotify_track_name}\n"
                        f"<b>Статус:</b> <i>Ищу на YouTube...</i>",
                        parse_mode="HTML"
                    )
                    query = spotify_track_name
                else:
                    await progress_msg.edit_text("<b>❌ Ошибка</b>\n\nНе удалось получить информацию о треке из Spotify.", parse_mode="HTML")
                    return
            
            if is_direct_download_link and video_id_to_download:
                 await progress_msg.delete()
                 try:
                    await download_queue.put((message, video_id_to_download, user_id))
                    await reply_func(
                        f"<b>▶️ Трек добавлен в очередь</b>\n\n"
                        f"<b>Позиция:</b> {download_queue.qsize()}\n"
                        f"<i>Ожидайте загрузку...</i>",
                        parse_mode="HTML"
                    )
                 except asyncio.QueueFull:
                    await reply_func(
                        f"<b>😕 Очередь переполнена</b>\n\n"
                        f"В данный момент в очереди максимальное количество треков ({MAX_QUEUE_SIZE}).\n"
                        f"Попробуйте повторить запрос позже.",
                        parse_mode="HTML"
                    )
                 return

        except Exception as e:
            logger.error(f"Ошибка при прямой обработке ссылки {query}: {e}")
            await progress_msg.edit_text("<b>❌ Ошибка</b>\n\nПроизошла ошибка при обработке ссылки.", parse_mode="HTML")
            return
        
        if not is_direct_download_link:
            if 'progress_msg' not in locals() or not progress_msg:
                 progress_msg = await reply_func("<b>🔍 Поиск трека...</b>", parse_mode="HTML")
            else: 
                 await progress_msg.edit_text(f"<b>🔍 Поиск трека</b>\n\n<b>Запрос:</b> \"{query}\"", parse_mode="HTML")
    else:
        progress_msg = await reply_func("<b>🔍 Поиск трека...</b>", parse_mode="HTML")

    is_artist_track = bool(re.search(r'^(.+?)\s*[-–]\s*(.+)$', query))
    cache_key = f"{user_id}_{query}"
    if cache_key in search_cache and (time.time() - search_cache[cache_key]['timestamp'] < CACHE_TTL):
        results = search_cache[cache_key]['results']
        logger.info(f"Результаты для '{query}' взяты из кэша.")
    else:
        logger.info(f"Выполняю поиск на YouTube: {query}")
        results_limit = 5 if is_artist_track else 20
        results = await asyncio.to_thread(search_youtube, query, results_limit)
        if results: search_cache[cache_key] = {'results': results, 'timestamp': time.time()}
    
    if not results:
        await progress_msg.edit_text(
            "<b>❌ Ничего не найдено</b>\n\n"
            "По вашему запросу не найдено результатов.\n"
            "Попробуйте изменить запрос или проверить правильность написания.",
            parse_mode="HTML"
        )
        return

    await progress_msg.delete()
    user_search_results[user_id] = results
    result_text = f"<b>🔍 Результаты поиска</b>\n\n<b>Запрос:</b> \"{query}\""
    if is_artist_track: result_text += "\n💡 <i>Показаны наиболее точные совпадения</i>"
    await reply_func(
        f"{result_text}\n\n<b>👇 Выберите трек из списка:</b>",
        reply_markup=get_search_results_keyboard(results, page=0, user_id=user_id),
        parse_mode="HTML"
    )
    await state.set_state(SearchStates.searching)
    asyncio.create_task(clear_user_cache(user_id))

@router.callback_query(F.data.startswith("page_"))
async def handle_pagination(callback: CallbackQuery):
    user_id = callback.from_user.id
    page = int(callback.data.split("_")[1])
    
    if user_id not in user_search_results:
        await callback.answer("❌ Результаты поиска устарели. Начни новый поиск.", show_alert=True)
        return
    
    results = user_search_results[user_id]
    
    await callback.message.edit_reply_markup(
        reply_markup=get_search_results_keyboard(results, page=page, user_id=user_id)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("download_"))
async def handle_download_callback(callback: CallbackQuery, state: FSMContext, download_queue: asyncio.Queue):
    user_id = callback.from_user.id
    index_key = callback.data.replace("download_", "", 1)
    
    video_id = get_video_id_by_key(index_key)
    
    if not video_id or not is_valid_youtube_id(video_id):
        await callback.answer("❌ Ошибка: ID видео не найден или некорректен.", show_alert=True)
        await callback.message.edit_text(
            "<b>❌ Ошибка</b>\n\n"
            "Не удалось получить ID видео.\n"
            "Пожалуйста, выполните новый поиск.",
            parse_mode="HTML"
        )
        return
    
    if not await can_user_download(user_id, DOWNLOAD_LIMIT_PER_DAY):
        limit_msg = f"⚠️ Дневной лимит ({await get_user_downloads(user_id)}/{DOWNLOAD_LIMIT_PER_DAY}) исчерпан."
        await callback.answer(limit_msg, show_alert=True)
        return
    
    try:
        await download_queue.put((callback.message, video_id, user_id))
        await callback.answer(f"▶️ Трек добавлен в очередь (поз. {download_queue.qsize()}). Ожидайте.", show_alert=False)
        await callback.message.edit_text(
            "<b>🎶 Трек добавлен в очередь</b>\n\n"
            f"<b>Позиция:</b> {download_queue.qsize()}\n"
            "<i>Ожидайте загрузку...</i>",
            parse_mode="HTML"
        )
    except asyncio.QueueFull:
        await callback.answer(f"😕 Очередь скачивания переполнена ({MAX_QUEUE_SIZE} треков). Попробуйте позже.", show_alert=True)
    except Exception as e:
        logger.error(f"Ошибка при добавлении в очередь скачивания: {e}")
        await callback.answer("❌ Произошла ошибка при добавлении в очередь.", show_alert=True)
    
    await state.clear()

@router.callback_query(F.data == "back_to_results")
async def handle_back_to_results(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    if user_id not in user_search_results:
        await callback.answer("❌ Результаты поиска устарели.", show_alert=True)
        return
    
    results = user_search_results[user_id]
    
    text = "<b>🔍 Результаты поиска</b>\n\n<b>👇 Выберите трек из списка:</b>"
    reply_markup = get_search_results_keyboard(results, page=0, user_id=user_id)

    is_group = callback.message.chat.type != "private"
    if is_group:
        await callback.message.delete()
        await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        # Проверяем, есть ли текст в сообщении
        if callback.message.text or callback.message.caption:
            await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            # Если текста нет (например, это аудио-сообщение), отправляем новое сообщение
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
    
    await state.set_state(SearchStates.searching)
    await callback.answer()

@router.callback_query(F.data.startswith("lyrics_"))
async def handle_lyrics_request(callback: CallbackQuery):
    # Проверка на время жизни сообщения
    current_time = time.time()
    message_time = callback.message.date.timestamp() # Используем timestamp() для datetime объекта
    if current_time - message_time > 180: # 3 минуты
        await callback.answer("⏳ Запрос на текст песни устарел. Пожалуйста, выполните новый поиск.", show_alert=True)
        try:
            if callback.message.from_user.is_bot:
                 await callback.message.edit_text(f"{callback.message.text}\n\n<i>⌛ Запрос на текст этой песни истек</i>", reply_markup=None, parse_mode="HTML")
            else:
                 pass
        except Exception as e:
            logger.warning(f"Не удалось отредактировать старое сообщение при истечении срока запроса текста: {e}")
        return

    try:
        parts = callback.data.split("_", 2)
        if len(parts) < 3:
            await callback.answer("❌ Недостаточно информации о треке", show_alert=True)
            return
        
        short_track_name_from_callback = parts[1]
        short_artist_name_from_callback = parts[2]
        
        full_track_name = None
        full_artist_name = None

        user_id = callback.from_user.id
        if user_id in user_search_results:
            for result in user_search_results[user_id]:
                title_from_search = result.get('title', '')
                uploader_from_search = result.get('uploader', '')

                # Сначала пытаемся распарсить "Исполнитель - Трек" из title_from_search
                artist_title_match_search = re.match(r'^(.+?)\s*[-–—]\s*(.+)$', title_from_search)
                if artist_title_match_search:
                    potential_artist = artist_title_match_search.group(1).strip()
                    potential_title = artist_title_match_search.group(2).strip()
                    # Сверяем с тем, что пришло из callback, чтобы найти нужный трек
                    if potential_title.lower().startswith(short_track_name_from_callback.lower()):
                        full_track_name = potential_title
                        full_artist_name = potential_artist
                        logger.info(f"Трек найден в user_search_results (распарсен): '{full_track_name}' - '{full_artist_name}'")
                        break
                
                # Если не распарсилось или не подошло, пробуем использовать uploader как исполнителя,
                # но только если title_from_search совпадает с callback
                if not full_track_name and title_from_search.lower().startswith(short_track_name_from_callback.lower()):
                    full_track_name = title_from_search
                    full_artist_name = uploader_from_search # Может быть названием канала
                    logger.info(f"Трек найден в user_search_results (title/uploader): '{full_track_name}' - '{full_artist_name}'")
                    break
        
        if not full_track_name and callback.message.audio and callback.message.audio.title:
            full_track_name = callback.message.audio.title
            logger.info(f"Название трека взято из audio.title: '{full_track_name}'")
        elif not full_track_name and callback.message.caption:
            caption_text = callback.message.caption
            if caption_text.startswith("🎧 "):
                full_track_name = caption_text[2:].strip()
                logger.info(f"Название трека взято из caption: '{full_track_name}'")
        
        if not full_artist_name and callback.message.audio and callback.message.audio.performer:
            # audio.performer часто содержит "SpotifySaverBot", его нужно проверять
            performer_candidate = callback.message.audio.performer
            if performer_candidate and performer_candidate.lower() != "spotifysaverbot":
                 full_artist_name = performer_candidate
                 logger.info(f"Исполнитель взят из audio.performer: '{full_artist_name}'")
            else:
                logger.info(f"audio.performer ('{performer_candidate}') не используется как исполнитель.")

        if not full_track_name:
            full_track_name = short_track_name_from_callback
            logger.info(f"Название трека (short) используется: '{full_track_name}'")
        if not full_artist_name or full_artist_name.lower() == "spotifysaverbot":
            # Если исполнитель из callback это 'spotifysaverbot' или пустой, не используем его
            if short_artist_name_from_callback and short_artist_name_from_callback.lower() != "spotifysaverbot":
                full_artist_name = short_artist_name_from_callback
                logger.info(f"Исполнитель (short) используется: '{full_artist_name}'")
            else: # Если и в callback_data плохой исполнитель, оставляем None
                full_artist_name = None 
                logger.info(f"Исполнитель (short) из callback ('{short_artist_name_from_callback}') не используется.")


        # Финальная попытка извлечь исполнителя из названия трека, если он все еще не определен или некорректен
        if not full_artist_name or full_artist_name.lower() == "spotifysaverbot":
            logger.info(f"Исполнитель '{full_artist_name}' некорректен или отсутствует, пытаемся извлечь из трека '{full_track_name}'")
            artist_from_title_match = re.match(r'^(.+?)\s*[-–—]\s*(.+)$', full_track_name)
            if artist_from_title_match:
                potential_artist = artist_from_title_match.group(1).strip()
                potential_title = artist_from_title_match.group(2).strip()
                if len(potential_artist) > 1 and len(potential_artist.split()) < 5: # Более мягкое правило
                    full_artist_name = potential_artist
                    full_track_name = potential_title 
                    logger.info(f"Исполнитель извлечен из названия: '{full_artist_name}', трек: '{full_track_name}'")
            else:
                 logger.info(f"Не удалось извлечь исполнителя из '{full_track_name}'")


        if not full_artist_name: # Крайний случай, если исполнителя так и не нашли
            logger.warning(f"Не удалось определить исполнителя для трека '{full_track_name}'. Запрос на текст может быть неточным.")
            # Можно установить исполнителя в "Unknown" или оставить None, 
            # чтобы get_lyrics_for_track попробовал найти без него (если Genius так умеет)
            # Для большей предсказуемости, лучше передать хоть что-то, даже если это callback data
            full_artist_name = short_artist_name_from_callback if short_artist_name_from_callback.lower() != "spotifysaverbot" else "Unknown"

        await callback.answer("🔍 Ищем текст песни...", show_alert=False)
        
        loading_msg = await callback.message.answer(
            "<b>⏳ Поиск текста песни</b>\n\n"
            "Ищем текст на Genius...\n"
            "Это может занять несколько секунд.",
            parse_mode="HTML"
        )
        
        lyrics_data = await asyncio.to_thread(get_lyrics_for_track, full_artist_name, full_track_name)
        
        await loading_msg.delete()
        
        if lyrics_data["success"]:
            # Форматируем текст песни для отображения
            lyrics_text = lyrics_data["lyrics"]
            
            artist_display_name = lyrics_data['artist_name']
            featured_artists = lyrics_data.get('featured_artists', [])
            if featured_artists:
                artist_display_name += f" (feat. {', '.join(featured_artists)})"

            # Создаем красивый заголовок
            header = (
                f"<b>📝 Текст песни</b>\n\n"
                f"<b>🎵 Название:</b> {lyrics_data['track_name']}\n"
                f"<b>👤 Исполнитель:</b> {artist_display_name}"
            )
            
            # Если есть ссылка на источник, добавляем ее
            if lyrics_data.get('source_url'):
                header += f"\n<b>🔗 Источник:</b> <a href='{lyrics_data['source_url']}'>Genius</a>"
            
            # Добавляем разделитель
            header += "\n\n<b>━━━━━━━━━━━━━━━━━━━━━━</b>\n\n"
            
            # Ограничиваем длину текста для сообщения Telegram (максимум 4096 символов)
            max_length = 4000 - len(header)
            if len(lyrics_text) > max_length:
                lyrics_text = lyrics_text[:max_length] + "...\n<i>(текст обрезан из-за ограничений Telegram)</i>"
            
            # Отправляем текст песни
            message_text = f"{header}{lyrics_text}"
            
            # Создаем клавиатуру с кнопкой возврата
            builder = InlineKeyboardBuilder()
            builder.row(InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_results"))
            
            await callback.message.answer(message_text, reply_markup=builder.as_markup(), parse_mode="HTML")
        else:
            # Если текст не найден, отправляем сообщение об ошибке
            error_msg = lyrics_data.get("error", "Текст песни не найден")
            await callback.message.answer(
                f"<b>❌ Текст песни не найден</b>\n\n{error_msg}",
                reply_markup=InlineKeyboardBuilder().row(
                    InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_results")
                ).as_markup(),
                parse_mode="HTML"
            )
    
    except Exception as e:
        logger.error(f"Ошибка при обработке запроса текста песни: {e}", exc_info=True)
        await callback.message.answer(
            "<b>❌ Ошибка при получении текста</b>\n\n"
            "Не удалось получить текст песни.\n"
            "Попробуйте еще раз или выберите другой трек.",
            reply_markup=InlineKeyboardBuilder().row(
                InlineKeyboardButton(text="↩️ Назад", callback_data="back_to_results")
            ).as_markup(),
            parse_mode="HTML"
        )

async def download_and_send_audio(original_message: Message, video_id: str, user_id: int):
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    is_group = original_message.chat.type != "private"
    reply_func = original_message.reply if is_group else original_message.answer
    bot_instance = original_message.bot

    progress_msg = await reply_func(
        "<b>📥 Скачивание трека</b>\n\n"
        "⏳ Пожалуйста, подождите...\n"
        "<i>Это может занять некоторое время в зависимости от размера файла</i>",
        parse_mode="HTML"
    )
    
    try:
        file_path, title = await asyncio.to_thread(download_audio, video_url)
        if not file_path or not os.path.exists(file_path) or os.path.getsize(file_path) < 1024:
            logger.error(f"Ошибка файла: path={file_path}, exists={os.path.exists(file_path) if file_path else False}, size={os.path.getsize(file_path) if file_path and os.path.exists(file_path) else 0}")
            await progress_msg.edit_text(
                "<b>❌ Ошибка загрузки</b>\n\n"
                "Не удалось скачать трек или файл поврежден.\n"
                "Попробуйте выбрать другой трек.",
                parse_mode="HTML"
            )
            return False
        
        await progress_msg.edit_text(
            "<b>⏳ Почти готово</b>\n\n"
            "Файл скачан, отправляю в чат...",
            parse_mode="HTML"
        )
        
        # Извлекаем исполнителя из названия, если возможно (для более точного track_info)
        parsed_artist = "SpotifySaverBot" # Исполнитель по умолчанию
        parsed_title = title # Название по умолчанию
        artist_title_match = re.match(r'^(.+?)\s*[-–—]\s*(.+)$', title)
        if artist_title_match:
            potential_artist = artist_title_match.group(1).strip()
            potential_title = artist_title_match.group(2).strip()
            # Простое эвристическое правило, чтобы не принять часть названия за исполнителя
            if len(potential_artist) > 2 and len(potential_artist.split()) < 4: 
                parsed_artist = potential_artist
                parsed_title = potential_title
                logger.info(f"Распарсен исполнитель: '{parsed_artist}', трек: '{parsed_title}' из полного названия: '{title}'")
            else:
                logger.info(f"Не удалось надежно распарсить исполнителя из: '{title}'")
        else:
            logger.info(f"Формат 'Исполнитель - Трек' не найден в: '{title}'")

        # Получаем информацию о треке для клавиатуры
        track_info = {
            'title': parsed_title, # Используем распарсенное название
            'uploader': parsed_artist, # Используем распарсенного исполнителя
            'id': video_id
        }
        
        # Создаем клавиатуру с кнопками "Показать текст песни" и "К результатам"
        back_button_markup = None
        # Проверяем, есть ли результаты поиска, чтобы решить, нужна ли кнопка "К результатам"
        # Это важно, так как original_message может быть результатом прямого скачивания по ссылке,
        # а не выбором из результатов поиска.
        if user_id in user_search_results and user_search_results[user_id]:
            back_button_markup = get_track_keyboard(track_info, has_back_button=True)
        else:
            # Если нет результатов поиска (например, прямая ссылка), то кнопка "К результатам" не нужна
            back_button_markup = get_track_keyboard(track_info, has_back_button=False)

        audio_file = FSInputFile(path=file_path, filename=f"{title[:60]}.mp3")
        # Для caption используем оригинальное полное название, которое скачал yt-dlp
        caption = f"🎧 {title[:900]}"
        # Для метаданных аудиофайла используем распарсенные title и artist
        audio_title_meta = parsed_title[:64]
        audio_performer_meta = parsed_artist[:64]
        
        target_chat_id = original_message.chat.id
        try:
            if is_group:
                await bot_instance.send_audio(
                    chat_id=target_chat_id,
                    audio=audio_file, 
                    title=audio_title_meta, 
                    performer=audio_performer_meta,
                    caption=caption, 
                    reply_markup=back_button_markup,
                    reply_to_message_id=original_message.message_id
                )
            else:
                await bot_instance.send_audio(
                    chat_id=target_chat_id,
                    audio=audio_file, 
                    title=audio_title_meta, 
                    performer=audio_performer_meta,
                    caption=caption, 
                    reply_markup=back_button_markup
                )
            logger.info(f"Аудио '{title}' отправлено в чат {target_chat_id} с мета: title='{audio_title_meta}', performer='{audio_performer_meta}'")
            await progress_msg.delete()
            return True
        except Exception as send_err:
            logger.error(f"Ошибка при отправке аудио в чат {target_chat_id}: {send_err}", exc_info=True)
            await progress_msg.edit_text(f"❌ Ошибка при отправке аудио. Возможно, файл слишком большой или проблема с Telegram.")
            return False
    except Exception as e:
        logger.error(f"Общая ошибка при скачивании/обработке {video_url}: {e}", exc_info=True)
        await progress_msg.edit_text("❌ Ошибка при скачивании. Попробуйте другой трек.")
        return False
    finally:
        if 'file_path' in locals() and file_path and os.path.exists(file_path):
            try: os.remove(file_path); logger.info(f"Временный файл удален: {file_path}")
            except Exception as e: logger.error(f"Ошибка при удалении временного файла {file_path}: {e}")

@router.message(Command("search"))
async def cmd_search(message: Message, state: FSMContext, download_queue: asyncio.Queue):
    user_id = message.from_user.id
    is_group = message.chat.type != "private"
    reply_func = message.reply if is_group else message.answer
    command_parts = message.text.split(maxsplit=1)
    if "@" in command_parts[0]: command_parts[0] = command_parts[0].split("@")[0]

    if len(command_parts) < 2 or not command_parts[1].strip():
        return await reply_func(
            "<b>ℹ️ Как использовать команду поиска</b>\n\n"
            "Используйте: <code>/search название трека или исполнитель</code>\n\n"
            "<b>Примеры:</b>\n"
            "• <code>/search Imagine Dragons - Believer</code>\n"
            "• <code>/search https://youtu.be/abcdef</code>\n"
            "• <code>/search https://open.spotify.com/track/...</code>",
            parse_mode="HTML"
        )

    query = command_parts[1].strip()
    logger.info(f"Команда /search от {user_id} (чат {message.chat.id}): {query}")

    is_direct_download_link = False
    video_id_to_download = None

    if is_youtube_url(query) or is_spotify_url(query):
        if not await can_user_download(user_id, DOWNLOAD_LIMIT_PER_DAY):
            limit_msg = f"⚠️ Достигнут дневной лимит скачиваний ({await get_user_downloads(user_id)}/{DOWNLOAD_LIMIT_PER_DAY})."
            await reply_func(limit_msg)
            return
        
        progress_msg = await reply_func("⏳ Обрабатываю ссылку...")
        try:
            if is_youtube_url(query):
                results = await asyncio.to_thread(search_youtube, query, 1)
                if results and is_valid_youtube_id(results[0]['id']):
                    video_id_to_download = results[0]['id']
                    is_direct_download_link = True
                else:
                    await progress_msg.edit_text("❌ Не удалось найти YouTube видео по этой ссылке.")
                    return
            elif is_spotify_url(query):
                spotify_track_name = await asyncio.to_thread(get_spotify_track_info, query)
                if spotify_track_name:
                    await progress_msg.edit_text(f"🎵 Из Spotify: {spotify_track_name}. Ищу на YouTube...")
                    query = spotify_track_name
                else:
                    await progress_msg.edit_text("❌ Не удалось получить информацию из Spotify.")
                    return
            
            if is_direct_download_link and video_id_to_download:
                 await progress_msg.delete()
                 try:
                    await download_queue.put((message, video_id_to_download, user_id))
                    await reply_func(f"▶️ Ваш трек добавлен в очередь (поз. {download_queue.qsize()}). Ожидайте.")
                 except asyncio.QueueFull:
                    await reply_func(f"😕 Очередь на скачивание переполнена ({MAX_QUEUE_SIZE} треков). Попробуйте позже.")
                 return

        except Exception as e:
            logger.error(f"Ошибка при прямой обработке ссылки в /search {query}: {e}")
            await progress_msg.edit_text("❌ Ошибка при обработке ссылки.")
            return
        
        if not is_direct_download_link:
            if 'progress_msg' not in locals() or not progress_msg:
                 progress_msg = await reply_func("🔍 Ищу трек...")
            else: 
                 await progress_msg.edit_text(f"🔍 Ищу трек: \"{query}\"...")
    else: 
        progress_msg = await reply_func("🔍 Ищу трек...")

    is_artist_track = bool(re.search(r'^(.+?)\s*[-–]\s*(.+)$', query))
    cache_key = f"{user_id}_{query}"
    if cache_key in search_cache and (time.time() - search_cache[cache_key]['timestamp'] < CACHE_TTL):
        results = search_cache[cache_key]['results']
    else:
        results_limit = 5 if is_artist_track or is_group else 20
        results = await asyncio.to_thread(search_youtube, query, results_limit)
        if results: search_cache[cache_key] = {'results': results, 'timestamp': time.time()}
    
    if not results:
        await progress_msg.edit_text(
            "<b>❌ Ничего не найдено</b>\n\n"
            "Попробуйте изменить запрос или проверить правильность написания.",
            parse_mode="HTML"
        )
        return

    await progress_msg.delete()
    user_search_results[user_id] = results
    result_text = f"<b>🔍 Результаты поиска</b>\n\n<b>Запрос:</b> \"{query}\""
    if is_artist_track: result_text += "\n💡 <i>Показаны наиболее точные совпадения</i>"
    await reply_func(
        f"{result_text}\n\n<b>👇 Выберите трек из списка:</b>",
        reply_markup=get_search_results_keyboard(results, page=0, user_id=user_id),
        parse_mode="HTML"
    )
    await state.set_state(SearchStates.searching)
    asyncio.create_task(clear_user_cache(user_id))

@router.inline_query()
async def inline_search(query: InlineQuery, bot_instance: Bot):
    search_text = query.query.strip()
    bot_username = (await bot_instance.get_me()).username
    
    if not search_text:
        return await query.answer([], switch_pm_text="Введите название песни или исполнителя", switch_pm_parameter="inline_help")

    logger.info(f"Инлайн-запрос от {query.from_user.id}: {search_text}")
    
    try:
        results_limit = 5
        search_results = await asyncio.to_thread(search_youtube, search_text, results_limit)

        if not search_results:
            return await query.answer([], switch_pm_text="Ничего не найдено...", switch_pm_parameter="not_found")

        inline_results = []
        for i, result in enumerate(search_results):
            title = result.get('title', 'Неизвестно')
            video_id = result.get('id')
            url = f"https://www.youtube.com/watch?v={video_id}"
            uploader = result.get('uploader', 'Неизвестно')
            
            thumbnail_url = f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"
            
            description = f"👤 {uploader}" if uploader else "🎵 Музыкальный трек"
            
            message_text = (
                f"<b>🎵 {title}</b>\n"
                f"<b>👤 Исполнитель:</b> {uploader}\n\n"
                f"<b>🔗 Ссылка:</b> {url}\n\n"
                f"<i>Отправлено через @{bot_username}</i>"
            )
            
            inline_results.append(InlineQueryResultArticle(
                id=f"{video_id}_{i}", title=title, 
                description=description, thumbnail_url=thumbnail_url,
                input_message_content=InputTextMessageContent(message_text=message_text, parse_mode="HTML"),
                reply_markup=InlineKeyboardBuilder().row(
                    InlineKeyboardButton(text="💾 Скачать трек", url=f"https://t.me/{bot_username}?start=download_{video_id}")
                ).as_markup()
            ))
        await query.answer(inline_results, cache_time=300, is_personal=True)
    except Exception as e:
        logger.error(f"Ошибка инлайн-поиска ({search_text}): {e}")
        await query.answer([], switch_pm_text="Ошибка поиска...", switch_pm_parameter="error") 