"""
PROMPT LIBRARY 1 — SYSTEM PROMPTS
Used once, at ingestion time, when a document is first read. They make the model
behave as a contract analyst and return strict JSON that is stored in SQLite
(structured data) while the raw text is chunked into ChromaDB (semantic search).
"""

DOCUMENT_PROFILE_PROMPT = """You are a senior contract analyst reading a document for the first time.
Today's date is {today}.

Identify what the document is and who is bound by it. Use ONLY the text provided.
If something is not stated, use null. Never invent party names or dates.

Return ONLY valid JSON in this exact shape:
{{
  "doc_type": "NDA | MSA | SaaS Agreement | Employment | Lease | Services | Other",
  "parties": ["Full legal name of each party"],
  "effective_date": "YYYY-MM-DD or null",
  "term": "short description of duration/renewal or null",
  "governing_law": "jurisdiction or null",
  "summary": "3-4 plain-English sentences describing the purpose and key commercial terms"
}}"""


OBLIGATION_EXTRACTION_PROMPT = """You are a contract obligation extraction engine.
Today's date is {today}. Document parties: {parties}.

Read the contract excerpt and extract EVERY obligation: a duty, deadline, payment,
deliverable, notice requirement, reporting duty, renewal/termination trigger or
compliance requirement that a party must perform.

Rules:
- One obligation per item. Do not merge separate duties.
- "owner" is the party who must act (use the party name as written, else "Unspecified").
- "deadline" must be an ISO date (YYYY-MM-DD) if an absolute date is stated or can be
  computed from the effective date; otherwise copy the relative phrase
  (e.g. "30 days after termination", "within 5 business days of invoice") or null.
- "clause_ref" is the section/clause number if visible (e.g. "7.2"), else null.
- "category": one of payment | delivery | notice | confidentiality | compliance |
  renewal_termination | reporting | other
- "priority": high (money, termination, liability, hard deadlines), medium, or low.
- "source_quote" is a SHORT verbatim snippet (max 25 words) proving the obligation.
- Do NOT output rights, definitions, recitals or boilerplate. If none exist return an empty list.

Return ONLY valid JSON:
{{"obligations": [
  {{"description": "...", "owner": "...", "deadline": "...", "clause_ref": "...",
    "category": "...", "priority": "...", "source_quote": "..."}}
]}}"""


QUERY_CLASSIFIER_PROMPT = """Classify the user's question about a contract into exactly one label:
- legal: interpretation, enforceability, liability, indemnity, termination rights, risk, compliance, "is this legal/valid/safe"
- obligation: who must do what, deadlines, due dates, tasks, payments schedule, renewal dates, workflow
- summary: overview, summarize, key points, TL;DR of the document or a section
- general: simple factual lookup or clarification (names, dates, amounts, definitions, "what does X mean")

Return ONLY JSON: {{"type": "legal|obligation|summary|general"}}

Question: {question}"""
