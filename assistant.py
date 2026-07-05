SCHEMA = """
CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT
);
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER REFERENCES customers(id),
    balance REAL
);
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    amount REAL,
    tx_date TEXT
);
"""

def generate_data(
    conn: sqlite3.Connection,
    n_customers: int = 200,
    n_accounts: int = 300,
    n_transactions: int = 5000,
    seed: int = 42,
) -> None:
    """Seed the DB with realistic synthetic data (German locale, Faker).

    Synthetic data instead of real customer data is standard practice in
    fintech (GDPR); a fixed seed keeps every run and test reproducible.
    """
    import random

    from faker import Faker

    fake = Faker("de_DE")
    Faker.seed(seed)
    rng = random.Random(seed)

    conn.executemany(
        "INSERT INTO customers VALUES (?, ?, ?)",
        [(i, fake.name(), fake.city()) for i in range(1, n_customers + 1)],
    )
    conn.executemany(
        "INSERT INTO accounts VALUES (?, ?, ?)",
        [
            (i, rng.randint(1, n_customers), round(rng.uniform(0, 50_000), 2))
            for i in range(1, n_accounts + 1)
        ],
    )
    conn.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?)",
        [
            (
                i,
                rng.randint(1, n_accounts),
                round(rng.uniform(-2_000, 5_000), 2),
                fake.date_between(start_date="-1y").isoformat(),
            )
            for i in range(1, n_transactions + 1)
        ],
    )
    conn.commit()


def get_connection() -> sqlite3.Connection:
    """Create and seed an in-memory demo database."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    generate_data(conn)
    return conn



#SQL generation
FEW_SHOT_EXAMPLES = """
Example 1
Question: Which customers live in Berlin?
SQL: SELECT name FROM customers WHERE city = 'Berlin'

Example 2
Question: What is the average transaction amount per account?
SQL: SELECT account_id, AVG(amount) AS avg_amount FROM transactions GROUP BY account_id
"""

PROMPT_TEMPLATE = """You are an expert SQL assistant.

Database schema (SQLite):
{schema}

{examples}

Rules:
- Reply with exactly ONE SQLite SELECT statement.
- No explanation, no markdown, no code fences.
{error_feedback}
Question: {question}
SQL:"""


def build_prompt(question: str, error_feedback: str = "") -> str:
    """Assemble the prompt: schema grounding + few-shot examples + rules."""
    feedback = (
        f"- Your previous attempt failed with this error, fix it:\n  {error_feedback}\n"
        if error_feedback
        else ""
    )
    return PROMPT_TEMPLATE.format(
        schema=SCHEMA.strip(),
        examples=FEW_SHOT_EXAMPLES.strip(),
        error_feedback=feedback,
        question=question,
    )


def mock_generate_sql(question: str, error_feedback: str = "") -> str:
    """Offline stand-in for the LLM so the demo runs with no API key."""
    q = question.lower()
    if "balance" in q or "guthaben" in q:
        if "city" in q or "stadt" in q:
            return (
                "SELECT c.city, SUM(a.balance) AS total_balance "
                "FROM customers c JOIN accounts a ON a.customer_id = c.id "
                "GROUP BY c.city ORDER BY total_balance DESC"
            )
        return (
            "SELECT c.name, SUM(a.balance) AS total_balance "
            "FROM customers c JOIN accounts a ON a.customer_id = c.id "
            "GROUP BY c.name ORDER BY total_balance DESC"
        )
    if "transaction" in q or "transaktion" in q:
        return (
            "SELECT c.name, COUNT(t.id) AS tx_count "
            "FROM customers c "
            "JOIN accounts a ON a.customer_id = c.id "
            "JOIN transactions t ON t.account_id = a.id "
            "GROUP BY c.name ORDER BY tx_count DESC"
        )
    if "customer" in q or "kunden" in q:
        return "SELECT id, name, city FROM customers ORDER BY name"
    raise ValueError(f"Mock mode can't handle this question: {question!r}")


def llm_generate_sql(question: str, error_feedback: str = "") -> str:
    """LLM-backed generation via the Anthropic API (needs ANTHROPIC_API_KEY)."""
    import anthropic  # lazy import: mock mode needs no API dependency

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    msg = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=300,
        messages=[{"role": "user", "content": build_prompt(question, error_feedback)}],
    )
    return msg.content[0].text.strip()


GENERATORS = {"mock": mock_generate_sql, "llm": llm_generate_sql}


#Validation — parse to an AST, never execute raw model output
class UnsafeSQLError(ValueError):
    """Raised when generated SQL is not a single read-only SELECT."""


def validate_sql(sql: str) -> str:
    """Parse with sqlglot and require exactly one plain SELECT statement.

    A real parser understands query structure, so tricks that fool
    string/regex filters (comments, casing, stacked statements) don't work.
    """
    try:
        statements = sqlglot.parse(sql, dialect="sqlite")
    except sqlglot.errors.ParseError as e:
        raise UnsafeSQLError(f"Not valid SQL: {e}") from e

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        raise UnsafeSQLError("Exactly one statement is required.")

    (statement,) = statements
    if not isinstance(statement, exp.Select):
        raise UnsafeSQLError(
            f"Only SELECT is allowed, got: {statement.key.upper()}"
        )
    return statement.sql(dialect="sqlite")


#Pipeline with self-correction loop
def ask(
    question: str,
    conn: sqlite3.Connection | None = None,
    mode: str = "mock",
    max_retries: int = 2,
) -> tuple[str, list]:
    """Question in, (sql, rows) out.

    If validation or execution fails, the error message is fed back to the
    generator so the LLM can correct itself (agentic retry loop).
    """
    conn = conn or get_connection()
    generate = GENERATORS[mode]
    error_feedback = ""

    for attempt in range(max_retries + 1):
        try:
            sql = validate_sql(generate(question, error_feedback))
            return sql, conn.execute(sql).fetchall()
        except (UnsafeSQLError, sqlite3.Error) as e:
            error_feedback = str(e)
            if attempt == max_retries or mode == "mock":
                raise  # mock can't self-correct; LLM ran out of retries


DEMO_QUESTIONS = [
    "Show me the total balance per customer",
    "Total balance per city?",
    "How many transactions does each customer have?",
    "List all customers",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("question", nargs="?", help="question in natural language")
    parser.add_argument("--mode", choices=GENERATORS, default="mock")
    args = parser.parse_args()

    conn = get_connection()
    for q in [args.question] if args.question else DEMO_QUESTIONS:
        sql, rows = ask(q, conn, mode=args.mode)
        print(f"\nQ: {q}\nSQL: {sql}")
        for row in rows:
            print("  ", row)


if __name__ == "__main__":
    main()
