from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
from models import db, User, Organization

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.is_active and user.check_password(password):
            login_user(user, remember=True)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))
        error = "Invalid email or password."

    return render_template("login.html", error=error)


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    """Admin signup — creates a new organization."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        org_name = request.form.get("org_name", "").strip()

        if not all([name, email, password, org_name]):
            error = "All fields are required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif User.query.filter_by(email=email).first():
            error = "An account with that email already exists."
        else:
            org = Organization(
                name=org_name,
                invite_code=Organization.generate_invite_code(),
            )
            db.session.add(org)
            db.session.flush()  # get org.id before creating user

            user = User(name=name, email=email, role="admin", org_id=org.id)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            login_user(user, remember=True)
            return redirect(url_for("settings.settings_page"))

    return render_template("signup.html", error=error)


@auth_bp.route("/join/<invite_code>", methods=["GET", "POST"])
def join(invite_code):
    """Employee signup via invite link."""
    if current_user.is_authenticated:
        return redirect(url_for("index"))

    org = Organization.query.filter_by(invite_code=invite_code).first()
    if not org:
        return render_template("join.html", error="This invite link is invalid or has been reset.", org=None)

    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not all([name, email, password]):
            error = "All fields are required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif User.query.filter_by(email=email).first():
            error = "An account with that email already exists."
        else:
            user = User(name=name, email=email, role="employee", org_id=org.id)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            login_user(user, remember=True)
            return redirect(url_for("index"))

    return render_template("join.html", error=error, org=org)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
