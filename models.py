import json
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
    # ── User Notes settings ───────────────────────────────────
    user_notes_enabled = db.Column(db.Boolean, nullable=False, default=True)
    user_notes_require_approval = db.Column(db.Boolean, nullable=False, default=False)
    user_notes_allow_images = db.Column(db.Boolean, nullable=False, default=True)
    # ─────────────────────────────────────────────────────────
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    users = db.relationship("User", backref="organization", lazy=True)
    documents = db.relationship("Document", backref="organization", lazy=True, cascade="all, delete-orphan")
    user_notes = db.relationship("UserNote", backref="organization", lazy=True, cascade="all, delete-orphan")

    @staticmethod
    def generate_invite_code():
        return secrets.token_urlsafe(9)

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


class Document(db.Model):
    __tablename__ = "documents"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    original_name = db.Column(db.String(256), nullable=False)
    stored_name = db.Column(db.String(256), nullable=False)
    file_type = db.Column(db.String(16), nullable=False)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    extracted_text = db.Column(db.Text, nullable=True)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class Favorite(db.Model):
    __tablename__ = "favorites"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    query = db.Column("query", db.String(512), nullable=False, key="search_query")
    title = db.Column(db.String(256), nullable=False, default="")
    html_content = db.Column(db.Text, nullable=False)
    sources_json = db.Column(db.Text, nullable=False, default="[]")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("favorites", lazy=True, cascade="all, delete-orphan"))


class UserNote(db.Model):
    __tablename__ = "user_notes"

    id = db.Column(db.Integer, primary_key=True)
    org_id = db.Column(db.Integer, db.ForeignKey("organizations.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    content = db.Column(db.Text, nullable=False)
    tags_json = db.Column(db.Text, nullable=False, default="[]")   # JSON array of tag strings
    image_filename = db.Column(db.String(256), nullable=True)       # stored filename in uploads/note-images/
    approved = db.Column(db.Boolean, nullable=False, default=True)  # False when pending admin approval
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    author = db.relationship("User", backref=db.backref("notes", lazy=True))
    upvotes = db.relationship("NoteUpvote", backref="note", lazy=True, cascade="all, delete-orphan")

    @property
    def tags(self):
        try:
            return json.loads(self.tags_json) if self.tags_json else []
        except Exception:
            return []

    @property
    def upvote_count(self):
        return len(self.upvotes)

    def is_upvoted_by(self, user_id: int) -> bool:
        return any(u.user_id == user_id for u in self.upvotes)

    def to_dict(self, current_user_id: int) -> dict:
        return {
            "id": self.id,
            "content": self.content,
            "tags": self.tags,
            "image_filename": self.image_filename,
            "author_name": self.author.name,
            "author_id": self.user_id,
            "created_at": self.created_at.strftime("%b %d, %Y"),
            "upvote_count": self.upvote_count,
            "user_upvoted": self.is_upvoted_by(current_user_id),
            "approved": self.approved,
        }


class NoteUpvote(db.Model):
    __tablename__ = "note_upvotes"

    id = db.Column(db.Integer, primary_key=True)
    note_id = db.Column(db.Integer, db.ForeignKey("user_notes.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("note_id", "user_id", name="uq_note_upvote"),
    )
