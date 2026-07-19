from __future__ import annotations

from web import register_all as register_p0_routes
from web.current_compat import (
    CURRENT_REQUIRED_SERVICES,
    CURRENT_ROUTE_KEYS,
    P0_ROUTE_CONFLICTS,
    CurrentWebDependencies,
    register_current_routes,
)

from .conftest import RecordingMCP


EXPECTED_CURRENT_ONLY_ROUTES = {
    ("GET", "/.well-known/openid-configuration"),
    ("GET", "/mcp/.well-known/oauth-authorization-server"),
    ("GET", "/mcp/.well-known/openid-configuration"),
    ("GET", "/mcp/.well-known/oauth-protected-resource"),
    ("GET", "/mcp/.well-known/oauth-protected-resource/{resource_path:path}"),
    ("GET", "/mcp/oauth/authorize"),
    ("POST", "/mcp/oauth/token"),
    ("GET", "/dashboard-assets/{path:path}"),
    ("GET", "/dream-hook"),
    ("GET", "/introspection-hook"),
    ("POST", "/api/bucket/{bucket_id}/comments"),
    ("DELETE", "/api/bucket/{bucket_id}/comments/{comment_id}"),
    ("GET", "/api/buckets/light"),
    ("POST", "/api/memories"),
    ("PATCH", "/api/bucket/{bucket_id}"),
    ("POST", "/api/buckets/delete"),
    ("POST", "/api/buckets/bulk-update"),
    ("GET", "/api/moments"),
    ("GET", "/api/todos"),
    ("PATCH", "/api/todos/{todo_id}"),
    ("POST", "/api/todos/{todo_id}/writeback"),
    ("GET", "/api/reminders"),
    ("POST", "/api/reminders"),
    ("PATCH", "/api/reminders/{reminder_id}"),
    ("GET", "/api/darkroom/status"),
    ("POST", "/api/ingest-raw"),
    ("GET", "/api/search-raw"),
    ("POST", "/api/search-raw"),
    ("GET", "/api/edges"),
    ("GET", "/api/domain-taxonomy"),
    ("GET", "/api/portrait-state"),
    ("POST", "/api/portrait-maintain"),
    ("DELETE", "/api/portrait-state/items"),
    ("POST", "/api/portrait-state/items"),
    ("PUT", "/api/portrait-state/items"),
    ("PUT", "/api/portrait-state/stable"),
    ("POST", "/api/portrait-state/stable/lock"),
    ("POST", "/api/portrait-state/stable/rollback"),
    ("POST", "/api/portrait-state/reset"),
    ("GET", "/api/profile-facts"),
    ("PATCH", "/api/profile-facts/{bucket_id}"),
    ("DELETE", "/api/profile-facts/{bucket_id}"),
    ("POST", "/api/profile-fact-proposals"),
    ("POST", "/api/profile-fact-proposals/confirm"),
    ("POST", "/api/anchor-proposals"),
    ("POST", "/api/anchor-proposals/confirm"),
    ("GET", "/api/word-map"),
    ("POST", "/api/word-map/rebuild"),
    ("GET", "/api/word-map/cards"),
    ("GET", "/api/identity-semantics"),
    ("POST", "/api/identity-semantics/rebuild"),
    ("GET", "/api/persona"),
    ("GET", "/api/dreams"),
    ("GET", "/api/dreams/{dream_id}"),
    ("GET", "/api/diffusion-debug"),
    ("GET", "/api/recall-debug"),
    ("GET", "/api/gateway-injections"),
    ("POST", "/api/reflection/run"),
    ("POST", "/api/daily-chat-memory/run"),
    ("POST", "/api/daily-activity-summary/run"),
    ("GET", "/api/daily-chat-memory/pending"),
    ("POST", "/api/daily-chat-memory/confirm"),
    ("GET", "/api/config/effective"),
    ("POST", "/api/backup/export/prepare"),
    ("GET", "/api/backup/export/status"),
    ("GET", "/api/backup/export"),
    ("POST", "/api/backup/restore"),
}


def test_current_only_route_inventory_is_complete_and_exact():
    assert CURRENT_ROUTE_KEYS == EXPECTED_CURRENT_ONLY_ROUTES


def test_registration_registers_every_current_only_route_once():
    mcp = RecordingMCP()

    report = register_current_routes(mcp, CurrentWebDependencies(config={}))

    assert set(mcp.routes) == EXPECTED_CURRENT_ONLY_ROUTES
    assert report.registered == frozenset(EXPECTED_CURRENT_ONLY_ROUTES)
    assert report.required_services == CURRENT_REQUIRED_SERVICES
    assert report.missing_required_services == CURRENT_REQUIRED_SERVICES


def test_registration_composes_after_p0_and_preserves_asset_route_order():
    mcp = RecordingMCP()
    register_p0_routes(mcp)
    p0_routes = tuple(mcp.routes)

    report = register_current_routes(mcp, CurrentWebDependencies(config={}))

    assert set(p0_routes).isdisjoint(report.registered)
    assert report.registered <= set(mcp.routes)
    route_order = tuple(mcp.routes)
    assert route_order.index(("GET", "/dashboard-assets/{name}")) < route_order.index(
        ("GET", "/dashboard-assets/{path:path}")
    )


def test_p0_conflicts_are_documented_and_not_re_registered():
    expected_preserved = {
        ("GET", "/api/buckets"),
        ("GET", "/api/bucket/{bucket_id}"),
        ("DELETE", "/api/bucket/{bucket_id}"),
        ("GET", "/api/search"),
        ("GET", "/api/network"),
        ("GET", "/api/breath-debug"),
        ("GET", "/api/config"),
        ("POST", "/api/config"),
        ("GET", "/api/status"),
        ("GET", "/dashboard"),
    }

    assert expected_preserved <= set(P0_ROUTE_CONFLICTS)
    assert expected_preserved.isdisjoint(CURRENT_ROUTE_KEYS)
    assert all(P0_ROUTE_CONFLICTS[key] for key in expected_preserved)
