# RAG Demo — py-basic-agent

This repo includes a tiny Retrieval-Augmented Generation (RAG) flow wired into the REPL.

## TL;DR
- Put `.md` or `.txt` files into the **knowledge/** folder.
- Run `/rag ingest` in the REPL to index them into pgvector.
- Use `/rag show -q "..."` to see retrieved chunks with sources & scores.
- Use `/rag ask <question>` to get an answer that **must** come from the retrieved context.

## What’s inside this file
- **RAG-DEMO-CODE:** `quartz-8127`  
  (Used by smoke tests to prove retrieval works.)
- **Scope:** This demo indexes Markdown and plaintext files only.
- **Policy:** Answers should be grounded strictly in retrieved context.
  - If the answer isn’t in the context, the assistant should say **“I don’t know.”**
- **Good queries to try:**
  - “What is the RAG demo code?”
  - “Summarize the policy about answers and context.”
  - “Which file contained the demo code?”

## How it works (short)
1. On ingest, files are split into overlapping word chunks and embedded.
2. On query, top-k chunks are retrieved by vector similarity.
3. The LLM receives your **question + retrieved context** and must answer **only** from that context.

## Tips
- Keep chunks reasonably sized (≈ 800 words, 150 overlap in the demo).
- Add sources in filenames; they’re displayed in retrieval panels.
- Re-ingest after updating files to refresh the vector store.

---
