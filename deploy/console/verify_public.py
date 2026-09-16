"""Read-only checks of the deployed domains; no login or application writes."""
import http.client
import json


def get(host, path="/", *, tls=True):
    connection = (http.client.HTTPSConnection if tls else http.client.HTTPConnection)(host, timeout=15)
    try:
        connection.request("GET", path, headers={"User-Agent": "AICompanyDeploymentVerification/1.0"})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read(1024 * 1024)
    finally:
        connection.close()


if __name__ == "__main__":
    status, headers, body = get("hyungwon.cloud")
    assert status == 200 and b"AI Company" in body, "console HTTPS page unavailable"
    assert "no-store" in headers.get("Cache-Control", ""), "console cache policy changed"
    assert get("hyungwon.cloud", "/api/projects")[0] == 401, "unauthenticated data access allowed"
    assert json.loads(get("hyungwon.cloud", "/api/session")[2]) == {"authenticated": False, "login_method": "password"}
    status, headers, _ = get("hyungwon.cloud", tls=False)
    assert status in (301, 308) and headers["Location"] == "https://hyungwon.cloud/"
    status, headers, _ = get("talenta-edward.life")
    assert status == 307 and headers["Location"] == "/login?next=%2F", "couple app route changed"
    print(json.dumps({"result": "PASS", "scope": "deployed public endpoints only; no candidate code or master UI claim",
                      "console_https": 200, "unauthenticated_api": 401, "http_redirect": True,
                      "couple_https": 307, "application_writes": 0}))
