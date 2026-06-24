import os
import re
import json
import time
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_from_directory, redirect, url_for, render_template
from flask_login import LoginManager, login_required, current_user
from dotenv import load_dotenv
from google import genai

load_dotenv(Path(__file__).parent / ".env")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32))
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///dynamic_docs.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ── Database ──────────────────────────────────────────────
from models import db, User, Document, Favorite, UserNote, NoteUpvote
db.init_app(app)

# ── Login manager ─────────────────────────────────────────
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "auth.login"
login_manager.login_message = ""

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ── Blueprints ────────────────────────────────────────────
from auth import auth_bp
from settings import settings_bp
app.register_blueprint(auth_bp)
app.register_blueprint(settings_bp)

# ── Paths ─────────────────────────────────────────────────
UPLOADS_DIR = Path(__file__).parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)
NOTE_IMAGES_DIR = Path(__file__).parent / "uploads" / "note-images"
NOTE_IMAGES_DIR.mkdir(exist_ok=True)
LOGS_DIR = Path(__file__).parent / "query-logs"
LOGS_DIR.mkdir(exist_ok=True)

NOTE_IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
NOTE_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


# ── LLM client cache ──────────────────────────────────────
_gemini_clients = {}

def get_gemini_client(api_key: str):
    if api_key not in _gemini_clients:
        _gemini_clients[api_key] = genai.Client(api_key=api_key)
    return _gemini_clients[api_key]


# ── Retrieval ─────────────────────────────────────────────

def load_all_docs(org_id: int) -> list[dict]:
    """Complete mode — send all org docs, zero retrieval bugs."""
    docs = []
    for doc in Document.query.filter_by(org_id=org_id).order_by(Document.uploaded_at).all():
        if doc.extracted_text:
            docs.append({
                "source": doc.original_name,
                "doc_id": doc.id,
                "content": doc.extracted_text,
                "_top_score": 999,
            })
    return docs


def search_docs(query: str, org_id: int) -> list[dict]:
    """Precision mode — extract and rank relevant sections from org docs."""
    STOP = {"the","and","but","for","with","from","onto","into","that","this",
            "are","was","were","have","has","had","not","you","can","will",
            "its","our","your","their","when","then","also","any","all","both",
            "each","more","just","been","being","after","before","over","under",
            "onto","doing","some","using","need","want","how","what","why","loading"}

    raw_words = re.split(r'\s+', query.lower())
    words = [w.strip('.,!?:;') for w in raw_words if w.strip('.,!?:;') and w.strip('.,!?:;') not in STOP]

    if not words:
        return []

    pattern = "|".join(re.escape(w) for w in words)
    regex = re.compile(pattern, re.IGNORECASE)
    heading_re = re.compile(r'^#{1,3}\s')
    step_heading_re = re.compile(r'^#{1,3}\s+(\d+\.|\()', re.IGNORECASE)
    table_row_re = re.compile(r'^\s*\|')

    results = []
    for doc in Document.query.filter_by(org_id=org_id).all():
        if not doc.extracted_text:
            continue

        lines = doc.extracted_text.splitlines()
        match_lines = [i for i, l in enumerate(lines) if regex.search(l)]
        if not match_lines:
            continue

        sections_seen = set()
        scored_sections = []

        for ml in match_lines:
            sec_start = 0
            for i in range(ml, -1, -1):
                if heading_re.match(lines[i]) and not step_heading_re.match(lines[i]):
                    sec_start = i
                    break
            sec_end = len(lines)
            for i in range(ml + 1, len(lines)):
                if heading_re.match(lines[i]) and not step_heading_re.match(lines[i]) and i > sec_start + 2:
                    sec_end = i
                    break

            key = (sec_start, sec_end)
            if key in sections_seen:
                continue
            sections_seen.add(key)

            section_lines = lines[sec_start:sec_end]
            score = 0
            heading_text = lines[sec_start].lower() if sec_start < len(lines) else ""
            for w in words:
                if w in heading_text:
                    score += 3
                for line in section_lines[1:]:
                    if table_row_re.match(line):
                        continue
                    if w.lower() in line.lower():
                        score += 1
                        break

            scored_sections.append((score, sec_start, "\n".join(section_lines)))

        if scored_sections:
            scored_sections.sort(key=lambda x: (-x[0], x[1]))
            relevant = [text for score, _, text in scored_sections if score > 0]
            if not relevant:
                relevant = [text for _, _, text in scored_sections[:3]]
            combined = "\n\n---\n\n".join(relevant)
            results.append({
                "source": doc.original_name,
                "doc_id": doc.id,
                "content": combined,
                "_top_score": scored_sections[0][0],
            })

    results.sort(key=lambda r: -r.get("_top_score", 0))
    return results


# ── Prompt builder ────────────────────────────────────────

def build_prompt(query: str, chunks: list[dict], notes: list[dict] | None = None) -> str:
    sources_text = ""
    for c in chunks:
        sources_text += f"\n\n--- SOURCE: {c['source']} ---\n{c['content']}"

    notes_section = ""
    if notes:
        notes_text = ""
        for n in notes:
            tags_str = ", ".join(n.get("tags") or []) or "none"
            upvotes = n.get("upvote_count", 0)
            notes_text += (
                f"\n\n--- TECH NOTE (id={n['id']}, author={n['author_name']}, "
                f"upvotes={upvotes}, tags=[{tags_str}]) ---\n{n['content']}"
            )
        notes_section = f"""

---

TECH NOTES FROM FIELD TECHNICIANS:
The following notes were added by technicians in your organization. They are real-world observations, fixes, and tips — not from the official manuals. Use BOTH the tags AND the content when deciding relevance to the query. Some notes may be only loosely related; include only those that genuinely add value.
{notes_text}

If any tech notes are relevant to this query, include them AFTER the main documentation content in this exact HTML structure:

<div class="tech-notes-section">
  <div class="tech-notes-header">
    <span class="tech-notes-icon">📋</span>
    <span>Tech Notes</span>
    <span class="tech-notes-badge">N</span>
  </div>
  <!-- one .tech-note div per relevant note, in order of relevance (most upvoted first if tied) -->
  <div class="tech-note" data-note-id="ID">
    <div class="tech-note-body">NOTE CONTENT HERE</div>
    <div class="tech-note-meta">
      <span class="tech-note-author">AUTHOR NAME</span>
      <span class="tech-note-date">DATE</span>
      <span class="tech-note-tags">TAGS</span>
      <span class="tech-note-upvotes">👍 N</span>
    </div>
  </div>
</div>

Replace N in the badge with the count of relevant notes included. If NO tech notes are relevant, omit the entire .tech-notes-section block — do not include an empty section.
"""

    return f"""You are a documentation generator for field service technicians.

A technician has asked: "{query}"

Below is the relevant content retrieved from the manuals. Use ONLY this content to generate your response. Do not add information from general knowledge. If something is not covered in the source material, say so explicitly in a "Knowledge Gap" section.

SOURCE MATERIAL:
{sources_text}
{notes_section}
---

Generate a complete, professional documentation page in HTML format for this query.

STRICT RULES:
- Only include information that exists in the source material above
- Never fabricate steps, procedures, values, or contacts
- If content is missing, include a clearly marked Knowledge Gap section
- Every major section should note which source it came from

OUTPUT FORMAT — return only the inner content HTML (no <html>, <head>, or <body> tags). Use these CSS classes that are already defined in the page template:
- .doc-header for the title block
- .callout.warn / .callout.danger / .callout.info for callout boxes
- .doc-section for each major section
- .step-list and .step-item for numbered steps
- .data-table for tables
- .img-card for images (use <img src="/images/FILENAME"> for any images referenced in the source)
- .knowledge-gap for missing content sections
- .source-tag for citing sources at the bottom of sections

CRITICAL RULE FOR STEP NUMBERING:
When the source material contains a continuous multi-step procedure (even if it has internal sub-headings), the ENTIRE procedure must be ONE unbroken .step-list from step 1 to the last step. Rules:
- NEVER close a .step-list and open a new one mid-procedure. That resets the counter.
- Represent internal sub-headings as <p class="step-phase">Phase name</p> placed INSIDE the open .step-list, BETWEEN .step-item elements — not as <h2> or <h3> tags.
- Step numbers must run continuously (1, 2, 3 … N) with no resets.
- A new .step-list is only appropriate for a genuinely separate, unrelated procedure.

The page should be comprehensive — a technician should be able to complete the task using only this page.
"""


# ── Logging ───────────────────────────────────────────────

def save_query_log(query, chunks, output_html, error=None, org_id=None):
    ts = time.strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"{ts}.jsonl"
    entry = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "org_id": org_id,
        "query": query,
        "sources_used": [c["source"] for c in chunks],
        "chunks": [{"source": c["source"], "top_score": c.get("_top_score"), "content": c["content"]} for c in chunks],
        "output_html": output_html,
        "error": error,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry["id"]


# ── Streaming LLM ─────────────────────────────────────────

def stream_llm(prompt: str, provider: str, model: str, api_key: str):
    """Yield SSE data chunks from the configured LLM provider."""
    if provider == "gemini":
        client = get_gemini_client(api_key)
        response = client.models.generate_content_stream(model=model, contents=prompt)
        for chunk in response:
            if chunk.text:
                yield chunk.text
    elif provider in ("openai", "anthropic"):
        raise NotImplementedError(f"{provider.title()} integration coming soon. Switch to Gemini in Settings to generate docs now.")
    else:
        raise ValueError(f"Unknown provider: {provider}")


# ── Routes ────────────────────────────────────────────────

@app.route("/")
@login_required
def index():
    org = current_user.organization if current_user.is_authenticated else None
    return render_template(
        "index.html",
        notes_enabled=org.user_notes_enabled if org else False,
        is_admin=current_user.is_admin if current_user.is_authenticated else False,
    )


IMAGES_DIR = Path(__file__).parent / "images"

@app.route("/images/<path:filename>")
@login_required
def serve_image(filename):
    return send_from_directory(str(IMAGES_DIR), filename)


@app.route("/api/generate", methods=["POST"])
@login_required
def generate():
    data = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    org = current_user.organization

    # Require API key
    if not org.api_key:
        return jsonify({"error": "No API key configured. Your admin needs to add one in Settings."}), 400

    # Check docs exist
    doc_count = Document.query.filter_by(org_id=org.id).count()
    if doc_count == 0:
        return jsonify({"error": "No documents uploaded yet. Your admin needs to upload knowledge base documents in Settings."}), 404

    # Retrieval
    if org.retrieval_mode == "precision":
        chunks = search_docs(query, org.id)
    else:
        chunks = load_all_docs(org.id)

    if not chunks:
        return jsonify({"error": "No relevant content found in the knowledge base."}), 404

    # Fetch approved org notes (if feature enabled)
    notes_for_prompt = None
    if org.user_notes_enabled:
        org_notes = (
            UserNote.query
            .filter_by(org_id=org.id, approved=True)
            .order_by(UserNote.created_at.desc())
            .all()
        )
        if org_notes:
            notes_for_prompt = [n.to_dict(current_user.id) for n in org_notes]

    prompt = build_prompt(query, chunks, notes=notes_for_prompt)

    def stream():
        full_output = []
        error_msg = None
        try:
            for text in stream_llm(prompt, org.llm_provider, org.llm_model, org.api_key):
                full_output.append(text)
                yield f"data: {json.dumps({'text': text})}\n\n"
            yield f"data: {json.dumps({'done': True, 'sources': [c['source'] for c in chunks]})}\n\n"
        except NotImplementedError as e:
            error_msg = str(e)
            yield f"data: {json.dumps({'error': error_msg})}\n\n"
        except Exception as e:
            error_msg = str(e)
            yield f"data: {json.dumps({'error': error_msg})}\n\n"
        finally:
            save_query_log(query, chunks, "".join(full_output), error_msg, org_id=org.id)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/me")
@login_required
def me():
    return jsonify({
        "id": current_user.id,
        "name": current_user.name,
        "email": current_user.email,
        "role": current_user.role,
        "org": current_user.organization.name,
    })


@app.route("/api/logs")
@login_required
def list_logs():
    files = sorted(LOGS_DIR.glob("*.jsonl"), reverse=True)
    return jsonify({"files": [f.name for f in files]})


@app.route("/api/logs/<date>")
@login_required
def get_log(date):
    log_file = LOGS_DIR / f"{date}.jsonl"
    if not log_file.exists():
        return jsonify({"error": "No log for that date"}), 404
    entries = []
    for line in log_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except Exception:
                pass
    return jsonify({"date": date, "count": len(entries), "entries": entries})


# ── Favorites ─────────────────────────────────────────────

@app.route("/api/favorites", methods=["GET"])
@login_required
def list_favorites():
    favs = (db.session.query(Favorite)
            .filter_by(user_id=current_user.id)
            .order_by(Favorite.created_at.desc())
            .all())
    return jsonify([{
        "id": f.id,
        "query": f.search_query,
        "title": f.title,
        "sources": json.loads(f.sources_json),
        "created_at": f.created_at.strftime("%b %d, %Y"),
    } for f in favs])


@app.route("/api/favorites", methods=["POST"])
@login_required
def save_favorite():
    data = request.get_json()
    query = (data.get("query") or "").strip()
    html_content = (data.get("html_content") or "").strip()
    sources = data.get("sources") or []
    title = (data.get("title") or query)[:256]

    if not query or not html_content:
        return jsonify({"error": "query and html_content are required"}), 400

    # Deduplicate — same user + same query keeps only the newest
    existing = db.session.query(Favorite).filter_by(user_id=current_user.id, search_query=query).first()
    if existing:
        existing.html_content = html_content
        existing.title = title
        existing.sources_json = json.dumps(sources)
        existing.created_at = __import__("datetime").datetime.utcnow()
        db.session.commit()
        return jsonify({"id": existing.id, "updated": True})

    fav = Favorite(
        user_id=current_user.id,
        search_query=query,
        title=title,
        html_content=html_content,
        sources_json=json.dumps(sources),
    )
    db.session.add(fav)
    db.session.commit()
    return jsonify({"id": fav.id, "updated": False}), 201


@app.route("/api/favorites/<int:fav_id>", methods=["GET"])
@login_required
def get_favorite(fav_id):
    fav = db.session.query(Favorite).filter_by(id=fav_id, user_id=current_user.id).first_or_404()
    return jsonify({
        "id": fav.id,
        "query": fav.search_query,
        "title": fav.title,
        "html_content": fav.html_content,
        "sources": json.loads(fav.sources_json),
        "created_at": fav.created_at.strftime("%b %d, %Y"),
    })


@app.route("/api/favorites/<int:fav_id>", methods=["DELETE"])
@login_required
def delete_favorite(fav_id):
    fav = db.session.query(Favorite).filter_by(id=fav_id, user_id=current_user.id).first_or_404()
    db.session.delete(fav)
    db.session.commit()
    return jsonify({"deleted": True})


# ── User Notes ────────────────────────────────────────────

@app.route("/api/notes", methods=["GET"])
@login_required
def list_notes():
    """Return all approved notes for the org (plus pending ones authored by the current user)."""
    org = current_user.organization
    if not org.user_notes_enabled:
        return jsonify([])
    notes = (
        UserNote.query
        .filter_by(org_id=org.id)
        .order_by(UserNote.created_at.desc())
        .all()
    )
    result = []
    for n in notes:
        if n.approved or n.user_id == current_user.id or current_user.is_admin:
            d = n.to_dict(current_user.id)
            result.append(d)
    return jsonify(result)


@app.route("/api/notes", methods=["POST"])
@login_required
def create_note():
    org = current_user.organization
    if not org.user_notes_enabled:
        return jsonify({"error": "User notes are disabled for this organization."}), 403

    content = (request.form.get("content") or "").strip()
    tags_raw = (request.form.get("tags") or "").strip()
    if not content:
        return jsonify({"error": "Note content is required."}), 400

    # Parse tags: comma-separated string → clean list
    tags = [t.strip().lstrip("#") for t in tags_raw.split(",") if t.strip()]

    # Image upload
    image_filename = None
    img_file = request.files.get("image")
    if img_file and img_file.filename:
        if not org.user_notes_allow_images:
            return jsonify({"error": "Image attachments are disabled for this organization."}), 403
        ext = img_file.filename.rsplit(".", 1)[-1].lower() if "." in img_file.filename else ""
        if ext not in NOTE_IMAGE_EXTENSIONS:
            return jsonify({"error": f"Unsupported image type .{ext}. Allowed: jpg, jpeg, png, gif, webp"}), 400
        img_bytes = img_file.read()
        if len(img_bytes) > NOTE_IMAGE_MAX_BYTES:
            return jsonify({"error": "Image exceeds 10 MB limit."}), 400
        image_filename = f"{uuid.uuid4().hex}.{ext}"
        (NOTE_IMAGES_DIR / image_filename).write_bytes(img_bytes)

    # Auto-approve unless org requires approval
    approved = not org.user_notes_require_approval

    note = UserNote(
        org_id=org.id,
        user_id=current_user.id,
        content=content,
        tags_json=json.dumps(tags),
        image_filename=image_filename,
        approved=approved,
    )
    db.session.add(note)
    db.session.commit()
    return jsonify({**note.to_dict(current_user.id), "pending_approval": not approved}), 201


@app.route("/api/notes/<int:note_id>", methods=["DELETE"])
@login_required
def delete_note(note_id):
    note = UserNote.query.filter_by(id=note_id, org_id=current_user.org_id).first_or_404()
    # Only author or admin can delete
    if note.user_id != current_user.id and not current_user.is_admin:
        return jsonify({"error": "Not authorized."}), 403
    # Clean up image file
    if note.image_filename:
        img_path = NOTE_IMAGES_DIR / note.image_filename
        if img_path.exists():
            img_path.unlink()
    db.session.delete(note)
    db.session.commit()
    return jsonify({"deleted": True})


@app.route("/api/notes/<int:note_id>/upvote", methods=["POST"])
@login_required
def upvote_note(note_id):
    note = UserNote.query.filter_by(id=note_id, org_id=current_user.org_id, approved=True).first_or_404()
    existing = NoteUpvote.query.filter_by(note_id=note_id, user_id=current_user.id).first()
    if existing:
        # Toggle off
        db.session.delete(existing)
        db.session.commit()
        return jsonify({"upvoted": False, "upvote_count": note.upvote_count})
    upvote = NoteUpvote(note_id=note_id, user_id=current_user.id)
    db.session.add(upvote)
    db.session.commit()
    return jsonify({"upvoted": True, "upvote_count": note.upvote_count})


@app.route("/api/notes/<int:note_id>/approve", methods=["POST"])
@login_required
def approve_note(note_id):
    if not current_user.is_admin:
        return jsonify({"error": "Admin only."}), 403
    note = UserNote.query.filter_by(id=note_id, org_id=current_user.org_id).first_or_404()
    note.approved = True
    db.session.commit()
    return jsonify({"approved": True})


@app.route("/uploads/note-images/<path:filename>")
@login_required
def serve_note_image(filename):
    return send_from_directory(str(NOTE_IMAGES_DIR), filename)


# ── Init ──────────────────────────────────────────────────

with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, debug=False)
