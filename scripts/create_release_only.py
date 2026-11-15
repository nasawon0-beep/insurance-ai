"""Create-only draft publisher. Never edits/deletes releases, tags or assets."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qsl, quote, urlsplit

MAX_PAGES = 100


class ApiResponse:
    def __init__(self, status, headers, data):
        self.status, self.headers, self.data = status, headers, data


def api(endpoint, method="GET", payload=None, asset=None):
    command = ["gh", "api", "--include", "--method", method, endpoint]
    if payload is not None:
        command += ["--input", "-"]
    if asset is not None:
        command += ["-H", "Content-Type: application/octet-stream", "--input", str(asset)]
    try:
        result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        raise RuntimeError("release API transport failure") from None
    header, separator, body = result.stdout.replace("\r\n", "\n").partition("\n\n")
    status = re.fullmatch(r"HTTP/\S+ (\d{3})(?: .*)?", header.split("\n")[0])
    headers = {}
    for line in header.split("\n")[1:]:
        name, colon, value = line.partition(":")
        if not colon or not re.fullmatch(r"[A-Za-z0-9-]+", name):
            raise RuntimeError("release API invalid headers")
        headers.setdefault(name.lower(), []).append(value.strip())
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise RuntimeError("release API invalid response") from None
    if not separator or not status or not isinstance(data, (dict, list)):
        raise RuntimeError("release API invalid response")
    code = int(status.group(1))
    # Only an explicit, well-formed GitHub 404 proves absence.
    if method == "GET" and code == 404 and result.returncode == 1 and isinstance(data, dict) and data.get("message") == "Not Found":
        return ApiResponse(code, headers, None)
    if result.returncode != 0 or code not in ({200} if method == "GET" else {201}):
        raise RuntimeError("release API refused operation")
    if method == "POST" and not isinstance(data, dict):
        raise RuntimeError("release API invalid creation response")
    return ApiResponse(code, headers, data)


def link_page(url, base):
    if any(ord(c) <= 32 or ord(c) >= 127 for c in url) or "\\" in url or "#" in url:
        raise RuntimeError("invalid pagination URL")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise RuntimeError("invalid pagination URL") from None
    if parsed.scheme != "https" or parsed.netloc != "api.github.com" or parsed.path != "/" + base + "/releases" or parsed.fragment:
        raise RuntimeError("foreign pagination URL")
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise RuntimeError("invalid pagination query") from None
    query = dict(pairs)
    if len(pairs) != 2 or set(query) != {"page", "per_page"} or query["per_page"] != "100" or not re.fullmatch(r"[1-9][0-9]*", query["page"]):
        raise RuntimeError("invalid pagination query")
    page = int(query["page"])
    if page > MAX_PAGES:
        raise RuntimeError("draft inventory pagination limit")
    return page


def next_inventory_page(response, base, page):
    relations = {}
    for value in response.headers.get("link", []):
        for part in value.split(","):
            match = re.fullmatch(r'\s*<([^<>]+)>\s*;\s*rel="(next|prev|first|last)"\s*', part)
            if not match or match[2] in relations:
                raise RuntimeError("malformed or duplicate pagination relation")
            relations[match[2]] = link_page(match[1], base)
    nxt = relations.get("next")
    if nxt is not None:
        if nxt != page + 1 or relations.get("last", nxt) < nxt:
            raise RuntimeError("pagination cycle or missing page")
    elif relations.get("last", page) != page:
        raise RuntimeError("final inventory page unproven")
    if relations.get("prev", page - 1) >= page or relations.get("first", 1) != 1:
        raise RuntimeError("inconsistent pagination relation")
    return nxt, relations.get("last")


def require_absent_release(base, tag):
    if api(base + "/releases/tags/" + tag).data is not None:
        raise RuntimeError("release already exists")
    page, seen, known_last = 1, set(), None
    while True:
        if page in seen or len(seen) >= MAX_PAGES:
            raise RuntimeError("draft inventory pagination cycle or limit")
        seen.add(page)
        response = api(base + "/releases?per_page=100&page=" + str(page))
        releases = response.data
        if not isinstance(releases, list) or any(not isinstance(item, dict) or not isinstance(item.get("tag_name"), str) for item in releases):
            raise RuntimeError("invalid draft inventory")
        if any(item["tag_name"] == tag for item in releases):
            raise RuntimeError("release already exists")
        nxt, declared_last = next_inventory_page(response, base, page)
        if declared_last is not None:
            if known_last is not None and known_last != declared_last:
                raise RuntimeError("inventory changed during pagination")
            known_last = declared_last
        if nxt is None:
            if known_last is not None and known_last != page:
                raise RuntimeError("final inventory page unproven")
            break  # A valid response has no next link, independent of item count.
        page = nxt
    if api(base + "/git/ref/tags/" + tag).data is not None:
        raise RuntimeError("tag already exists")


def remote_name(name):
    """ASCII v1: Unicode -> uXXXX; unsafe ASCII runs -> underscore."""
    value = name.strip(". ")
    # Normalize the installer stem first, then append the exact signature suffix.
    if value.lower().endswith(".sig"):
        signature = remote_name(value[:-4]) + ".sig"
        if len(signature) > 240:
            raise RuntimeError("invalid remote asset name")
        return signature
    value = "".join(c if ord(c) < 128 else "u" + format(ord(c), "04x") for c in value)
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip(".")
    if not value or len(value) > 240 or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*[A-Za-z0-9_-]", value):
        raise RuntimeError("invalid remote asset name")
    return value


def file_digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def plan_assets(bundle):
    paths = sorted([*bundle.glob("msi/*"), *bundle.glob("nsis/*")])
    if not paths or not bundle.resolve().is_relative_to(Path.cwd().resolve()):
        raise RuntimeError("invalid release assets")
    rows, used = [], set()
    for path in paths:
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(bundle.resolve()):
            raise RuntimeError("invalid release asset path")
        name = remote_name(path.name)
        if not re.search(r"\.(exe|msi)(\.sig)?$", name, re.I):
            raise RuntimeError("unsupported release asset suffix")
        if name.casefold() in used:
            raise RuntimeError("remote asset name collision")
        used.add(name.casefold())
        rows.append({"local_path": str(path), "local_name": path.name, "remote_name": name,
                     "size": path.stat().st_size, "sha256": file_digest(path)})
    by_name = {row["remote_name"]: row for row in rows}
    for row in rows:
        name = row["remote_name"]
        signature = name.lower().endswith(".sig")
        pair = name[:-4] if signature else name + ".sig"
        if pair not in by_name:
            raise RuntimeError("missing exact installer/signature pair")
        installer, sig = (by_name[pair], row) if signature else (row, by_name[pair])
        if sig["local_name"][:-4] != installer["local_name"]:
            raise RuntimeError("unrelated local signature pair")
        row["role"] = "signature" if signature else "installer"
        row["paired_remote_name"] = pair
    return rows


def verify_asset(item, row, base):
    if not isinstance(item, dict):
        raise RuntimeError("invalid asset response")
    identity = item.get("id")
    if (type(identity) is not int or identity <= 0 or item.get("name") != row["remote_name"] or
            item.get("state") != "uploaded" or type(item.get("size")) is not int or item["size"] != row["size"] or
            item.get("url") != "https://api.github.com/" + base + "/releases/assets/" + str(identity)):
        raise RuntimeError("asset identity verification failed")
    digest = item.get("digest")
    if digest is not None and digest != "sha256:" + row["sha256"]:
        raise RuntimeError("asset digest verification failed")
    return identity, digest is not None


def require_unchanged(rows):
    for row in rows:
        path = Path(row["local_path"])
        if path.stat().st_size != row["size"] or file_digest(path) != row["sha256"]:
            raise RuntimeError("local asset changed after preflight")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    version = args.version
    repo = os.environ.get("GH_REPO", "")
    sha = os.environ.get("GITHUB_SHA", "")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not re.fullmatch(r"[a-fA-F0-9]{40}", sha):
        raise RuntimeError("invalid release identity")
    if json.loads(Path("desktop/package.json").read_text(encoding="utf-8"))["version"] != version:
        raise RuntimeError("release version mismatch")
    rows = plan_assets(Path("desktop/src-tauri/target/release/bundle"))
    tag, base = "v" + version + "-windows", "repos/" + repo
    manifest = {"schema": 1, "naming_policy": "ascii-v1", "repository": repo, "tag": tag,
                "commit": sha, "verified": False, "assets": rows}
    manifest_path = Path("release-asset-manifest.json")
    with manifest_path.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, ensure_ascii=True, indent=2)
    require_absent_release(base, tag)
    require_unchanged(rows)
    ref = api(base + "/git/refs", "POST", {"ref": "refs/tags/" + tag, "sha": sha}).data
    if not isinstance(ref, dict) or ref.get("ref") != "refs/tags/" + tag or not isinstance(ref.get("object"), dict) or ref["object"].get("sha") != sha:
        raise RuntimeError("unexpected created tag")
    release = api(base + "/releases", "POST", {"tag_name": tag, "target_commitish": sha, "name": "모아둠 v" + version, "draft": True, "prerelease": False, "generate_release_notes": True}).data
    if not isinstance(release, dict):
        raise RuntimeError("unexpected created release")
    release_id = release.get("id")
    if type(release_id) is not int or release_id <= 0 or release.get("tag_name") != tag or release.get("draft") is not True:
        raise RuntimeError("unexpected created release")
    asset_ids = set()
    for row in rows:
        require_unchanged([row])
        uploaded = api("https://uploads.github.com/" + base + "/releases/" + str(release_id) + "/assets?name=" + quote(row["remote_name"], safe=""), "POST", asset=Path(row["local_path"])).data
        identity, digest_verified = verify_asset(uploaded, row, base)
        if identity in asset_ids:
            raise RuntimeError("duplicate asset identity")
        asset_ids.add(identity)
        row.update(asset_id=identity, digest_verified=digest_verified)
    # Bind asset identities to the exact newly-created release readback.
    final = api(base + "/releases/" + str(release_id)).data
    if not isinstance(final, dict) or type(final.get("id")) is not int or final["id"] != release_id or final.get("draft") is not True or final.get("tag_name") != tag or not isinstance(final.get("assets"), list):
        raise RuntimeError("release readback verification failed")
    members = final["assets"]
    if len(members) != len(rows) or any(not isinstance(item, dict) or type(item.get("id")) is not int for item in members) or {item["id"] for item in members} != asset_ids:
        raise RuntimeError("release asset membership mismatch")
    for row in rows:
        item = next(item for item in members if item["id"] == row["asset_id"])
        _, digest_verified = verify_asset(item, row, base)
        row["digest_verified"] = row["digest_verified"] or digest_verified
    require_unchanged(rows)
    manifest.update(verified=True, release_id=release_id)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
    print("Created and read back new draft assets; release-asset-manifest.json records names. No publication.")


if __name__ == "__main__":
    main()
