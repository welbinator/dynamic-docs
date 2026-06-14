import secrets
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class Organization(db.Model):
    __tablename__ = "organizations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    invite_code = db.Column(db.String(32), unique=True, nullable=False)
    llm_provider = db.Column(db.String(32), nullable=False, default="gemini")
    llm_model = db.Column(db.String(80), nullable=False, default="gemini-2.5-flash")
    api_key = db.Column(db.String(256), nullable=True)
    retrieval_mode = db.Column(db.String(16), nullable=False, default="complete")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", backref="organization", lazy=True)

    @staticmethod
    def generate_invite_code():
        """Generate a cryptographically random 12-char invite code."""
        return secrets.token_urlsafe(9)  # 9 bytes → 12 base64url chars

    def regenerate_invite_code(self):
        self.invite_code = self.generate_invite_code()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="employee")  # admin | employee
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    @property
    def is_admin(self):
        return self.role == "admin"
