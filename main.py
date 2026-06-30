"""Тонкая точка входа. Реальное приложение — в app.main:app.

Запуск для разработки:
    uvicorn app.main:app --reload
или:
    python main.py
"""

from app.main import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
