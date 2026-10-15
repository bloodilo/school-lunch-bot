# -*- coding: utf-8 -*-
# Обед в школе: настройки. Скопировать в /srv/lunch/config.py и заполнить. Файл с секретами в git не класть.
TG_API_ID = 0                      # my.telegram.org
TG_API_HASH = "..."
BOT_TOKEN = "123456:ABC..."        # токен бота от @BotFather

CHAT_ID = -1001234567890           # родительский чат (форум)
TOPIC_ID = 0                       # тема, куда школа кидает опрос

DAD_ID = 0                         # Telegram id родителя
KID_ID = 0                         # Telegram id ребёнка

DEADLINE_HOUR = 9                  # срез опроса в школе
REMIND_EVE_HOUR = 21               # напоминание накануне вечером
REMIND_MORNING_HOUR = 7            # напоминание утром
REMIND_MORNING_MIN = 30
DEFAULT_HOUR = 8                   # автозаказ, если никто не нажал
DEFAULT_MIN = 30
# суп + первое второе + первый гарнир; None = без автозаказа
DEFAULT_ORDER = ("soup", "main1", "side1")

# дни недели без обеда (0 = понедельник)
SKIP_WEEKDAYS = [3]

# блюда, которые ребёнок не ест: такой суп в автозаказ не попадает (подстрока, без регистра)
AVOID_DISHES = ["горох"]
