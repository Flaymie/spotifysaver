#!/bin/bash

# =========================================================
#                                                         
#  ███████╗██████╗  ██████╗ ████████╗██╗███████╗██╗   ██╗
#  ██╔════╝██╔══██╗██╔═══██╗╚══██╔══╝██║██╔════╝╚██╗ ██╔╝
#  ███████╗██████╔╝██║   ██║   ██║   ██║█████╗   ╚████╔╝ 
#  ╚════██║██╔═══╝ ██║   ██║   ██║   ██║██╔══╝    ╚██╔╝  
#  ███████║██║     ╚██████╔╝   ██║   ██║██║        ██║   
#  ╚══════╝╚═╝      ╚═════╝    ╚═╝   ╚═╝╚═╝        ╚═╝   
#                                                         
#  ███████╗ █████╗ ██╗   ██╗███████╗██████╗ 
#  ██╔════╝██╔══██╗██║   ██║██╔════╝██╔══██╗
#  ███████╗███████║██║   ██║█████╗  ██████╔╝
#  ╚════██║██╔══██║╚██╗ ██╔╝██╔══╝  ██╔══██╗
#  ███████║██║  ██║ ╚████╔╝ ███████╗██║  ██║
#  ╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝
#
# =========================================================
#      Автор: flaymie | https://github.com/flaymie
#      Версия: 2.0.0 | Последнее обновление: $(date +%d.%m.%Y)
# =========================================================

# Цвета для красивого вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
BOLD='\033[1m'
UNDERLINE='\033[4m'
BLINK='\033[5m'
BG_BLACK='\033[40m'
BG_RED='\033[41m'
BG_GREEN='\033[42m'
BG_BLUE='\033[44m'
NC='\033[0m' # No Color

show_spinner() {
    local pid=$1
    local delay=0.1
    local spinstr='|/-\'
    while [ "$(ps a | awk '{print $1}' | grep $pid)" ]; do
        local temp=${spinstr#?}
        printf " [%c]  " "$spinstr"
        local spinstr=$temp${spinstr%"$temp"}
        sleep $delay
        printf "\b\b\b\b\b\b"
    done
    printf "    \b\b\b\b"
}

show_progress() {
    local duration=$1
    local step=$((duration/20))
    echo -ne "${CYAN}[                    ] 0%${NC}\r"
    for i in {1..20}; do
        sleep $step
        local progress=$((i*5))
        local bars=$(printf "%${i}s" | tr ' ' '█')
        local spaces=$(printf "%$((20-i))s" | tr ' ' ' ')
        echo -ne "${CYAN}[${bars}${spaces}] ${progress}%${NC}\r"
    done
    echo -ne "${GREEN}[████████████████████] 100%${NC}\n"
}

# Начало скрипта
clear
echo -e "${BG_BLACK}${MAGENTA}${BOLD}"
echo "  _____             _   _  __       _____                      "
echo " / ____|           | | (_)/ _|     / ____|                     "
echo "| (___  _ __   ___ | |_ _| |_ _   | (___   __ ___   _____ _ __ "
echo " \___ \| '_ \ / _ \| __| |  _| | | \___ \ / _\` \ \ / / _ \ '__|"
echo " ____) | |_) | (_) | |_| | | | |_| |___) | (_| |\ V /  __/ |   "
echo "|_____/| .__/ \___/ \__|_|_|  \__, |____/ \__,_| \_/ \___|_|   "
echo "       | |                     __/ |                            "
echo "       |_|                    |___/                             "
echo -e "${NC}"

echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${YELLOW}${BOLD}       SpotifySaver Bot - Музыка всегда с тобой!        ${CYAN}║${NC}"
echo -e "${CYAN}╠════════════════════════════════════════════════════════════╣${NC}"
echo -e "${CYAN}║${WHITE} 🎵 Скачивание музыки с YouTube и Spotify              ${CYAN}║${NC}"
echo -e "${CYAN}║${WHITE} 🚀 Версия: 2.0.0                                     ${CYAN}║${NC}"
echo -e "${CYAN}║${WHITE} 👨‍💻 Автор: flaymie                                    ${CYAN}║${NC}"
echo -e "${CYAN}║${WHITE} 📅 Дата запуска: $(date "+%d.%m.%Y %H:%M:%S")           ${CYAN}║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"

# Создание лог-файла
touch "$LOG_FILE"
log_info "Скрипт запущен. Логи сохраняются в файл: $LOG_FILE"

# Проверка системы
log_header "ПРОВЕРКА СИСТЕМЫ"
log_info "Операционная система: $(uname -s) $(uname -r)"
log_info "Хост: $(hostname)"
log_info "Пользователь: $(whoami)"
log_info "Текущая директория: $(pwd)"
log_info "Версия Python: $(python3 --version 2>&1)"
log_info "Версия Bash: ${BASH_VERSION}"

# Проверка наличия необходимых программ
log_header "ПРОВЕРКА ЗАВИСИМОСТЕЙ"
command -v python3 >/dev/null 2>&1 || { log_error "Python3 не установлен! Установите Python3 для продолжения."; exit 1; }
command -v pip >/dev/null 2>&1 || { log_warning "pip не найден. Будет использован pip3 если доступен."; }
command -v ffmpeg >/dev/null 2>&1 || { log_warning "ffmpeg не установлен. Некоторые функции могут работать некорректно."; }

# Проверка наличия виртуального окружения
log_header "НАСТРОЙКА ОКРУЖЕНИЯ"
if [ ! -d "venv" ]; then
    log_warning "Виртуальное окружение не найдено. Создаю..."
    echo -ne "${YELLOW}Создание виртуального окружения: ${NC}"
    python3 -m venv venv > /dev/null 2>&1 &
    show_spinner $!
    if [ -d "venv" ]; then
        log_success "Виртуальное окружение успешно создано!"
    else
        log_error "Не удалось создать виртуальное окружение!"
        exit 1
    fi
else
    log_success "Виртуальное окружение найдено."
fi

# Активация виртуального окружения
log_info "Активирую виртуальное окружение..."
source venv/bin/activate
if [ $? -eq 0 ]; then
    log_success "Виртуальное окружение активировано."
else
    log_error "Не удалось активировать виртуальное окружение!"
    exit 1
fi

# Проверка и установка зависимостей
log_header "УСТАНОВКА ЗАВИСИМОСТЕЙ"
log_info "Устанавливаю зависимости из requirements.txt..."
echo -e "${YELLOW}Это может занять некоторое время...${NC}"
pip install -r requirements.txt > "$LOG_FILE.pip" 2>&1 &
show_spinner $!

if [ $? -eq 0 ]; then
    log_success "Зависимости успешно установлены!"
    log_info "Подробный лог установки сохранен в файл: $LOG_FILE.pip"
else
    log_error "Ошибка при установке зависимостей! Проверьте лог: $LOG_FILE.pip"
    exit 1
fi

# Проверка наличия .env файла
log_header "ПРОВЕРКА КОНФИГУРАЦИИ"
if [ ! -f ".env" ]; then
    log_error "Файл .env не найден!"
    if [ -f ".env.example" ]; then
        log_warning "Найден .env.example. Копирую его в .env..."
        cp .env.example .env
        log_warning "⚠️  Пожалуйста, отредактируйте файл .env и добавьте необходимые токены!"
        
        echo -e "${BG_RED}${WHITE}${BOLD}                                                  ${NC}"
        echo -e "${BG_RED}${WHITE}${BOLD}  ВНИМАНИЕ! Необходимо настроить файл .env!       ${NC}"
        echo -e "${BG_RED}${WHITE}${BOLD}  Бот не будет работать без настройки токенов.    ${NC}"
        echo -e "${BG_RED}${WHITE}${BOLD}                                                  ${NC}"
        
        exit 1
    else
        log_error "Ошибка: Файл .env.example не найден. Невозможно продолжить."
        exit 1
    fi
else
    log_success "Файл .env найден."
    # Проверка содержимого .env файла
    if grep -q "BOT_TOKEN=" .env && ! grep -q "BOT_TOKEN=$" .env; then
        log_success "Токен бота настроен."
    else
        log_warning "Токен бота не настроен в файле .env!"
    fi
    
    if grep -q "SPOTIFY_CLIENT_ID=" .env && ! grep -q "SPOTIFY_CLIENT_ID=$" .env; then
        log_success "Spotify Client ID настроен."
    else
        log_warning "Spotify Client ID не настроен в файле .env!"
    fi
    
    if grep -q "SPOTIFY_CLIENT_SECRET=" .env && ! grep -q "SPOTIFY_CLIENT_SECRET=$" .env; then
        log_success "Spotify Client Secret настроен."
    else
        log_warning "Spotify Client Secret не настроен в файле .env!"
    fi
fi

# Проверка наличия папки downloads
log_header "ПРОВЕРКА ФАЙЛОВОЙ СТРУКТУРЫ"
if [ ! -d "downloads" ]; then
    log_warning "Папка для загрузок не найдена. Создаю..."
    mkdir -p downloads
    log_success "Папка для загрузок создана: $(pwd)/downloads"
else
    log_success "Папка для загрузок найдена: $(pwd)/downloads"
    log_info "Количество файлов в папке: $(ls -1 downloads | wc -l | tr -d ' ')"
fi

# Проверка базы данных
if [ -f "user_data.db" ]; then
    log_success "База данных найдена: $(pwd)/user_data.db"
    log_info "Размер базы данных: $(du -h user_data.db | cut -f1)"
else
    log_warning "База данных не найдена. Будет создана при первом запуске."
fi

# Проверка лог-файла
if [ -f "bot.log" ]; then
    log_success "Лог-файл бота найден: $(pwd)/bot.log"
    log_info "Размер лог-файла: $(du -h bot.log | cut -f1)"
    log_info "Последние записи в логе:"
    echo -e "${BLUE}----------------------------------------${NC}"
    tail -n 5 bot.log | while read line; do
        echo -e "${CYAN}| ${NC}$line"
    done
    echo -e "${BLUE}----------------------------------------${NC}"
else
    log_warning "Лог-файл бота не найден. Будет создан при первом запуске."
fi

# Финальная подготовка к запуску
log_header "ЗАПУСК БОТА"
log_info "Подготовка к запуску SpotifySaver бота..."

echo -e "\n${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${YELLOW}${BOLD}                ЗАПУСК SPOTIFYSAVER                    ${CYAN}║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"

# Имитация загрузки для красоты
echo -e "${CYAN}Инициализация компонентов:${NC}"
show_progress 3

# Запуск бота
log_info "Запускаю SpotifySaver бота..."
echo -e "\n${BG_GREEN}${WHITE}${BOLD}  🚀 SPOTIFYSAVER БОТ ЗАПУЩЕН! НАЖМИТЕ CTRL+C ДЛЯ ОСТАНОВКИ  ${NC}\n"

# Запуск основного процесса
python3 main.py 2>&1 | tee -a "$LOG_FILE"
BOT_EXIT_CODE=$?

# Обработка завершения работы
if [ $BOT_EXIT_CODE -eq 0 ]; then
    log_success "Бот завершил работу с кодом: $BOT_EXIT_CODE (успешно)"
else
    log_error "Бот завершил работу с кодом: $BOT_EXIT_CODE (с ошибкой)"
fi

# Деактивация виртуального окружения при выходе
deactivate
log_info "Виртуальное окружение деактивировано."

# Итоговая информация
log_header "ИТОГИ СЕССИИ"


echo -e "\n${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║${GREEN}${BOLD}        СПАСИБО ЗА ИСПОЛЬЗОВАНИЕ SPOTIFYSAVER!         ${CYAN}║${NC}"
echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}" 