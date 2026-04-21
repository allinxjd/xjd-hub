"""Hub 端到端集成测试 — 完整商业流程."""

import asyncio
import json
import hashlib
import sys

async def run_e2e():
    from hub.db import HubDB
    from hub.server import create_hub_app
    from hub.payment.credits import CreditManager
    from hub.payment.license import LicenseManager
    from hub.signing import SkillSigner, content_hash
    from agent.skills.sandbox_policy import assess_tools_risk, DANGEROUS_TOOLS, SAFE_TOOLS
    from gateway.core.auth import JWTManager, PasswordHasher
    from aiohttp.test_utils import TestServer, TestClient

    passed = 0
    failed = 0

    def check(name, condition, detail=""):
        nonlocal passed, failed
        if condition:
            passed += 1
            print(f"  PASS: {name}")
        else:
            failed += 1
            print(f"  FAIL: {name} — {detail}")

    app = create_hub_app(db_path=":memory:", jwt_secret="test-secret-key-for-e2e")
    async with TestClient(TestServer(app)) as client:
        # ── 1. Register ──
        resp = await client.post("/hub/api/auth/register", json={
            "username": "author1", "email": "a@test.com", "password": "StrongPass123",
        })
        check("Register author", resp.status == 201)
        author = await resp.json()
        author_token = author.get("token", "")

        resp = await client.post("/hub/api/auth/register", json={
            "username": "buyer1", "email": "b@test.com", "password": "BuyerPass456",
        })
        check("Register buyer", resp.status == 201)
        buyer = await resp.json()
        buyer_token = buyer.get("token", "")

        # Duplicate username
        resp = await client.post("/hub/api/auth/register", json={
            "username": "author1", "email": "x@test.com", "password": "Whatever123",
        })
        check("Duplicate username rejected", resp.status == 409)

        # Weak password
        resp = await client.post("/hub/api/auth/register", json={
            "username": "weak", "email": "w@test.com", "password": "short",
        })
        check("Weak password rejected", resp.status == 400)

        # ── 2. Login ──
        resp = await client.post("/hub/api/auth/login", json={
            "username": "author1", "password": "StrongPass123",
        })
        check("Login success", resp.status == 200)

        resp = await client.post("/hub/api/auth/login", json={
            "username": "author1", "password": "wrong",
        })
        check("Wrong password rejected", resp.status == 401)

        # ── 3. Me / Profile ──
        resp = await client.get("/hub/api/auth/me", headers={"Authorization": f"Bearer {author_token}"})
        check("Get profile", resp.status == 200)
        me = await resp.json()
        check("Profile has username", me.get("username") == "author1")

        resp = await client.get("/hub/api/auth/me")
        check("Unauthenticated me rejected", resp.status == 401)

        # ── 4. Publish skill ──
        skill_content = "# Auto Deploy\nDeploy to server automatically.\n\ntools: [web_search, read_file]"
        resp = await client.post("/hub/api/skills", json={
            "name": "Auto Deploy",
            "content": skill_content,
            "description": "Automated deployment skill",
            "tags": ["deploy", "automation"],
            "tools": ["web_search", "read_file"],
            "version": "1.0.0",
            "category": "devops",
            "price": 0,
        }, headers={"Authorization": f"Bearer {author_token}"})
        check("Publish free skill", resp.status == 201)
        pub = await resp.json()
        check("Publish returns slug", "slug" in pub)
        check("Tools risk is low", pub.get("tools_risk") == "low")
        free_slug = pub.get("slug", "")

        # Publish paid skill with dangerous tools
        paid_content = "# DB Manager\nManage databases.\n\ntools: [database_query, run_terminal]"
        resp = await client.post("/hub/api/skills", json={
            "name": "DB Manager Pro",
            "content": paid_content,
            "description": "Database management",
            "tags": ["database"],
            "tools": ["database_query", "run_terminal"],
            "version": "1.0.0",
            "price": 50.0,
        }, headers={"Authorization": f"Bearer {author_token}"})
        check("Publish paid skill", resp.status == 201)
        paid_pub = await resp.json()
        check("Dangerous tools risk is high", paid_pub.get("tools_risk") == "high")
        paid_slug = paid_pub.get("slug", "")

        # Unauthenticated publish
        resp = await client.post("/hub/api/skills", json={"name": "x", "content": "y"})
        check("Unauthenticated publish rejected", resp.status == 401)

        # ── 5. Review (approve) ──
        # Make author1 a reviewer
        db = app["hub_db"]
        await db.execute("UPDATE hub_users SET role = 'admin' WHERE username = 'author1'")
        await db.commit()

        resp = await client.get("/hub/api/reviews", headers={"Authorization": f"Bearer {author_token}"})
        check("List pending reviews", resp.status == 200)
        reviews = await resp.json()
        check("Has pending reviews", len(reviews.get("reviews", [])) >= 2)

        # Approve both skills
        for slug in [free_slug, paid_slug]:
            resp = await client.post(f"/hub/api/skills/{slug}/review", json={
                "action": "approve", "comment": "Looks good",
            }, headers={"Authorization": f"Bearer {author_token}"})
            check(f"Approve {slug}", resp.status == 200)

        # Non-reviewer cannot review
        resp = await client.post(f"/hub/api/skills/{free_slug}/review", json={
            "action": "approve",
        }, headers={"Authorization": f"Bearer {buyer_token}"})
        check("Non-reviewer rejected", resp.status == 403)

        # ── 6. Search ──
        resp = await client.get("/hub/api/skills?q=deploy")
        check("Search skills", resp.status == 200)
        search = await resp.json()
        check("Search finds free skill", any(s["slug"] == free_slug for s in search.get("skills", [])))

        resp = await client.get("/hub/api/skills?category=devops")
        check("Search by category", resp.status == 200)

        # ── 7. Detail ──
        resp = await client.get(f"/hub/api/skills/{free_slug}")
        check("Skill detail", resp.status == 200)
        detail = await resp.json()
        check("Detail excludes content", "content" not in detail.get("skill", {}))

        # ── 8. Download free skill ──
        resp = await client.get(f"/hub/api/skills/{free_slug}/download",
                                headers={"Authorization": f"Bearer {buyer_token}"})
        check("Download free skill", resp.status == 200)
        dl = await resp.json()
        check("Download has content", bool(dl.get("content")))
        check("Download has hash", bool(dl.get("content_hash")))

        # ── 9. Purchase paid skill ──
        # Give buyer credits
        credits = CreditManager(db)
        await credits.add_credits(buyer["user_id"], 100.0, reason="test")

        resp = await client.post(f"/hub/api/skills/{paid_slug}/purchase",
                                 headers={"Authorization": f"Bearer {buyer_token}"})
        check("Purchase paid skill", resp.status == 200)
        purchase = await resp.json()
        check("Purchase returns license", bool(purchase.get("license_key")))
        check("Price paid correct", purchase.get("price_paid") == 50.0)

        # Verify buyer balance deducted
        buyer_balance = await credits.get_balance(buyer["user_id"])
        check("Buyer balance deducted", buyer_balance == 50.0)

        # Verify author received (minus 20% commission)
        author_balance = await credits.get_balance(author["user_id"])
        check("Author received 80%", author_balance == 40.0)

        # Duplicate purchase returns existing license
        resp = await client.post(f"/hub/api/skills/{paid_slug}/purchase",
                                 headers={"Authorization": f"Bearer {buyer_token}"})
        check("Duplicate purchase returns existing", resp.status == 200)
        dup = await resp.json()
        check("Same license key", dup.get("license_key") == purchase.get("license_key"))

        # ── 10. License verification ──
        license_mgr = LicenseManager(app["hub_jwt"])
        license_key = purchase.get("license_key", "")
        check("License valid", license_mgr.verify(license_key) is not None)
        check("License matches skill", license_mgr.verify_for_skill(license_key, paid_slug))
        check("License wrong skill rejected", not license_mgr.verify_for_skill(license_key, "wrong-slug"))
        check("Invalid license rejected", license_mgr.verify("garbage.token.here") is None)

        # ── 11. Download paid skill (with license) ──
        resp = await client.get(f"/hub/api/skills/{paid_slug}/download",
                                headers={"Authorization": f"Bearer {buyer_token}"})
        check("Download paid skill with license", resp.status == 200)

        # ── 12. Rate skill ──
        resp = await client.post(f"/hub/api/skills/{free_slug}/rate", json={"score": 5},
                                 headers={"Authorization": f"Bearer {buyer_token}"})
        check("Rate skill", resp.status == 200)

        resp = await client.post(f"/hub/api/skills/{free_slug}/rate", json={"score": 0},
                                 headers={"Authorization": f"Bearer {buyer_token}"})
        check("Invalid score rejected", resp.status == 400)

        # ── 13. Push version ──
        resp = await client.put(f"/hub/api/skills/{free_slug}/versions", json={
            "content": "# Auto Deploy v2\nUpdated.", "version": "2.0.0",
        }, headers={"Authorization": f"Bearer {author_token}"})
        check("Push new version", resp.status == 200)

        # Non-owner cannot push version
        resp = await client.put(f"/hub/api/skills/{free_slug}/versions", json={
            "content": "hack", "version": "9.9.9",
        }, headers={"Authorization": f"Bearer {buyer_token}"})
        check("Non-owner version push rejected", resp.status == 404)

        # ── 14. Sandbox policy ──
        check("Safe tools risk = low", assess_tools_risk(["web_search", "read_file"]) == "low")
        check("Moderate tools risk = medium", assess_tools_risk(["web_fetch"]) == "medium")
        check("Dangerous tools risk = high", assess_tools_risk(["run_terminal"]) == "high")
        check("Empty tools risk = low", assess_tools_risk([]) == "low")

        # ── 15. Content hash verification ──
        check("Content hash matches", content_hash(skill_content) == hashlib.sha256(skill_content.encode()).hexdigest())

        # ── 16. Security headers ──
        check("X-Content-Type-Options", resp.headers.get("X-Content-Type-Options") == "nosniff")
        check("X-Frame-Options", resp.headers.get("X-Frame-Options") == "DENY")

    # ── Summary ──
    total = passed + failed
    print(f"\n{'='*50}")
    print(f"Results: {passed}/{total} passed, {failed} failed")
    if failed:
        print("COMMERCIAL READINESS: NOT READY")
        return 1
    print("COMMERCIAL READINESS: PASSED")
    return 0


if __name__ == "__main__":
    code = asyncio.run(run_e2e())
    sys.exit(code)
