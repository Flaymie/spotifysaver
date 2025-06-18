import asyncio
from typing import Callable, Dict, Any, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject
from cachetools import TTLCache

# Простой антифлуд middleware
class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit: float = 0.7, burst_limit: int = 3, period: float = 10.0):
        """
        :param rate_limit: Максимальная частота сообщений (сообщений в секунду) в среднем.
                          То есть, не более 1 сообщения каждые 1/rate_limit секунд.
                          0.7 -> примерно 1 сообщение в 1.4 секунды.
        :param burst_limit: Сколько сообщений можно отправить подряд без задержки, 
                            прежде чем начнет действовать rate_limit.
        :param period: Период (в секундах), за который сбрасывается счетчик burst.
        """
        # Используем TTLCache для хранения времени последних сообщений пользователя
        # Ключ - user_id, значение - список времен последних сообщений
        # TTL - время, через которое запись о пользователе удаляется, если он неактивен
        self.cache = TTLCache(maxsize=10_000, ttl=period + rate_limit * burst_limit + 5) # Немного запаса по ttl
        
        self.rate_limit_sec = 1.0 / rate_limit if rate_limit > 0 else float('inf')
        self.burst_limit = burst_limit
        self.period = period

    async def __call__(
        self, 
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject, 
        data: Dict[str, Any]
    ) -> Any:
        
        # Применяем только к Message и CallbackQuery, можно расширить
        if not isinstance(event, (Message)):
            return await handler(event, data)

        user_id = event.from_user.id
        now = asyncio.get_running_loop().time() # Более точное время

        # Получаем историю сообщений пользователя из кэша
        user_timestamps = self.cache.get(user_id, [])

        # Убираем старые временные метки, которые вышли за пределы периода burst
        # Это не совсем классический token bucket, а скорее sliding window
        user_timestamps = [ts for ts in user_timestamps if now - ts < self.period]
        
        # Проверяем burst лимит
        if len(user_timestamps) >= self.burst_limit:
            # Если burst лимит достигнут, проверяем rate лимит по последнему сообщению
            # Должно пройти self.rate_limit_sec с момента последнего сообщения в burst-серии
            if user_timestamps and (now - user_timestamps[-1] < self.rate_limit_sec):
                # Если и rate лимит нарушен, троттлим
                # await event.answer("Пожалуйста, не так быстро!") # Для Message это не сработает так
                if isinstance(event, Message):
                    # Можно отправить сообщение, но это может быть спамно само по себе
                    # await event.reply("⏳ Пожалуйста, не так быстро! Подождите немного.")
                    # Лучше просто проигнорировать (не вызывать handler)
                    pass # Игнорируем сообщение
                # Для CallbackQuery можно сделать event.answer(...)
                # logger.info(f"Троттлинг для user_id={user_id}")
                return # Не передаем управление дальше
        
        # Добавляем текущее время и обновляем кэш
        user_timestamps.append(now)
        self.cache[user_id] = user_timestamps[-self.burst_limit:] # Храним только последние N для burst
        
        return await handler(event, data) 