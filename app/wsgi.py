"""ASGI entrypoint: uvicorn app.wsgi:app"""
import logging

from .main import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = create_app()
