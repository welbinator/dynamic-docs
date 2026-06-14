import os
import re
import json
import time
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, Response, send_from_directory
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv(Path(__file__).parent / ".env")

app = Flask(__name__, static_folder="static")

# Configure Gemini
_client = None
def get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GOOGLE_AI_API_KEY"])
    return _client

KB_DIR = Path(__file__).parent / "raw"
IMAGES_DIR = Path(__file__).parent / "images"
LOGS_DIR = Path(__file__).parent / "query-logs"
LOGS_DIR.mkdir(exist_ok=True)


def save_query_log(query: str, chunks: list[dict], output_html: str, error: str = None):
    """Save query + retrieved chunks + generated output to a daily JSONL log file."""
    ts = time.strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"{ts}.jsonl"
    entry = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "query": query,
        "sources_used": [c["source"] for c in chunks],
        "chunks": [
            {"source": c["source"], "top_score": c.get("_top_score"), "content": c["content"]}
            for c in chunks
        ],
        "output_html": output_html,
        "error": error,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return entry["id"]


def load_all_docs() -> list[dict]:
    """Load complete markdown docs — no retrieval filtering.
    Gemini 2.5 Flash has a 1M token context; both CDLATM manuals are ~340K chars (~85K tokens).
    Nothing ever gets missed. More API tokens per query, but zero retrieval bugs."""
    docs = []
    for md_file in sorted(KB_DIR.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            docs.append({
                "source": md_file.stem.replace("_", " "),
                "filename": md_file.name,
                "content": content,
                "_top_score": 999,
            })
        except Exception:
            pass
    return docs


def search_docs(query: str) -> list[dict]:
    """Search all docs, extract and rank full sections by relevance."""
    STOP = {"the","and","but","for","with","from","onto","into","that","this",
            "are","was","were","have","has","had","not","you","can","will",
            "its","our","your","their","when","then","also","any","all","both",
            "each","more","just","been","being","after","before","over","under",
            "onto","doing","some","using","need","want","how","what","why","loading"}

    raw_words = re.split(r'\s+', query.lower())
    words = []
    for w in raw_words:
        w = w.strip('.,!?:;')
        if not w:
            continue
        # Keep everything — numerics, single letters (model variants like "A", "S"), abbreviations
        if w not in STOP:
            words.append(w)

    if not words:
        return []

    pattern = "|".join(re.escape(w) for w in words)
    regex = re.compile(pattern, re.IGNORECASE)
    heading_re = re.compile(r'^#{1,3}\s')
    # Numbered-step headings like "## 5. Select..." or "## (take SD card out...)" are
    # artifacts from PDF conversion — treat them as body content, not section boundaries
    step_heading_re = re.compile(r'^#{1,3}\s+(\d+\.|\()', re.IGNORECASE)
    table_row_re = re.compile(r'^\s*\|')

    results = []
    for md_file in KB_DIR.glob("*.md"):
        try:
            lines = md_file.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        match_lines = [i for i, l in enumerate(lines) if regex.search(l)]
        if not match_lines:
            continue

        sections_seen = set()
        scored_sections = []

        for ml in match_lines:
            # Find enclosing section boundaries — skip numbered-step headings
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

            # Score: matches in heading = 3pts, body text = 1pt, table row = 0pts
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

            # No line cap — send the complete section. Gemini 2.5 Flash has a 1M token
            # context window; truncating procedures causes missing steps.
            scored_sections.append((score, sec_start, "\n".join(section_lines)))

        if scored_sections:
            scored_sections.sort(key=lambda x: (-x[0], x[1]))
            # No arbitrary section count cap — take everything that scored above 0
            relevant = [text for score, _, text in scored_sections if score > 0]
            # If nothing scored, fall back to top 3 by position
            if not relevant:
                relevant = [text for _, _, text in scored_sections[:3]]
            combined = "\n\n---\n\n".join(relevant)
            results.append({
                "source": md_file.stem.replace("_", " "),
                "filename": md_file.name,
                "content": combined,
                "_top_score": scored_sections[0][0],
            })

    results.sort(key=lambda r: -r.get("_top_score", 0))
    return results


def build_prompt(query: str, chunks: list[dict]) -> str:
    sources_text = ""
    for c in chunks:
        sources_text += f"\n\n--- SOURCE: {c['source']} ---\n{c['content']}"

    return f"""You are a documentation generator for CDLATM field service technicians.

A technician has asked: "{query}"

Below is the relevant content retrieved from the CDLATM manuals. Use ONLY this content to generate your response. Do not add information from general knowledge. If something is not covered in the source material, say so explicitly in a "Knowledge Gap" section.

SOURCE MATERIAL:
{sources_text}

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
When the source material contains a continuous multi-step procedure (even if it has internal sub-headings like "Resetting the Master Password" or "Setting the ATM Model Type"), the ENTIRE procedure must be ONE unbroken .step-list from step 1 to the last step. Rules:
- NEVER close a .step-list and open a new one mid-procedure. That resets the counter.
- Represent internal sub-headings as a <p class="step-phase">Phase name</p> element placed INSIDE the open .step-list, BETWEEN .step-item elements — not as <h2> or <h3> tags.
- The step numbers must run continuously (1, 2, 3 … 26) with no resets.
- A new .step-list is only appropriate when starting a genuinely separate, unrelated procedure.

The page should be comprehensive — a technician should be able to complete the task using only this page.
"""


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/images/<path:filename>")
def serve_image(filename):
    return send_from_directory(str(IMAGES_DIR), filename)


@app.route("/api/generate", methods=["POST"])
def generate():
    data = request.get_json()
    query = data.get("query", "").strip()
    if not query:
        return jsonify({"error": "No query provided"}), 400

    chunks = load_all_docs()
    if not chunks:
        return jsonify({"error": "No relevant content found in the knowledge base"}), 404

    prompt = build_prompt(query, chunks)

    def stream():
        full_output = []
        error_msg = None
        try:
            client = get_client()
            response = client.models.generate_content_stream(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            for chunk in response:
                if chunk.text:
                    full_output.append(chunk.text)
                    yield f"data: {json.dumps({'text': chunk.text})}\n\n"
            yield f"data: {json.dumps({'done': True, 'sources': [c['source'] for c in chunks]})}\n\n"
        except Exception as e:
            error_msg = str(e)
            yield f"data: {json.dumps({'error': error_msg})}\n\n"
        finally:
            save_query_log(query, chunks, "".join(full_output), error_msg)

    return Response(stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/search-only", methods=["POST"])
def search_only():
    """Debug endpoint — see what chunks would be retrieved for a query."""
    data = request.get_json()
    query = data.get("query", "").strip()
    chunks = search_docs(query)
    return jsonify({"chunks": [{"source": c["source"], "top_score": c.get("_top_score"), "preview": c["content"][:300]} for c in chunks]})


@app.route("/api/logs", methods=["GET"])
def list_logs():
    """List all query log files."""
    files = sorted(LOGS_DIR.glob("*.jsonl"), reverse=True)
    return jsonify({"files": [f.name for f in files]})


@app.route("/api/logs/<date>", methods=["GET"])
def get_log(date):
    """Return all entries for a given date (YYYY-MM-DD)."""
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8765, debug=False)
