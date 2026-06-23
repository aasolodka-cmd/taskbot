import os


class Config:
    # Обязательные переменные окружения
    BOT_TOKEN: str = os.environ["BOT_TOKEN"]
    MANAGER_ID: int = int(os.environ["MANAGER_ID"])

    # Часовой пояс (меняй под свой)
    TIMEZONE: str = os.environ.get("TIMEZONE", "Europe/Moscow")

    # Время утренней сводки (часы, минуты)
    _morning = os.environ.get("MORNING_TIME", "10:00").split(":")
    MORNING_TIME: tuple = (int(_morning[0]), int(_morning[1]))

    # Время вечернего отчёта (часы, минуты)
    _evening = os.environ.get("EVENING_TIME", "23:00").split(":")
    EVENING_TIME: tuple = (int(_evening[0]), int(_evening[1]))
