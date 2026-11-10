# ResolveRight: One-Page Project Proposal

**Course:** PSIA AI Engineering Cohort #1, Generative AI and Agentic AI Capstone (individual project)
**Student:** Christian Ablao  |  **Date:** 2026/11/03

## Problem
Support agents at a small online retailer (fictional store: **Brightcart**) handle messages such as "my package never came" or "I want to return this". To answer correctly, an agent must find the order, read the right policy pages, apply thresholds and deadlines, and write a polite reply. This is slow and inconsistent, and a wrong refund costs money.

## Solution
**ResolveRight** is a customer-support agent that acts within the store's policy (D50). The customer's message comes in through the chat (or the API). ResolveRight finds the order, checks policy, and **carries out the resolution** the policy allows an agent to approve (refund, store credit, replacement or cancellation up to ₱5,000), replying with a **policy-cited confirmation**. This is a mock: a ledger line and a runtime order change, no real payment system. Cases the policy keeps for a person (safety, fraud, legal, harassment, chargeback, repeated refunds, a request for a human, amounts above ₱5,000, late damage claims) and questions no policy covers are escalated to staff with a structured hand-off, and nothing is executed for them.

## Why generative AI
Customer messages are vague free text. Rules alone cannot extract the intent, spot missing details, combine them with order status, and write a grounded reply. **Off-the-shelf test:** a generic chatbot does not know Brightcart's policies, cannot look up its orders, cannot apply its approval limits and cannot escalate into its workflow. The value comes from the store's own data, tools and constraints.

## Design principle
The LLM understands the customer and explains the decision. **Deterministic code computes eligibility** (30-day window, ₱5,000 agent limit, final-sale items and so on). The LLM cannot overrule it, and an output guardrail checks that the two agree.

## Agentic capabilities
Retrieval-augmented generation over 10 policy documents; five tools (`search_policy`, `lookup_order`, `check_eligibility`, `execute_resolution`, `escalate_to_human`); multi-step planning within a 6-step cap; session memory; clarifying questions; human escalation; safety boundaries.

## Technical plan
Python 3.12; OpenAI mini-class model with native tool calling; framework-light state machine; Qdrant vector store (local mode) with `text-embedding-3-small`; LangGraph state machine; FastAPI API plus Gradio UI; Langfuse tracing; Docker on Render.

## Guardrails and operations
Input layer: length limits, prompt-injection detection, off-topic filtering, PII masking. Output layer: moderation, citation and grounding check, rule-consistency check, schema validation, refusal with escalation. Response and embedding caching with measured before/after latency and cost. Structured JSON logs without PII.

## Evaluation plan
25-question RAG golden set (RAGAS metrics); 23 tool scenarios (24 before D57) including designed failures (tool-selection accuracy, task completion); 19 attack cases plus 7 benign look-alikes (GR-10 "process the refund now" became a normal request in D50) (refusal accuracy, escalation recall, false-positive rate); LLM-as-judge with hand spot-check; three prompt variants compared; a baseline-to-improved retrieval comparison (fixed-size to section-aware chunking); one documented debugged failure.

## Data
All data is synthetic: 10 policy documents, 2 customers (Nora, the demo user, and Milo, whose orders test ownership), 30 orders. No real people or payment data.

## Deliverables and timeline
Public URL, GitHub repository with README and evidence, and a demo video of at most 5 minutes. Week 1 builds the tools, RAG, orchestrator, UI and first deployment; week 2 covers evaluation, guardrails, caching, final deployment and the video.

## Risks
Free-tier hosting cold starts (mitigated by an uptime ping and backup evidence); API cost on a public URL (rate limits, daily budget and a provider spending cap); limited time (protected priority order: deployed app, evaluation, guardrails, caching, video).
