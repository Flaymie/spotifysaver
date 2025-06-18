from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from config import RESULTS_PER_PAGE
import re

# Словарь для хранения полных ID видео
video_id_map = {}

# Эмодзи для разных типов контента и действий
EMOJI = {
    'music': '🎵',
    'download': '💾',
    'lyrics': '📝',
    'back': '↩️',
    'next': '➡️',
    'prev': '⬅️',
    'search': '🔍',
    'artist': '👤',
    'play': '▶️',
    'popular': '🔥',
    'new': '✨',
    'settings': '⚙️'
}

def get_search_results_keyboard(results, page=0, user_id=None):
    builder = InlineKeyboardBuilder()
    
    start_idx = page * RESULTS_PER_PAGE
    end_idx = min(start_idx + RESULTS_PER_PAGE, len(results))
    
    for i in range(start_idx, end_idx):
        result = results[i]
        title = result['title']
        video_id = result['id']
        uploader = result.get('uploader', '').strip()
        
        # Определяем эмодзи для кнопки в зависимости от позиции
        position_emoji = f"{i+1+start_idx}. " if i+start_idx < 9 else ""
        
        # Создаем текст кнопки с названием и автором
        if uploader:
            # Обрезаем длинные названия и имена авторов
            if len(title) > 35:
                title = title[:32] + "..."
            
            if len(uploader) > 15:
                uploader = uploader[:12] + "..."
                
            button_text = f"{position_emoji}{title}\n{EMOJI['artist']} {uploader}"
        else:
            # Если автор не указан, просто обрезаем длинное название
            if len(title) > 45:
                title = title[:42] + "..."
            button_text = f"{position_emoji}{title}"
        
        # Создаем короткий индекс для callback_data
        index_key = f"{user_id}_{i}"
        video_id_map[index_key] = video_id
        
        # Добавляем каждую кнопку в отдельную строку
        builder.row(InlineKeyboardButton(
            text=button_text,
            callback_data=f"download_{index_key}"
        ))
    
    # Добавляем кнопки пагинации
    nav_buttons = []
    
    # Добавляем индикатор страницы
    current_page = f"{page + 1}/{(len(results) + RESULTS_PER_PAGE - 1) // RESULTS_PER_PAGE}"
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(
            text=f"{EMOJI['prev']} Назад",
            callback_data=f"page_{page-1}"
        ))
    
    # Добавляем индикатор текущей страницы
    nav_buttons.append(InlineKeyboardButton(
        text=f"📄 {current_page}",
        callback_data=f"current_page"  # Это действие ничего не делает, просто для отображения
    ))
    
    if end_idx < len(results):
        nav_buttons.append(InlineKeyboardButton(
            text=f"{EMOJI['next']} Вперед",
            callback_data=f"page_{page+1}"
        ))
    
    if nav_buttons:
        builder.row(*nav_buttons)
    
    return builder.as_markup()

def get_video_id_by_key(key):
    """Получает полный ID видео по ключу"""
    return video_id_map.get(key)

def get_track_keyboard(track_info, has_back_button=True):
    """
    Создает клавиатуру для трека с кнопкой для показа текста песни
    
    Args:
        track_info: Словарь с информацией о треке
        has_back_button: Добавлять ли кнопку возврата к результатам
        
    Returns:
        InlineKeyboardMarkup: Клавиатура с кнопками
    """
    builder = InlineKeyboardBuilder()
    
    # Очищаем и нормализуем данные для callback_data
    # Ограничиваем длину и удаляем специальные символы для предотвращения ошибки BUTTON_DATA_INVALID
    title = re.sub(r'[^\w\s-]', '', track_info.get('title', ''))[:20]
    uploader = re.sub(r'[^\w\s-]', '', track_info.get('uploader', ''))[:20]
    
    # Добавляем кнопки действий
    builder.row(InlineKeyboardButton(
        text=f"{EMOJI['lyrics']} Текст песни",
        callback_data=f"lyrics_{title}_{uploader}"
    ))
    
    # Добавляем кнопку возврата к результатам поиска, если нужно
    if has_back_button:
        builder.row(InlineKeyboardButton(
            text=f"{EMOJI['back']} К результатам поиска",
            callback_data="back_to_results"
        ))
    
    return builder.as_markup() 