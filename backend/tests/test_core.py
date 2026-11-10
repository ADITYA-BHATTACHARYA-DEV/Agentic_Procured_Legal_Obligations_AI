from app.services.query_router import heuristic
from app.services.document_processor import chunk_pages, windows
from evaluation.eval_extraction import f1


def test_router_legal():
    assert heuristic("Is the indemnity clause enforceable and what is our liability?") == "legal"


def test_router_obligation():
    assert heuristic("What are the upcoming deadlines and who must deliver?") == "obligation"


def test_router_summary():
    assert heuristic("Give me a summary of this contract") == "summary"


def test_router_general_falls_through():
    assert heuristic("What is the vendor's name?") is None


def test_chunking_keeps_page_numbers():
    chunks = chunk_pages([{"page": 2, "text": "Payment is due in 30 days. " * 80}])
    assert chunks and all(c["page"] == 2 for c in chunks)


def test_windows_include_page_markers():
    assert "[p.1]" in windows([{"page": 1, "text": "hello"}])[0]


def test_f1_perfect_and_partial():
    assert f1(["a", "b"], ["a", "b"]) == 1.0
    assert 0 < f1(["a", "x"], ["a", "b"]) < 1
