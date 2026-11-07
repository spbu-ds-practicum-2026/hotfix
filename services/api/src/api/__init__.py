from api.contests import router as contests_router

try:
    from api.submissions import router as submissions_router
    __all__ = ["contests_router", "submissions_router"]
except ImportError:
    __all__ = ["contests_router"]
