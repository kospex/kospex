"""kweb route handlers run in FastAPI's threadpool.

They were `async def` while making blocking SQLite calls, so one slow page
(/repos/ took ~7s) held the event loop and every other request queued behind
it. As plain `def`, FastAPI runs them on worker threads.

That makes any sqlite3 connection opened at import a bug: it may only be used
on the thread that created it. GraphService held one.
"""
import inspect
import threading

import kweb2
import api_routes

# Kept async on purpose: the lifespan hook and the exception handlers, which
# await Starlette's handlers and do no DB work.
ASYNC_ALLOWED = {"lifespan", "custom_404_handler", "custom_http_exception_handler"}


def test_route_handlers_are_sync():
    async_routes = [
        route.endpoint.__name__
        for app_routes in (kweb2.app.routes, api_routes.router.routes)
        for route in app_routes
        if getattr(route, "endpoint", None) is not None
        # FastAPI's own /docs and /openapi.json routes are async; not ours
        and route.endpoint.__module__ in ("kweb2", "api_routes")
        and inspect.iscoroutinefunction(route.endpoint)
        and route.endpoint.__name__ not in ASYNC_ALLOWED
    ]
    assert async_routes == []


def test_graph_service_query_works_off_the_import_thread():
    service = kweb2.graph_service
    errors = []

    def worker():
        try:
            list(service.query.kospex_db.query("SELECT 1"))
        except Exception as exc:  # sqlite3.ProgrammingError when shared
            errors.append(exc)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    assert errors == []
