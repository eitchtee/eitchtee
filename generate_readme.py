#!/usr/bin/env python3
"""
Neofetch-style README generator for GitHub profile.
Fetches GitHub stats via API and generates a README with ASCII art.
"""

import hashlib
import json
import os
import requests
from datetime import datetime
from dateutil.relativedelta import relativedelta
from pathlib import Path

# Directory for caching LOC data
CACHE_DIR = Path(__file__).parent / "cache"


def calculate_uptime(birthday_str: str) -> str:
    """
    Calculate uptime from a birthday date string (YYYY-MM-DD).
    Returns formatted string like '25 years, 2 months, 24 days'.
    """
    birthday = datetime.strptime(birthday_str, "%Y-%m-%d")
    today = datetime.today()
    diff = relativedelta(today, birthday)

    parts = []
    if diff.years > 0:
        parts.append(f"{diff.years} year{'s' if diff.years != 1 else ''}")
    if diff.months > 0:
        parts.append(f"{diff.months} month{'s' if diff.months != 1 else ''}")
    if diff.days > 0:
        parts.append(f"{diff.days} day{'s' if diff.days != 1 else ''}")

    return ", ".join(parts) if parts else "0 days"


def graphql_request(query: str, variables: dict, token: str) -> dict | None:
    """Make a GraphQL request to GitHub API."""
    headers = {"Authorization": f"bearer {token}"}
    response = requests.post(
        "https://api.github.com/graphql",
        json={"query": query, "variables": variables},
        headers=headers,
    )
    if response.status_code == 200:
        return response.json()
    print(f"GraphQL request failed: {response.status_code} - {response.text}")
    return None


def fetch_repos_with_commits(
    username: str, token: str, cursor: str | None = None
) -> list[dict]:
    """
    Fetch all repos for a user with their commit counts using GraphQL.
    Handles pagination to get all repos.
    """
    query = """
    query ($login: String!, $cursor: String) {
        user(login: $login) {
            id
            repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
                edges {
                    node {
                        nameWithOwner
                        isFork
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history {
                                        totalCount
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }
    """
    variables = {"login": username, "cursor": cursor}
    result = graphql_request(query, variables, token)

    if not result or "data" not in result or not result["data"]["user"]:
        return []

    user_data = result["data"]["user"]
    repos = []

    for edge in user_data["repositories"]["edges"]:
        node = edge["node"]
        commit_count = 0
        if node["defaultBranchRef"] and node["defaultBranchRef"]["target"]:
            commit_count = node["defaultBranchRef"]["target"]["history"]["totalCount"]

        repos.append(
            {
                "nameWithOwner": node["nameWithOwner"],
                "isFork": node["isFork"],
                "commitCount": commit_count,
            }
        )

    # Handle pagination
    if user_data["repositories"]["pageInfo"]["hasNextPage"]:
        next_cursor = user_data["repositories"]["pageInfo"]["endCursor"]
        repos.extend(fetch_repos_with_commits(username, token, next_cursor))

    return repos


def fetch_user_id(username: str, token: str) -> str | None:
    """Fetch the user's GraphQL ID for filtering commits."""
    query = """
    query ($login: String!) {
        user(login: $login) {
            id
        }
    }
    """
    result = graphql_request(query, {"login": username}, token)
    if result and "data" in result and result["data"]["user"]:
        return result["data"]["user"]["id"]
    return None


def fetch_loc_for_repo(
    owner: str,
    repo_name: str,
    user_id: str,
    token: str,
    cursor: str | None = None,
    additions: int = 0,
    deletions: int = 0,
    my_commits: int = 0,
) -> tuple[int, int, int]:
    """
    Fetch LOC for a single repo using GraphQL with pagination.
    Only counts commits authored by the specified user.
    """
    query = """
    query ($owner: String!, $repo: String!, $cursor: String) {
        repository(name: $repo, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            edges {
                                node {
                                    additions
                                    deletions
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }
    """
    variables = {"owner": owner, "repo": repo_name, "cursor": cursor}
    result = graphql_request(query, variables, token)

    if not result or "data" not in result:
        return additions, deletions, my_commits

    repo_data = result["data"]["repository"]
    if not repo_data or not repo_data["defaultBranchRef"]:
        return additions, deletions, my_commits

    history = repo_data["defaultBranchRef"]["target"]["history"]

    for edge in history["edges"]:
        node = edge["node"]
        author = node.get("author", {})
        author_user = author.get("user") if author else None

        if author_user and author_user.get("id") == user_id:
            my_commits += 1
            additions += node["additions"]
            deletions += node["deletions"]

    # Handle pagination
    if history["pageInfo"]["hasNextPage"]:
        next_cursor = history["pageInfo"]["endCursor"]
        return fetch_loc_for_repo(
            owner,
            repo_name,
            user_id,
            token,
            next_cursor,
            additions,
            deletions,
            my_commits,
        )

    return additions, deletions, my_commits


def get_cache_path(username: str) -> Path:
    """Get the cache file path for a user."""
    # Use hashed username for filename (privacy + valid filename)
    filename = hashlib.sha256(username.encode("utf-8")).hexdigest()[:16] + ".json"
    return CACHE_DIR / filename


def load_loc_cache(username: str) -> dict:
    """Load LOC cache from file."""
    cache_path = get_cache_path(username)
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}


def save_loc_cache(username: str, cache: dict) -> None:
    """Save LOC cache to file."""
    CACHE_DIR.mkdir(exist_ok=True)
    cache_path = get_cache_path(username)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def fetch_loc_with_cache(
    username: str, token: str, verbose: bool = False
) -> tuple[int, int]:
    """
    Fetch LOC for all repos with smart caching.
    Only re-fetches repos where commit count has changed.

    Returns: (total_additions, total_deletions)
    """
    if not token:
        if verbose:
            print("  [LOC] No token provided, skipping LOC fetch")
        return 0, 0

    # Get user ID for filtering commits
    user_id = fetch_user_id(username, token)
    if not user_id:
        if verbose:
            print("  [LOC] Could not fetch user ID")
        return 0, 0

    # Fetch all repos with commit counts
    repos = fetch_repos_with_commits(username, token)
    if verbose:
        print(f"  [LOC] Found {len(repos)} repositories")

    # Load existing cache
    cache = load_loc_cache(username)

    cache_hits = 0
    cache_misses = 0
    total_additions = 0
    total_deletions = 0

    for repo in repos:
        name = repo["nameWithOwner"]
        current_commits = repo["commitCount"]

        # Skip forks
        if repo["isFork"]:
            continue

        # Check if cache is valid
        if name in cache and cache[name].get("commitCount") == current_commits:
            # Cache hit - use cached values
            total_additions += cache[name].get("additions", 0)
            total_deletions += cache[name].get("deletions", 0)
            cache_hits += 1
        else:
            # Cache miss - fetch fresh data
            owner, repo_name = name.split("/")
            additions, deletions, my_commits = fetch_loc_for_repo(
                owner, repo_name, user_id, token
            )

            # Update cache
            cache[name] = {
                "commitCount": current_commits,
                "additions": additions,
                "deletions": deletions,
                "myCommits": my_commits,
            }

            total_additions += additions
            total_deletions += deletions
            cache_misses += 1

    # Save updated cache
    save_loc_cache(username, cache)

    if verbose:
        print(f"  [LOC] Cache hits: {cache_hits}, Cache misses: {cache_misses}")

    return total_additions, total_deletions


def load_ascii_art(filepath: str) -> list[str]:
    """Load ASCII art from file and return as list of lines."""
    with open(filepath, "r", encoding="utf-8") as f:
        return [line.rstrip() for line in f.readlines()]


def load_config(filepath: str) -> dict:
    """Load configuration from JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_github_stats(
    username: str, token: str | None = None, verbose: bool = False
) -> dict:
    """Fetch GitHub statistics using the API."""
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"

    stats = {
        "repos": 0,
        "stars": 0,
        "followers": 0,
        "commits": 0,
        "prs": 0,
        "additions": 0,
        "deletions": 0,
    }

    # Get user info (repos, followers)
    user_resp = requests.get(
        f"https://api.github.com/users/{username}", headers=headers
    )
    if user_resp.status_code == 200:
        user_data = user_resp.json()
        stats["repos"] = user_data.get("public_repos", 0)
        stats["followers"] = user_data.get("followers", 0)

    # Get total stars across all repos
    repos_resp = requests.get(
        f"https://api.github.com/users/{username}/repos?per_page=100", headers=headers
    )
    if repos_resp.status_code == 200:
        repos = repos_resp.json()
        stats["stars"] = sum(repo.get("stargazers_count", 0) for repo in repos)

    # Get commit count (search API)
    commits_resp = requests.get(
        f"https://api.github.com/search/commits?q=author:{username}",
        headers={**headers, "Accept": "application/vnd.github.cloak-preview+json"},
    )
    if commits_resp.status_code == 200:
        stats["commits"] = commits_resp.json().get("total_count", 0)

    # Get PR count (search API)
    prs_resp = requests.get(
        f"https://api.github.com/search/issues?q=author:{username}+type:pr",
        headers=headers,
    )
    if prs_resp.status_code == 200:
        stats["prs"] = prs_resp.json().get("total_count", 0)

    # Get lines of code using GraphQL with caching
    if token:
        additions, deletions = fetch_loc_with_cache(username, token, verbose=verbose)
        stats["additions"] = additions
        stats["deletions"] = deletions

    return stats


def format_number(n: int) -> str:
    """Format large numbers with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def build_info_lines(config: dict, stats: dict) -> list[str]:
    """Build the info section lines with proper alignment."""
    CONTENT_WIDTH = 50  # Width for content inside the box
    BOX_WIDTH = CONTENT_WIDTH + 4  # Add 2 spaces on each side

    def make_line(label: str, value: str, is_link: bool = False) -> str:
        """Create a line with dot padding to align to CONTENT_WIDTH."""
        if is_link:
            # For links, we need to calculate visible length (excluding HTML tags)
            visible_value = value.split(">")[1].split("<")[0] if ">" in value else value
            visible_len = len(label) + 3 + len(visible_value)  # "label: " + " " + value
        else:
            visible_len = len(label) + 3 + len(value)  # "label: " + " " + value

        dots_needed = CONTENT_WIDTH - visible_len
        dots = "." * max(dots_needed, 3)
        content = f"{label}: {dots} {value}"
        return f"  {content}  "  # 2 spaces on each side

    def make_section(title: str) -> str:
        """Create a section header that spans CONTENT_WIDTH using ─ character."""
        prefix = f"── {title} "
        dashes_needed = CONTENT_WIDTH - len(prefix)
        content = prefix + "─" * dashes_needed
        return f"  {content}  "  # 2 spaces on each side

    def make_empty() -> str:
        """Create an empty line with proper padding."""
        return "  " + " " * CONTENT_WIDTH + "  "

    lines = []

    # Header line (before the box)
    header = config["header"]
    lines.append(f"$ {header}")

    # Top border of box (heavy style)
    lines.append("┏" + "━" * (BOX_WIDTH - 2) + "┓")

    # Empty line after top border
    lines.append(make_empty())

    # Basic info (only show if config value exists and is non-empty)
    if config.get("host"):
        lines.append(make_line("Host", config["host"]))
    if config.get("kernel"):
        lines.append(make_line("Kernel", config["kernel"]))
    if config.get("birthday"):
        lines.append(make_line("Uptime", calculate_uptime(config["birthday"])))
    if config.get("location"):
        lines.append(make_line("Location", config["location"]))
    lines.append(make_empty())

    # Languages section (only if any language values exist)
    languages = config.get("languages", {})
    lang_lines = []
    if languages.get("code"):
        lang_lines.append(make_line("Languages.Code", languages["code"]))
    if languages.get("markup"):
        lang_lines.append(make_line("Languages.Markup", languages["markup"]))
    if languages.get("human"):
        lang_lines.append(make_line("Languages.Human", languages["human"]))
    if lang_lines:
        lines.append(make_section("Languages"))
        lines.extend(lang_lines)
        lines.append(make_empty())

    # Stack section (only if any stack values exist)
    stack = config.get("stack", {})
    stack_lines = []
    if stack.get("backend"):
        stack_lines.append(make_line("Backend", stack["backend"]))
    if stack.get("frontend"):
        stack_lines.append(make_line("Frontend", stack["frontend"]))
    if stack.get("database"):
        stack_lines.append(make_line("Database", stack["database"]))
    if stack.get("infra"):
        stack_lines.append(make_line("Infra", stack["infra"]))
    if stack_lines:
        lines.append(make_section("Stack"))
        lines.extend(stack_lines)
        lines.append(make_empty())

    # Contact section (only if any contact values exist)
    contact = config.get("contact", {})
    contact_lines = []
    if contact.get("website", {}).get("url") and contact.get("website", {}).get(
        "label"
    ):
        website_link = (
            f'<a href="{contact["website"]["url"]}">{contact["website"]["label"]}</a>'
        )
        contact_lines.append(make_line("Website", website_link, is_link=True))
    if contact.get("email", {}).get("url") and contact.get("email", {}).get("label"):
        email_link = (
            f'<a href="{contact["email"]["url"]}">{contact["email"]["label"]}</a>'
        )
        contact_lines.append(make_line("Email", email_link, is_link=True))
    if contact.get("linkedin", {}).get("url") and contact.get("linkedin", {}).get(
        "label"
    ):
        linkedin_link = (
            f'<a href="{contact["linkedin"]["url"]}">{contact["linkedin"]["label"]}</a>'
        )
        contact_lines.append(make_line("LinkedIn", linkedin_link, is_link=True))
    if contact_lines:
        lines.append(make_section("Contact"))
        lines.extend(contact_lines)
        lines.append(make_empty())

    # GitHub Stats section
    lines.append(make_section("GitHub Stats"))
    lines.append(make_line("Repos", str(stats["repos"])))
    lines.append(make_line("Commits", format_number(stats["commits"])))
    lines.append(make_line("PRs", format_number(stats["prs"])))
    lines.append(make_line("Stars", format_number(stats["stars"])))
    lines.append(make_line("Followers", str(stats["followers"])))
    total_loc = stats["additions"] + stats["deletions"]
    lines.append(
        make_line(
            "Lines of Code",
            f"{format_number(total_loc)} {{ {format_number(stats['additions'])}++, {format_number(stats['deletions'])}-- }}",
        )
    )

    # Empty line before bottom border
    lines.append(make_empty())

    # Bottom border of box (heavy style)
    lines.append("┗" + "━" * (BOX_WIDTH - 2) + "┛")

    return lines


def merge_ascii_and_info(ascii_lines: list[str], info_lines: list[str]) -> list[str]:
    """Merge ASCII art and info lines side by side, centering ASCII vertically."""
    # Find the max width of ASCII art
    ascii_width = max(len(line) for line in ascii_lines) if ascii_lines else 0
    padding = 6  # Space between ASCII and info

    # Calculate vertical centering offset
    ascii_height = len(ascii_lines)
    info_height = len(info_lines)

    # Create fill line (all @) and border line (all =)
    fill_line = "@" * ascii_width
    border_line = "=" * ascii_width

    if info_height > ascii_height:
        # Center ASCII vertically within info height, accounting for 2 border lines
        available_height = info_height - 2  # Reserve 2 lines for borders
        if available_height > ascii_height:
            top_fill = (available_height - ascii_height) // 2
            bottom_fill = available_height - ascii_height - top_fill
        else:
            top_fill = 0
            bottom_fill = 0

        # Build ASCII block: border + fill + art + fill + border
        ascii_lines = (
            [border_line]
            + [fill_line] * top_fill
            + ascii_lines
            + [fill_line] * bottom_fill
            + [border_line]
        )
    else:
        # Just add borders when ASCII is taller than info
        ascii_lines = [border_line] + ascii_lines + [border_line]
        # Pad info to match ASCII height
        info_lines = info_lines + [""] * (len(ascii_lines) - len(info_lines))

    # Merge side by side
    result = []
    max_lines = max(len(ascii_lines), len(info_lines))

    for i in range(max_lines):
        ascii_part = ascii_lines[i] if i < len(ascii_lines) else ""
        info_part = info_lines[i] if i < len(info_lines) else ""
        result.append(f"{ascii_part:<{ascii_width}}{' ' * padding}{info_part}")

    return result


def generate_readme(
    ascii_file: str,
    config_file: str,
    output_file: str,
    token: str | None = None,
    verbose: bool = True,
) -> None:
    """Generate the README.md file."""
    # Load data
    ascii_lines = load_ascii_art(ascii_file)
    config = load_config(config_file)

    # Fetch GitHub stats
    if verbose:
        print("Fetching GitHub stats...")
    stats = fetch_github_stats(config["username"], token, verbose=verbose)

    # Build info lines
    info_lines = build_info_lines(config, stats)

    # Merge ASCII and info
    merged = merge_ascii_and_info(ascii_lines, info_lines)

    # Build README content
    readme_content = "<pre>\n"
    readme_content += "\n".join(merged)
    readme_content += "\n</pre>\n"
    readme_content += "\n---\n\n"
    readme_content += f'<p align="center"> <img src="https://komarev.com/ghpvc/?username={config["username"]}&label=👀" alt="{config["username"]}" /> </p>\n'

    # Write output
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(readme_content)

    print(f"Generated {output_file}")


if __name__ == "__main__":
    script_dir = Path(__file__).parent
    token = os.environ.get("GITHUB_TOKEN")

    generate_readme(
        ascii_file=str(script_dir / "ascii.txt"),
        config_file=str(script_dir / "config.json"),
        output_file=str(script_dir / "README.md"),
        token=token,
    )
