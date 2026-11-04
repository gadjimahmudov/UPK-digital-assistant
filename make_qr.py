"""Генерация QR-кодов для всех мест.
Нужна библиотека:  pip install "qrcode[pil]"
Запуск:            python make_qr.py http://192.168.1.50:8000
(адрес, по которому ТЕЛЕФОН видит сервер: IP ноутбука в одной Wi-Fi сети, не localhost)
Результат: папка qr/ с файлами QR_<ключ>.png и список ссылок в консоли."""
import os, sys
try:
    import qrcode
except ImportError:
    sys.exit('Установите библиотеку:  pip install "qrcode[pil]"')
from app import LOCS

base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
os.makedirs("qr", exist_ok=True)
for building, floor, room, zone, key in LOCS:
    url = f"{base}/?loc={key}"
    qrcode.make(url).save(f"qr/QR_{key.split('-')[0]}.png")
    print(f"{building}, {room or zone}: {url}")
print("\nГотово: файлы лежат в папке qr/. Отсканируйте один камерой телефона и проверьте, что форма открывается.")
