import os


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "A_VERY_SECRET_VALUE"
