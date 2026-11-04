"""Точка входа для хостинга (WSGI): PythonAnywhere, gunicorn и др.
На хостинге блок `if __name__ == "__main__"` из app.py не выполняется, поэтому базу создаём здесь
(init_db безопасен при повторном вызове: таблицы создаются «если нет», данные добавляются только в пустую базу).

PythonAnywhere:  from wsgi import application
gunicorn:        gunicorn wsgi:app
"""
from app import app, init_db

init_db()
application = app  # имя, которого ждёт PythonAnywhere
