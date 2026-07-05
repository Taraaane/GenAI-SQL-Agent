# GenAI SQL Assistant

An LLM-powered tool that converts natural language questions into executable SQL queries. It ensures security by parsing queries into an Abstract Syntax Tree (AST) before execution and uses a self-correction loop to automatically fix SQL errors. 

All tests and executions run on a GDPR-compliant synthetic dataset generated with Faker.

## 🏗️ Pipeline Flow

```text
User Question
      │
      ▼
LLM SQL Generation (with Few-Shot Prompting)
      │
      ▼
AST Security Validation (sqlglot)
      │ ◄────── (Self-Correction Loop on Error) ──────┐
      ▼                                               │
Database Execution (SQLite + Synthetic Data) ─────────┘
```

## 🚀 Key Features

* **Schema Grounding:** The full database schema is injected into the prompt for accurate SQL generation.
* **AST Guardrails:** Uses `sqlglot` to parse output. Only safe, single `SELECT` statements are allowed to execute, blocking destructive commands.
* **Agentic Self-Correction:** If the SQL fails validation or execution, the exact error is sent back to the LLM to fix and retry.
* **Synthetic Data:** Uses `Faker` to generate reproducible (fixed seed), realistic data in-memory for safe testing without exposing real user data.


## 📦 Project Structure

```text
├── assistant.py          # Core pipeline (LLM generator, AST validation, Data)
├── test_assistant.py     # Pytest suite 
└── README.md             # Documentation
```

## 🧪 Tests

The test suite (`test_assistant.py`) verifies dataset seeding, pipeline correctness, and security blocks (preventing malicious DDL commands like `DROP` or `DELETE` from executing).
