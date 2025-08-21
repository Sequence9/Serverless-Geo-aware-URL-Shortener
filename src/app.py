# src/app.py
import os
import boto3

# ---- Configuration ----
# Lambda@Edge runs in us-east-1 and env vars are not reliable there
# Keep the table name constant and region us-east-1
TABLE_NAME = "url-shortener"
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL")  # used for LocalStack in tests/CI

# Use resource for native (de-typed) DynamoDB items.
_dynamodb = boto3.resource(
    "dynamodb",
    region_name=AWS_REGION,
    endpoint_url=AWS_ENDPOINT_URL,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
)
_table = _dynamodb.Table(TABLE_NAME)


def _cf_headers(d: dict):
    """CloudFront header format: lower-case keys, list of {key,value}."""
    return {k.lower(): [{"key": k.title(), "value": v}] for k, v in d.items()}


def _redirect(url: str, ttl: int = 300):
    return {
        "status": "302",
        "statusDescription": "Found",
        "headers": _cf_headers({
            "Location": url,
            "Cache-Control": f"max-age={ttl}",
        }),
    }


def _not_found(ttl: int = 60, body: str | None = None):
    resp = {
        "status": "404",
        "statusDescription": "Not Found",
        "headers": _cf_headers({"Cache-Control": f"max-age={ttl}"}),
    }
    if body:
        resp["body"] = body
    return resp


def lambda_handler(event, _context):
    """
    Handles **origin-request** events from CloudFront.
    - Extracts the short_id from the URI.
    - Reads country from CloudFront-Viewer-Country header.
    - Looks up the destination in DynamoDB.
    - Returns a 302 redirect (or 404 if not found).
    """
    request = event["Records"][0]["cf"]["request"]
    headers = request.get("headers", {})
    uri = request.get("uri", "/")

    # Ignore favicon
    if uri.endswith("favicon.ico"):
        return {"status": "204", "statusDescription": "No Content"}

    # "/" -> no short_id
    short_id = uri.lstrip("/")
    if not short_id:
        return _not_found(body="No short id")

    # Country header is lower-cased by CF in the event
    country = ""
    if "cloudfront-viewer-country" in headers and headers["cloudfront-viewer-country"]:
        country = headers["cloudfront-viewer-country"][0].get("value", "").upper()

    try:
        resp = _table.get_item(Key={"short_id": short_id})
        item = resp.get("Item")
        if not item:
            return _not_found(body=f'Short URL for "{short_id}" not found.')

        destinations = item.get("destinations", {}) or {}

        # choose country-specific first, then default
        url = destinations.get(country) or destinations.get("default")
        if not url:
            return _not_found(body=f'No destination for "{short_id}"')

        return _redirect(url)

    except Exception as e:
        # Edge logs end up in the regional replicas—keep it brief but useful
        print(f"[ERROR] short_id={short_id} err={e}")
        return {
            "status": "500",
            "statusDescription": "Internal Server Error",
            "body": "An internal error occurred. Please check function logs.",
        }
