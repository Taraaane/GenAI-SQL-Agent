import pytest

from assistant import (
    UnsafeSQLError,
    ask,
    build_prompt,
    get_connection,
    mock_generate_sql,
    validate_sql,
)

@pytest.fixture(scope="module")
def conn():
    # module-scoped: generate the synthetic dataset once for all tests
    return get_connection()

def test_dataset_is_seeded(conn):
    assert conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 200
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 300
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 5000


def test_dataset_is_reproducible():
    # fixed seed -> two fresh DBs contain identical data
    a = get_connection().execute("SELECT * FROM customers ORDER BY id").fetchall()
    b = get_connection().execute("SELECT * FROM customers ORDER BY id").fetchall()
    assert a == b


# --- pipeline correctness, verified against independent reference queries ----

def test_balance_per_customer(conn):
    sql, rows = ask("total balance per customer", conn)
    assert "join" in sql.lower()
    # spot-check one customer against a directly computed reference value
    name, total = rows[0]
    expected = conn.execute(
        "SELECT SUM(a.balance) FROM accounts a "
        "JOIN customers c ON a.customer_id = c.id WHERE c.name = ?",
        (name,),
    ).fetchone()[0]
    assert total == pytest.approx(expected)
    #descending by balance
    totals = [r[1] for r in rows]
    assert totals == sorted(totals, reverse=True)


def test_balance_per_city(conn):
    _, rows = ask("total balance per city", conn)
    grand_total = sum(r[1] for r in rows)
    expected = conn.execute(
        "SELECT SUM(balance) FROM accounts a "
        "JOIN customers c ON a.customer_id = c.id"
    ).fetchone()[0]
    assert grand_total == pytest.approx(expected)


def test_transactions_per_customer(conn):
    _, rows = ask("how many transactions per customer?", conn)
    total_tx = sum(r[1] for r in rows)
    expected = conn.execute(
        "SELECT COUNT(*) FROM transactions t "
        "JOIN accounts a ON t.account_id = a.id"
    ).fetchone()[0]
    assert total_tx == expected


def test_unknown_question_raises():
    with pytest.raises(ValueError):
        mock_generate_sql("what's the weather like?")


@pytest.mark.parametrize("bad_sql", [
    "DROP TABLE customers",
    "DELETE FROM accounts",
    "UPDATE accounts SET balance = 0",
    "SELECT * FROM customers; DROP TABLE customers",   
    "INSERT INTO customers VALUES (9,'x','y')",
    "not sql at all",
])
def test_validate_blocks_unsafe_sql(bad_sql):
    with pytest.raises(UnsafeSQLError):
        validate_sql(bad_sql)


def test_validate_allows_select():
    assert "SELECT" in validate_sql("SELECT * FROM customers;").upper()


def test_validate_is_not_fooled_by_casing_or_comments():
    with pytest.raises(UnsafeSQLError):
        validate_sql("dRoP /* just reading, promise */ TABLE customers")

def test_prompt_contains_schema_and_examples():
    prompt = build_prompt("total balance per city")
    assert "CREATE TABLE customers" in prompt   
    assert "Example 1" in prompt               
    assert "total balance per city" in prompt

def test_prompt_includes_error_feedback_on_retry():
    prompt = build_prompt("q", error_feedback="no such column: nam")
    assert "no such column: nam" in prompt     