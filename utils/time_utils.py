from datetime import datetime


def now_dt():
    return datetime.now()


def now_text():
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def today_key():
    return now_dt().strftime("%Y-%m-%d")