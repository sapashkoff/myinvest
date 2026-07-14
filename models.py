from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from cryptography.fernet import Fernet
import os

db = SQLAlchemy()

# Ключ шифрования для токенов
def get_cipher():
    key = os.environ.get('ENCRYPTION_KEY')
    if not key:
        key = Fernet.generate_key().decode()
        os.environ['ENCRYPTION_KEY'] = key
    return Fernet(key.encode() if isinstance(key, str) else key)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    tinkoff_token_encrypted = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    def set_token(self, token):
        """Шифрует и сохраняет токен"""
        cipher = get_cipher()
        self.tinkoff_token_encrypted = cipher.encrypt(token.encode()).decode()

    def get_token(self):
        """Расшифровывает токен"""
        if not self.tinkoff_token_encrypted:
            return None
        cipher = get_cipher()
        return cipher.decrypt(self.tinkoff_token_encrypted.encode()).decode()