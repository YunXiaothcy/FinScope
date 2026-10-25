import asyncio

from httpx import ASGITransport, AsyncClient

from app.main import app


def test_health_returns_ok() -> None:
    async def request_health():
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            return await client.get("/api/v1/health")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
