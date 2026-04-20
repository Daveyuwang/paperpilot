DEPTH_CONFIG: dict[str, int] = {
    "quick": 3,
    "standard": 5,
    "deep": 8,
}

CONFIDENCE_THRESHOLD: float = 0.4
MAX_REPLAN: int = 2
SEARCH_CONCURRENCY: int = 3
MAX_PAGE_CHARS: int = 3000
FETCH_TOP_N: int = 3

PLAN_MODEL: str = "claude-sonnet-4-6"
EXECUTE_MODEL: str = "claude-sonnet-4-6"
SYNTHESIZE_MODEL: str = "claude-sonnet-4-6"
