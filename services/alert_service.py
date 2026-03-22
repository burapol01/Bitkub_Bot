try:
    import winsound
    HAS_WINSOUND = True
except ImportError:
    HAS_WINSOUND = False


def beep_alert(kind: str):
    if not HAS_WINSOUND:
        return

    if kind == "BUY":
        winsound.Beep(1200, 500)
    elif kind == "SELL":
        winsound.Beep(1800, 700)
    elif kind == "STOP_LOSS":
        winsound.Beep(900, 900)
    elif kind == "TAKE_PROFIT":
        winsound.Beep(2000, 900)