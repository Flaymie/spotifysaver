import os
import re
import uuid
import yt_dlp
import spotipy
import tempfile
import json
import requests
import concurrent.futures
from spotipy.oauth2 import SpotifyClientCredentials
import time
from config import SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, DOWNLOADS_DIR, GENIUS_ACCESS_TOKEN
import socket
import urllib.parse
import logging
import lyricsgenius

logger = logging.getLogger(__name__)

# Инициализируем Genius API, если токен доступен
genius_api = None
if GENIUS_ACCESS_TOKEN:
    genius_api = lyricsgenius.Genius(GENIUS_ACCESS_TOKEN, verbose=False, remove_section_headers=True, skip_non_songs=True)
else:
    logger.warning("Токен Genius API не найден. Функция получения текстов песен будет недоступна.")

class YouTubeError(Exception):
    """Ошибка при работе с YouTube API"""
    pass

class SpotifyError(Exception):
    """Ошибка при работе с Spotify API"""
    pass

class DownloadError(Exception):
    """Ошибка при скачивании аудио"""
    pass

def is_youtube_url(url):
    youtube_regex = r'(https?://)?(www\.)?(youtube\.com|youtu\.?be)/.+'
    return bool(re.match(youtube_regex, url))

def is_spotify_url(url):
    spotify_regex = r'(https?://)?(open\.)?spotify\.com/.+'
    return bool(re.match(spotify_regex, url))

def is_valid_youtube_id(video_id):
    """Проверяет, является ли ID YouTube корректным"""
    # YouTube ID может быть разной длины, но обычно от 11 символов
    if not video_id or len(video_id) < 8:
        return False
    
    # YouTube ID содержит только буквы, цифры, дефисы и подчеркивания
    valid_chars = re.match(r'^[A-Za-z0-9_-]+$', video_id)
    return bool(valid_chars)

def search_youtube(query, limit=5):
    """
    Ищет видео на YouTube по запросу и возвращает результаты поиска.
    """
    try:
        # Установим таймаут для запросов
        socket.setdefaulttimeout(10)
        
        # Логирование запроса
        logger.info(f"Начинаем поиск YouTube: {query}")
        
        # Проверяем, является ли запрос прямой ссылкой на YouTube
        if is_youtube_url(query):
            video_id = extract_video_id(query)
            if video_id:
                try:
                    # Получаем информацию о видео
                    result = [{
                        'id': video_id,
                        'title': get_video_title(video_id),
                        'uploader': get_video_uploader(video_id),
                        'duration': get_video_duration(video_id),
                        'url': f'https://www.youtube.com/watch?v={video_id}'
                    }]
                    return result
                except Exception as e:
                    logger.error(f"Ошибка при получении информации о видео: {e}")
        
        # Очищаем и подготавливаем запрос
        query = query.strip()
        
        # Проверяем, похож ли запрос на формат "артист - трек"
        artist_track_match = re.search(r'^(.+?)\s*[-–]\s*(.+)$', query)
        
        # Для хранения результатов поиска
        results = []
        
        # Определяем стратегии поиска и запускаем только одну наиболее подходящую
        # для ускорения работы с инлайн-запросами
        
        # Стратегия 1: Прямой поиск (для коротких запросов)
        if len(query) < 50:
            search_params = {
                'search_query': query,
                'sp': 'EgIQAQ%3D%3D'  # Фильтр только для музыки
            }
            
            search_url = f"https://www.youtube.com/results?{urllib.parse.urlencode(search_params)}"
            
            # Выполняем запрос
            html = make_request(search_url)
            if not html:
                logger.warning(f"Не удалось получить HTML для запроса: {query}")
                return []
                
            # Извлекаем результаты
            results = extract_video_info_from_html(html, limit)
            
            # Если нашли достаточно результатов, возвращаем их
            if results and len(results) > 0:
                return results[:limit]
        
        # Если у нас формат "артист - трек", используем специальный поиск
        if artist_track_match:
            artist = artist_track_match.group(1).strip()
            track = artist_track_match.group(2).strip()
            
            # Формируем запрос с explicit указанием на музыку
            music_query = f"{artist} {track} music audio"
            search_params = {
                'search_query': music_query,
                'sp': 'EgIQAQ%3D%3D'  # Фильтр только для музыки
            }
            
            search_url = f"https://www.youtube.com/results?{urllib.parse.urlencode(search_params)}"
            
            # Выполняем запрос
            html = make_request(search_url)
            if html:
                artist_track_results = extract_video_info_from_html(html, limit)
                
                # Добавляем только уникальные результаты
                for result in artist_track_results:
                    if result not in results:
                        results.append(result)
                        if len(results) >= limit:
                            break
        
        # Если до сих пор ничего не нашли, пробуем запасной вариант
        if not results:
            # Формируем запрос с добавлением "audio"
            backup_query = f"{query} audio"
            search_params = {
                'search_query': backup_query,
                'sp': 'EgIQAQ%3D%3D'  # Фильтр только для музыки
            }
            
            search_url = f"https://www.youtube.com/results?{urllib.parse.urlencode(search_params)}"
            
            # Выполняем запрос
            html = make_request(search_url)
            if html:
                backup_results = extract_video_info_from_html(html, limit)
                results.extend(backup_results)
        
        # Возвращаем результаты
        return results[:limit]
        
    except Exception as e:
        logger.error(f"Ошибка при поиске на YouTube: {e}")
        return []

def get_spotify_track_info(track_url):
    if not SPOTIFY_CLIENT_ID or not SPOTIFY_CLIENT_SECRET:
        raise SpotifyError("Не настроены ключи Spotify API. Проверьте .env файл.")
        
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET
        ))
        
        track_id = track_url.split('/')[-1].split('?')[0]
        track = sp.track(track_id)
        
        artists = ", ".join([artist['name'] for artist in track['artists']])
        track_name = track['name']
        search_query = f"{artists} - {track_name}"
        
        return search_query
    except Exception as e:
        print(f"Ошибка при получении информации из Spotify: {e}")
        raise SpotifyError(f"Ошибка при получении данных из Spotify: {str(e)}")

def get_lyrics_for_track(artist_name, track_name):
    """
    Получает текст песни по имени исполнителя и названию трека, используя Genius API.
    
    Args:
        artist_name: Имя исполнителя
        track_name: Название трека
        
    Returns:
        dict: Словарь с текстом песни или сообщение об ошибке
    """
    if not genius_api:
        return {
            "success": False,
            "error": "Genius API не инициализирован (токен не найден)",
            "track_name": track_name,
            "artist_name": artist_name
        }

    try:
        # Очистка названия трека от дополнительной информации в скобках
        cleaned_track_name = re.sub(r'\([^)]*\)', '', track_name).strip()
        cleaned_artist_name = artist_name.strip()
        
        logger.info(f"Поиск текста песни на Genius для: '{cleaned_track_name}' - '{cleaned_artist_name}'")
        
        # Ищем песню на Genius
        song = genius_api.search_song(cleaned_track_name, cleaned_artist_name)
        
        if song and song.lyrics:
            # Убираем первую строку, если она является заголовком типа "Track Name Lyrics"
            lines = song.lyrics.split('\n')
            if len(lines) > 1 and lines[0].lower().endswith("lyrics") and cleaned_track_name.lower() in lines[0].lower():
                lyrics_text = '\n'.join(lines[1:]).strip()
            else:
                lyrics_text = song.lyrics.strip()
            
            # Убираем Embed в конце, если есть
            if lyrics_text.endswith("Embed"):
                lyrics_text = lyrics_text[:-5].strip()
                # Также убираем число перед Embed, если оно есть (например, 123Embed)
                match_embed_number = re.search(r'\d+$', lyrics_text)
                if match_embed_number:
                    lyrics_text = lyrics_text[:match_embed_number.start()].strip()

            raw_artist_string = song.artist
            raw_title_string = song.title 
            
            main_artist = raw_artist_string # По умолчанию основной исполнитель - это все из song.artist
            featured_artists_list = []

            # Паттерн для извлечения фитов: 
            # Ищет (feat. ...), (ft. ...), (featuring ...)
            # Также пытается захватить артистов, перечисленных через & или запятую внутри скобок
            feat_patterns = [
                r'(?:\(|\[)?(?:feat|ft|featuring)\.?\s+([^)\]]+)(?:\)|\])?',
                r'(?:\s+with\s+)([^)\]]+)' # для конструкций "Artist1 with Artist2"
            ]
            
            processed_artist_string = raw_artist_string
            processed_title_string = raw_title_string

            # Сначала ищем фиты в строке исполнителя
            for pattern in feat_patterns:
                match = re.search(pattern, processed_artist_string, re.IGNORECASE)
                if match:
                    main_artist = processed_artist_string[:match.start()].strip() # Все до "feat."
                    featured_artists_str = match.group(1).strip()
                    # Разделяем фиты, если их несколько (например, "Artist1, Artist2 & Artist3")
                    # Убираем повторное добавление, если они уже есть
                    current_feats = [artist.strip() for artist in re.split(r',\s*|\s+&\s+', featured_artists_str) if artist.strip()]
                    for ft_artist in current_feats:
                        if ft_artist not in featured_artists_list:
                             featured_artists_list.append(ft_artist)
                    # Удаляем часть с фитами из строки исполнителя для дальнейшей обработки
                    processed_artist_string = main_artist 
                    break 
            
            # Затем ищем фиты в названии трека, если они не были найдены в исполнителе
            if not featured_artists_list:
                for pattern in feat_patterns:
                    match = re.search(pattern, processed_title_string, re.IGNORECASE)
                    if match:
                        # Если нашли фит в названии, предполагаем, что processed_artist_string - это основной исполнитель
                        main_artist = processed_artist_string.strip()
                        featured_artists_str = match.group(1).strip()
                        current_feats = [artist.strip() for artist in re.split(r',\s*|\s+&\s+', featured_artists_str) if artist.strip()]
                        for ft_artist in current_feats:
                            if ft_artist not in featured_artists_list:
                                featured_artists_list.append(ft_artist)
                        # Можно также попробовать очистить название трека от информации о фитах
                        # processed_title_string = processed_title_string[:match.start()].strip()
                        break
            
            # Если основной артист содержит "(Artist Name)", извлекаем только имя
            # Например, "МУККА (MUKKA)" -> "МУККА"
            # Стараемся не затронуть случаи типа "Artist (from band)" или "Artist (Official Video)" - это больше для title
            artist_name_match = re.match(r'^([^(]+)\s*\([^)]*\)$', main_artist)
            if artist_name_match and not any(kw in main_artist.lower() for kw in ['band', 'official', 'feat', 'ft']):
                 # Проверяем, что в скобках нечто похожее на дублирование или уточнение основного имени,
                 # а не что-то совершенно другое.
                 # Это очень грубая эвристика.
                 part_in_parentheses = main_artist[artist_name_match.end(1):].strip()
                 ifLevenshtein = True # Заглушка, нужна реальная функция Левенштейна или другая проверка
                 try:
                     from Levenshtein import distance as levenshtein_distance
                     # Сравниваем то, что до скобок, с тем, что в скобках, если они достаточно похожи
                     if levenshtein_distance(artist_name_match.group(1).strip().lower(), part_in_parentheses.lower().strip('()')) < len(artist_name_match.group(1).strip()) * 0.5:
                        main_artist = artist_name_match.group(1).strip()
                 except ImportError:
                     # Если нет Levenshtein, используем более простую проверку
                     if artist_name_match.group(1).strip().lower() in part_in_parentheses.lower():
                        main_artist = artist_name_match.group(1).strip()

            # Удаляем дубликаты из featured_artists_list, если основной артист попал туда
            if main_artist in featured_artists_list:
                featured_artists_list.remove(main_artist)

            # Проверяем, не попал ли основной артист (или его часть) снова в фиты после всех манипуляций
            # Это актуально, если, например, song.artist был "ArtistA feat. ArtistA & ArtistB"
            final_featured_artists = []
            for fa in featured_artists_list:
                if fa.lower() != main_artist.lower() and main_artist.lower() not in fa.lower() and fa.lower() not in main_artist.lower() :
                    final_featured_artists.append(fa)

            return {
                "success": True,
                "lyrics": lyrics_text,
                "source": "Genius.com",
                "source_url": song.url if hasattr(song, 'url') else None,
                "track_name": raw_title_string, # Возвращаем оригинальное название трека из Genius
                "artist_name": main_artist.strip(),
                "featured_artists": final_featured_artists
            }
        else:
            logger.warning(f"Текст песни не найден на Genius для: '{cleaned_track_name}' - '{cleaned_artist_name}'")
            return {
                "success": False,
                "error": "Текст песни не найден на Genius.com",
                "track_name": track_name,
                "artist_name": artist_name
            }
    
    except Exception as e:
        logger.error(f"Ошибка при получении текста песни с Genius: {e}", exc_info=True)
        return {
            "success": False,
            "error": f"Ошибка при работе с Genius API: {str(e)}",
            "track_name": track_name,
            "artist_name": artist_name
        }

def download_audio(video_url):
    """
    Скачивает аудио с YouTube
    
    Args:
        video_url: URL видео на YouTube
        
    Returns:
        tuple: (путь к файлу, название трека)
    
    Raises:
        DownloadError: если произошла ошибка при скачивании
    """
    # Создаем директорию для загрузок, если её нет
    if not os.path.exists(DOWNLOADS_DIR):
        os.makedirs(DOWNLOADS_DIR)
    
    try:
        # Получаем информацию о видео без скачивания
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            
            if not info_dict:
                raise DownloadError("Не удалось получить информацию о видео")
            
            title = info_dict.get('title', 'Unknown Title')
            duration = info_dict.get('duration')
            
            if duration is not None and duration < 1:
                raise DownloadError(f"Видео имеет нулевую длительность: {duration} секунд")
            
            print(f"Найдено видео: {title}, длительность: {duration} сек")
        
        # Используем временную директорию для скачивания
        with tempfile.TemporaryDirectory() as temp_dir:
            ydl_opts = {
                'format': 'bestaudio/best',
                'extractaudio': True,
                'audioformat': 'mp3',
                'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                'noplaylist': True,
                'quiet': True,
                'no_warnings': True,
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
            }
            
            # Скачиваем видео
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            # Ищем скачанный файл в временной директории
            for file in os.listdir(temp_dir):
                if file.endswith('.mp3'):
                    # Копируем файл в папку downloads с уникальным именем
                    unique_id = uuid.uuid4().hex
                    output_file = os.path.join(DOWNLOADS_DIR, f"{unique_id}.mp3")
                    
                    # Копируем файл
                    with open(os.path.join(temp_dir, file), 'rb') as source:
                        with open(output_file, 'wb') as dest:
                            dest.write(source.read())
                    
                    # Проверяем размер файла
                    file_size = os.path.getsize(output_file)
                    if file_size < 1024:  # Меньше 1KB
                        raise DownloadError(f"Скачанный файл слишком маленький: {file_size} байт")
                    
                    return output_file, title
            
            # Если файл не найден
            raise DownloadError("Не удалось найти скачанный файл")
        
    except Exception as e:
        print(f"Ошибка при скачивании: {e}")
        raise DownloadError(f"Не удалось скачать аудио: {str(e)}")

def extract_video_id(url):
    """Извлекает ID видео из YouTube URL"""
    try:
        # Паттерны для разных форматов URL YouTube
        patterns = [
            r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
            r'(?:embed\/)([0-9A-Za-z_-]{11})',
            r'(?:shorts\/)([0-9A-Za-z_-]{11})',
            r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None
    except Exception as e:
        logger.error(f"Ошибка при извлечении ID видео: {e}")
        return None

def make_request(url):
    """Выполняет HTTP запрос и возвращает HTML страницу"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()  # Проверяем на ошибки HTTP
        return response.text
    except Exception as e:
        logger.error(f"Ошибка при запросе {url}: {e}")
        return None

def extract_video_info_from_html(html, limit=5):
    """Извлекает информацию о видео из HTML страницы YouTube"""
    try:
        results = []
        
        # Ищем JSON-данные в скрипте
        start_marker = 'var ytInitialData = '
        if start_marker in html:
            json_start = html.index(start_marker) + len(start_marker)
            json_text = html[json_start:]
            json_end = json_text.find(';</script>')
            if json_end > 0:
                json_text = json_text[:json_end]
                
                try:
                    data = json.loads(json_text)
                    
                    # Ищем результаты в структуре данных
                    contents = data.get('contents', {}).get('twoColumnSearchResultsRenderer', {}).get('primaryContents', {})
                    
                    if not contents:
                        return results
                        
                    # Извлекаем секцию с результатами
                    items = contents.get('sectionListRenderer', {}).get('contents', [])
                    
                    if not items:
                        return results
                        
                    # Извлекаем результаты
                    for item in items:
                        if 'itemSectionRenderer' in item:
                            videos = item.get('itemSectionRenderer', {}).get('contents', [])
                            
                            for video in videos:
                                if 'videoRenderer' in video:
                                    video_data = video.get('videoRenderer', {})
                                    
                                    # Проверяем, что это видео, а не плейлист или канал
                                    if 'videoId' in video_data:
                                        video_id = video_data.get('videoId')
                                        title = extract_text(video_data.get('title', {}))
                                        uploader = extract_text(video_data.get('ownerText', {}))
                                        
                                        # Добавляем информацию в результаты
                                        results.append({
                                            'id': video_id,
                                            'title': title or 'Неизвестно',
                                            'uploader': uploader or 'Неизвестно',
                                            'duration': 0,  # Не извлекаем длительность для ускорения
                                            'url': f'https://www.youtube.com/watch?v={video_id}'
                                        })
                                        
                                        # Проверяем, достаточно ли результатов
                                        if len(results) >= limit:
                                            return results
                except json.JSONDecodeError as e:
                    logger.error(f"Ошибка декодирования JSON: {e}")
        
        return results
    except Exception as e:
        logger.error(f"Ошибка при извлечении информации из HTML: {e}")
        return []

def extract_text(obj):
    """Извлекает текст из объекта YouTube API"""
    if not obj:
        return None
        
    if 'runs' in obj:
        text_parts = []
        for run in obj.get('runs', []):
            if 'text' in run:
                text_parts.append(run['text'])
        return ' '.join(text_parts)
    elif 'simpleText' in obj:
        return obj['simpleText']
    return None

def get_video_title(video_id):
    """Получает название видео по его ID"""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        html = make_request(url)
        if html:
            title_match = re.search(r'<meta name="title" content="(.*?)"',
                                  html, re.IGNORECASE)
            if title_match:
                return title_match.group(1)
            
            # Альтернативный поиск
            title_match = re.search(r'<title>(.*?) - YouTube</title>', html, re.IGNORECASE)
            if title_match:
                return title_match.group(1)
        return "Неизвестное видео"
    except Exception as e:
        logger.error(f"Ошибка при получении названия видео {video_id}: {e}")
        return "Неизвестное видео"

def get_video_uploader(video_id):
    """Получает имя загрузчика видео по его ID"""
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        html = make_request(url)
        if html:
            channel_match = re.search(r'<link itemprop="name" content="(.*?)"',
                                   html, re.IGNORECASE)
            if channel_match:
                return channel_match.group(1)
        return "Неизвестный канал"
    except Exception as e:
        logger.error(f"Ошибка при получении автора видео {video_id}: {e}")
        return "Неизвестный канал"

def get_video_duration(video_id):
    """Получает длительность видео по его ID (в секундах)"""
    try:
        return 0  # Для ускорения не получаем реальную длительность
    except Exception as e:
        logger.error(f"Ошибка при получении длительности видео {video_id}: {e}")
        return 0 