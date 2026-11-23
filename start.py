import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.chdir(HERE)
sys.path.insert(0, str(HERE))

import clipper  # noqa: E402


def ask_int(text, default):
    raw = input(f"{text} [{default}]: ").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def ask_url():
    url = ""
    while not url:
        url = input("Вставь ссылку на ролик YouTube и нажми Enter: ").strip().strip('"')
    return url


def choose_audio(url):
    print("\nПроверяю, какие озвучки есть у ролика...")
    try:
        langs = clipper.list_audio_languages(url)
    except Exception as e:
        print(f"Не удалось получить список озвучек ({e}). Возьму озвучку по умолчанию.")
        return ""
    if len(langs) < 2:
        print("У ролика одна озвучка, выбирать не из чего.")
        return ""

    default = next((i for i, l in enumerate(langs, 1) if l[2]), 1)
    print("\nДоступные озвучки:")
    for i, (code, name, original) in enumerate(langs, 1):
        mark = "   <- оригинал" if original else ""
        print(f"  {i}. {name} [{code}]{mark}")
    n = ask_int("Номер озвучки", default)
    if n < 1 or n > len(langs) or n == default:
        return ""  # оригинал / значение по умолчанию
    return langs[n - 1][0]


def choose_subs():
    print("\nЯзык субтитров:")
    print("  1. Как в озвучке (без перевода)")
    print("  2. Русский (перевод)")
    print("  3. English (перевод)")
    print("  4. Другой язык (введи код, например es, de, fr)")
    print("  5. Без субтитров")
    n = ask_int("Номер", 1)
    if n == 2:
        return "ru"
    if n == 3:
        return "en"
    if n == 4:
        code = input("Код языка: ").strip().lower()
        return code or "orig"
    if n == 5:
        return "none"
    return "orig"


def main():
    print("=== Нарезка роликов YouTube для TikTok ===\n")
    url = ask_url()

    while True:
        audio = choose_audio(url)
        subs = choose_subs()
        clips = ask_int("\nСколько клипов вырезать", 3)
        length = ask_int("Длина одного клипа в секундах", 40)

        while True:
            sys.argv = ["clipper.py", url, "--clips", str(clips), "--length", str(length), "--subs", subs]
            if audio:
                sys.argv += ["--audio", audio]
            try:
                clipper.main()
            except SystemExit:
                pass
            except KeyboardInterrupt:
                print("\nОстановлено.")
            except Exception as e:
                print(f"\nОшибка: {e}")
            else:
                print("\nКлипы лежат в папке clips рядом с этой программой.")
                try:
                    os.startfile(HERE / "clips")
                except Exception:
                    pass

            nxt = input(
                "\nEnter - ещё клипы из этого ролика, n - другой ролик "
                "(или сразу вставь ссылку), q - выход: "
            ).strip().strip('"')
            low = nxt.lower()
            if low in ("q", "й", "exit"):
                return
            if low.startswith("http"):
                url = nxt
                break
            if low in ("n", "т"):
                url = ask_url()
                break


if __name__ == "__main__":
    main()
