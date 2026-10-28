"""Refresh citation snapshots without replacing them when Scholar is unavailable."""
import json
import os
from pathlib import Path
import subprocess
import sys
import traceback
from datetime import datetime, timezone

UNAVAILABLE = 75
FETCH_TIMEOUT = 120


def validate_author(author):
    if not isinstance(author, dict) or not author.get("name"):
        raise ValueError("Scholar returned an incomplete author profile")
    for key in ("citedby", "hindex"):
        value = author.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("Scholar returned an invalid " + key)
    publications = author.get("publications")
    if not isinstance(publications, list):
        raise ValueError("Scholar returned an invalid publication list")
    if any(not isinstance(p, dict) or not p.get("author_pub_id") for p in publications):
        raise ValueError("Scholar returned a publication without an ID")
    return author


def is_profile_parse_error(error):
    # The observed failure is a missing HTML element inside scholarly's parser.
    # Do not classify unrelated AttributeErrors in our code as upstream failures.
    return (
        isinstance(error, AttributeError)
        and "'NoneType' object has no attribute" in str(error)
        and any(
            frame.filename.replace("\\", "/").endswith("/scholarly/author_parser.py")
            and frame.name.startswith("_fill_")
            for frame in traceback.extract_tb(error.__traceback__)
        )
    )


def fetch_worker():
    from scholarly import scholarly
    from scholarly._proxy_generator import MaxTriesExceededException
    from requests.exceptions import RequestException

    scholar_id = os.environ.get("GOOGLE_SCHOLAR_ID", "").strip()
    if not scholar_id:
        raise ValueError("GOOGLE_SCHOLAR_ID is missing")
    scholarly.set_timeout(20)
    scholarly.set_retries(1)
    try:
        author = scholarly.search_author_id(scholar_id)
        scholarly.fill(author, sections=["basics", "indices", "counts", "publications"])
    except (MaxTriesExceededException, RequestException) as error:
        print("Google Scholar could not be fetched (" + type(error).__name__ + ").", file=sys.stderr)
        return UNAVAILABLE
    except AttributeError as error:
        if not is_profile_parse_error(error):
            raise
        print("Google Scholar did not provide the expected profile elements.", file=sys.stderr)
        return UNAVAILABLE
    print(json.dumps(validate_author(author), ensure_ascii=False))
    return 0


def report(refreshed, message):
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write("refreshed=" + str(refreshed).lower() + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("### Citation data\n\n" + message + "\n")
    print(message if refreshed else "::warning title=Citation data not refreshed::" + message)


def main():
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--fetch"],
            capture_output=True, text=True, timeout=FETCH_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        report(False, "Google Scholar exceeded the 120-second time limit. The last successful citation data and its update date are unchanged.")
        return
    if result.returncode == UNAVAILABLE:
        report(False, "Google Scholar is unavailable or returned an unexpected profile page. No citation data was updated; the previous snapshot and its update date are unchanged. See the next scheduled run.")
        return
    if result.returncode != 0:
        # Dependency, configuration, and unexpected programming errors remain failures.
        sys.stderr.write(result.stderr)
        raise RuntimeError("Citation collector failed with exit code " + str(result.returncode))
    author = validate_author(json.loads(result.stdout))
    author["updated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    author["publications"] = {p["author_pub_id"]: p for p in author["publications"]}
    snapshots = {
        "gs_data.json": author,
        "gs_data_shieldsio.json": {
            "schemaVersion": 1, "label": "citations", "message": str(author["citedby"]),
        },
    }
    results = Path("results")
    results.mkdir(exist_ok=True)
    for filename, data in snapshots.items():
        temporary = results / (filename + ".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        temporary.replace(results / filename)
    report(True, "Fetched " + str(len(author["publications"])) + " publications and " + str(author["citedby"]) + " citations from Google Scholar.")


if __name__ == "__main__":
    if "--fetch" in sys.argv:
        sys.exit(fetch_worker())
    main()
