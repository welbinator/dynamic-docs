# CDLATM Docs Project

## Vision
Convert CDLATM PDF training materials into a dynamic documentation system.
Rather than a chatbot that summarizes content, the goal is an AI that generates
a full, structured doc page in response to any question — as if a technical writer
created it specifically for that inquiry.

## Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | PDF Extraction → raw markdown | ⏳ Waiting on PDFs |
| 2 | Chunk by topic + YAML frontmatter | Not started |
| 3 | AI knowledge base (clean chunks for RAG) | Not started |
| 4 | Human-facing docs (tutorials, how-tos, reference) | Not started |
| 5 | Website + Anthropic API for dynamic doc generation | Not started |
| 6 | Video scripts + TTS narration | Not started |

## Directory Structure

```
cdlatm-docs/
  raw/           # Raw extracted markdown, one file per PDF
  chunks/        # Topic-chunked markdown files with YAML frontmatter
  images/        # Embedded images extracted from PDFs
  human-docs/    # Human-facing tutorials, guides, reference pages
  ai-kb/         # Final AI knowledge base (cleaned, indexed chunks)
  README.md      # This file
```

## Dynamic Docs Vision

When deployed on the website:
1. User types a question
2. System retrieves relevant chunks from the knowledge base
3. Anthropic API generates a complete, structured doc page from those chunks
4. Output looks like a real documentation page — title, sections, examples, etc.
5. No chatbot UI — just a doc, created on demand

## Tech Stack (planned)

- **Extraction:** pymupdf / pymupdf4llm
- **KB format:** Markdown with YAML frontmatter
- **RAG (if needed):** ChromaDB or Pinecone
- **Dynamic generation:** Anthropic API (Claude)
- **Website:** TBD
