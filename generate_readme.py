#!/usr/bin/env python3
"""
Neofetch-style README generator for GitHub profile.
Fetches GitHub stats via API and generates a README with ASCII art.
"""

import json
import os
import requests
from pathlib import Path


def load_ascii_art(filepath: str) -> list[str]:
    """Load ASCII art from file and return as list of lines."""
    with open(filepath, "r", encoding="utf-8") as f:
        return [line.rstrip() for line in f.readlines()]


def load_config(filepath: str) -> dict:
    """Load configuration from JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def fetch_github_stats(username: str, token: str | None = None) -> dict:
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

    # Get lines of code (additions/deletions) from repos
    if repos_resp.status_code == 200:
        repos = repos_resp.json()
        for repo in repos[:10]:  # Limit to avoid rate limiting
            if repo.get("fork"):
                continue
            stats_resp = requests.get(
                f"https://api.github.com/repos/{username}/{repo['name']}/stats/contributors",
                headers=headers,
            )
            if stats_resp.status_code == 200:
                contributors = stats_resp.json()
                if isinstance(contributors, list):
                    for contributor in contributors:
                        if contributor.get("author", {}).get("login") == username:
                            for week in contributor.get("weeks", []):
                                stats["additions"] += week.get("a", 0)
                                stats["deletions"] += week.get("d", 0)

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
    LINE_WIDTH = 50  # Total width for info section

    def make_line(label: str, value: str, is_link: bool = False) -> str:
        """Create a line with dot padding to align to LINE_WIDTH."""
        if is_link:
            # For links, we need to calculate visible length (excluding HTML tags)
            visible_value = value.split(">")[1].split("<")[0] if ">" in value else value
            visible_len = len(label) + 2 + len(visible_value)  # label: value
        else:
            visible_len = len(label) + 2 + len(value)  # label: value

        dots_needed = LINE_WIDTH - visible_len
        dots = "." * max(dots_needed, 3)
        return f"{label}: {dots} {value}"

    def make_section(title: str) -> str:
        """Create a section header that spans LINE_WIDTH."""
        dashes_after = LINE_WIDTH - len(title) - 4  # 4 = "-- " + " "
        return f"-- {title} " + "-" * dashes_after

    lines = []

    # Header
    header = config["header"]
    dashes = "-" * (LINE_WIDTH - len(header) - 1)
    lines.append(f"{header} {dashes}")
    lines.append("")

    # Basic info
    lines.append(make_line("Name", config["name"]))
    lines.append(make_line("Uptime", config["uptime"]))
    lines.append(make_line("Location", config["location"]))
    lines.append("")

    # Languages section
    lines.append(make_section("Languages"))
    lines.append(make_line("Languages.Code", config["languages"]["code"]))
    lines.append(make_line("Languages.Markup", config["languages"]["markup"]))
    lines.append(make_line("Languages.Human", config["languages"]["human"]))
    lines.append("")

    # Stack section
    lines.append(make_section("Stack"))
    lines.append(make_line("Backend", config["stack"]["backend"]))
    lines.append(make_line("Frontend", config["stack"]["frontend"]))
    lines.append(make_line("Database", config["stack"]["database"]))
    lines.append(make_line("Infra", config["stack"]["infra"]))
    lines.append("")

    # Contact section (with clickable links)
    contact = config["contact"]
    lines.append(make_section("Contact"))

    website_link = (
        f'<a href="{contact["website"]["url"]}">{contact["website"]["label"]}</a>'
    )
    email_link = f'<a href="{contact["email"]["url"]}">{contact["email"]["label"]}</a>'
    linkedin_link = (
        f'<a href="{contact["linkedin"]["url"]}">{contact["linkedin"]["label"]}</a>'
    )

    lines.append(make_line("Website", website_link, is_link=True))
    lines.append(make_line("Email", email_link, is_link=True))
    lines.append(make_line("LinkedIn", linkedin_link, is_link=True))
    lines.append("")

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

    return lines


def merge_ascii_and_info(ascii_lines: list[str], info_lines: list[str]) -> list[str]:
    """Merge ASCII art and info lines side by side, centering ASCII vertically."""
    # Find the max width of ASCII art
    ascii_width = max(len(line) for line in ascii_lines) if ascii_lines else 0
    padding = 6  # Space between ASCII and info

    # Calculate vertical centering offset
    ascii_height = len(ascii_lines)
    info_height = len(info_lines)

    if info_height > ascii_height:
        # Center ASCII vertically within info height
        top_padding = (info_height - ascii_height) // 2
        ascii_lines = (
            [""] * top_padding
            + ascii_lines
            + [""] * (info_height - ascii_height - top_padding)
        )
    else:
        # Pad info to match ASCII height
        info_lines = info_lines + [""] * (ascii_height - info_height)

    # Merge side by side
    result = []
    max_lines = max(len(ascii_lines), len(info_lines))

    for i in range(max_lines):
        ascii_part = ascii_lines[i] if i < len(ascii_lines) else ""
        info_part = info_lines[i] if i < len(info_lines) else ""
        result.append(f"{ascii_part:<{ascii_width}}{' ' * padding}{info_part}")

    return result


def generate_readme(
    ascii_file: str, config_file: str, output_file: str, token: str | None = None
) -> None:
    """Generate the README.md file."""
    # Load data
    ascii_lines = load_ascii_art(ascii_file)
    config = load_config(config_file)

    # Fetch GitHub stats
    stats = fetch_github_stats(config["username"], token)

    # Build info lines
    info_lines = build_info_lines(config, stats)

    # Merge ASCII and info
    merged = merge_ascii_and_info(ascii_lines, info_lines)

    # Build README content
    readme_content = "<pre>\n"
    readme_content += "\n".join(merged)
    readme_content += "\n</pre>\n"
    readme_content += "\n---\n\n"
    readme_content += f'<p align="center"> <img src="https://github-readme-stats.vercel.app/api/top-langs/?username={config["username"]}&hide_title=true" alt="languages" /> </p>\n'
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
