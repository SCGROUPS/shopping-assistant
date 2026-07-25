"""Operator console contract.

The console is the surface through which a business changes itself, so the
things worth pinning are the ones that would let it change the wrong thing:
who may act, whether an edit survives, and whether the record agrees with the
database.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.admin.auth import DEMO_BOOTSTRAP_KEY, ROLES, Principal, hash_key, new_salt, verify_key
from app.common.runtime_config import SPECS, ConfigError, validate
from app.main import app


@pytest.fixture
async def admin():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-API-Key": DEMO_BOOTSTRAP_KEY},
    ) as api:
        yield api


async def test_admin_endpoints_reject_an_unauthenticated_caller(client):
    """The old control was a request header any caller could set."""
    for method, path in (
        ("get", "/api/v1/admin/experiences"),
        ("get", "/api/v1/admin/settings"),
        ("get", "/api/v1/admin/audit"),
        ("get", "/api/v1/analytics/funnel"),
    ):
        response = await getattr(client, method)(path)
        assert response.status_code == 401, f"{path} answered an anonymous caller"


async def test_a_wrong_key_is_rejected(client):
    response = await client.get(
        "/api/v1/admin/settings", headers={"X-API-Key": "vk_not-a-real-key"}
    )
    assert response.status_code == 401


async def test_bootstrap_key_authenticates_as_an_administrator(admin):
    response = await admin.get("/api/v1/admin/me")
    assert response.status_code == 200
    assert response.json()["role"] == "admin"


async def test_roles_grant_strictly_what_they_should():
    """A role gaining an unintended capability is a silent privilege change."""
    assert ROLES["analyst"] == {"read"}
    analyst = Principal(id="x", email="a@b.c", name="A", role="analyst")
    assert analyst.can("read")
    assert not analyst.can("catalog:write")
    assert not analyst.can("configure")

    merchandiser = Principal(id="x", email="m@b.c", name="M", role="merchandiser")
    assert merchandiser.can("merchandise")
    # A merchandiser runs campaigns; editing and publishing inventory is a
    # different job with a different blast radius.
    assert not merchandiser.can("catalog:write")
    assert not merchandiser.can("catalog:publish")
    assert not merchandiser.can("operators:manage")

    catalog_manager = Principal(id="x", email="c@b.c", name="C", role="catalog_manager")
    assert catalog_manager.can("catalog:publish")
    assert not catalog_manager.can("operators:manage")

    assert Principal(id="x", email="r@b.c", name="R", role="admin").can("operators:manage")


async def test_an_unknown_role_grants_nothing():
    assert not Principal(id="x", email="x@y.z", name="X", role="root").can("read")


def test_a_key_is_verifiable_only_against_its_own_salt():
    salt = new_salt()
    digest = hash_key("vk_secret", salt)
    assert verify_key("vk_secret", salt, digest)
    assert not verify_key("vk_other", salt, digest)
    assert not verify_key("vk_secret", new_salt(), digest)


async def test_the_import_endpoint_requires_a_credential(client):
    files = {"file": ("catalog.json", b"[]", "application/json")}
    assert (await client.post("/api/v1/admin/imports", files=files)).status_code == 401
    # The header that used to be the whole control is now just a header.
    spoofed = await client.post(
        "/api/v1/admin/imports", files=files, headers={"X-Admin-Role": "catalog_manager"}
    )
    assert spoofed.status_code == 401


async def test_the_import_endpoint_reports_what_is_wrong(admin):
    files = {"file": ("catalog.json", b'[{"slug": "a"}]', "application/json")}
    response = await admin.post("/api/v1/admin/imports", files=files)
    assert response.status_code == 202
    body = response.json()
    assert body["valid_count"] == 0
    assert "title" in body["errors"][0]["error"]


async def test_malformed_json_is_refused_rather_than_crashing(admin):
    files = {"file": ("catalog.json", b"{not json", "application/json")}
    assert (await admin.post("/api/v1/admin/imports", files=files)).status_code == 422


async def test_settings_expose_value_default_and_whether_it_was_changed(admin):
    response = await admin.get("/api/v1/admin/settings")
    assert response.status_code == 200
    settings = {row["key"]: row for row in response.json()["settings"]}
    assert set(settings) == set(SPECS)
    weights = settings["search_weights"]
    assert weights["value"] == weights["default"]
    assert weights["overridden"] is False


# Configuration a person can edit at runtime is configuration a person can
# break at runtime, so the validators are the guardrail worth pinning.
@pytest.mark.parametrize(
    "key,value,reason",
    [
        ("search_weights", {"relevance": 1.4}, "a weight above 1"),
        ("search_weights", {"relevance": 0.5, "quality": 0.4}, "too few weights"),
        ("search_weights", dict.fromkeys("abcdefg", 0.9), "weights summing far above 1"),
        ("search_weights", {"relevance": "high"}, "a non-numeric weight"),
        ("category_take_rates", {"Cruise": 0.9}, "an implausible take rate"),
        ("max_merchandising_boost", 9.0, "a boost ceiling that swamps relevance"),
        ("recommendation_mmr_lambda", -0.1, "a negative lambda"),
        ("assistant_policy", 5, "policy that is not text"),
    ],
)
def test_invalid_configuration_is_refused(key, value, reason):
    with pytest.raises(ConfigError):
        validate(key, value)


def test_valid_configuration_is_accepted():
    cleaned = validate(
        "search_weights",
        {
            "relevance": 0.4,
            "preference_fit": 0.2,
            "availability_fit": 0.15,
            "price_fit": 0.15,
            "quality": 0.1,
        },
    )
    assert cleaned["relevance"] == 0.4
    assert validate("max_merchandising_boost", 1.25) == 1.25


def test_an_unknown_setting_cannot_be_written():
    with pytest.raises(ConfigError):
        validate("search_weight_relevance", 0.9)
