from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import login_required, current_user
from models import db, User, Organization

settings_bp = Blueprint("settings", __name__)

PROVIDER_DEFAULTS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
}


def admin_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


@settings_bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings_page():
    org = current_user.organization
    saved = False
    error = None

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_org":
            org_name = request.form.get("org_name", "").strip()
            if not org_name:
                error = "Organization name cannot be blank."
            else:
                org.name = org_name
                db.session.commit()
                saved = True

        elif action == "save_llm":
            provider = request.form.get("provider", "gemini")
            model = request.form.get("model", "").strip()
            api_key = request.form.get("api_key", "").strip()
            retrieval_mode = request.form.get("retrieval_mode", "complete")

            if provider not in PROVIDER_DEFAULTS:
                error = "Invalid provider selected."
            elif not model:
                error = "Model name cannot be blank."
            else:
                org.llm_provider = provider
                org.llm_model = model
                # Only update api_key if admin typed something new (empty = keep existing)
                if api_key:
                    org.api_key = api_key
                org.retrieval_mode = retrieval_mode
                db.session.commit()
                saved = True

        elif action == "regenerate_invite":
            org.regenerate_invite_code()
            db.session.commit()
            saved = True

        elif action == "remove_user":
            user_id = request.form.get("user_id", type=int)
            if user_id:
                user = User.query.filter_by(id=user_id, org_id=org.id).first()
                if user and user.id != current_user.id:
                    db.session.delete(user)
                    db.session.commit()
                    saved = True

    members = User.query.filter_by(org_id=org.id).order_by(User.created_at).all()
    invite_url = request.host_url.rstrip("/") + url_for("auth.join", invite_code=org.invite_code)

    return render_template(
        "settings.html",
        org=org,
        members=members,
        invite_url=invite_url,
        provider_defaults=PROVIDER_DEFAULTS,
        saved=saved,
        error=error,
    )
