#!/usr/bin/env python3
"""
repo-health.py — Revisa el estado del repo GitHub y reporta novedades.
Uso: python3 /home/user/lina/bin/repo-health.py
"""
import subprocess, json, os, sys
from datetime import datetime

STATE_FILE = "/tmp/repo-health.state"
OWNER = "federico-dominguez"
REPO = "lina"

def gh_api(endpoint):
    url = f"https://api.github.com/repos/{OWNER}/{REPO}/{endpoint}"
    result = subprocess.run(
        ["curl", "-s", url],
        capture_output=True, text=True, timeout=15
    )
    if result.returncode == 0 and result.stdout:
        try:
            return json.loads(result.stdout)
        except:
            return None
    return None

def check_prs():
    data = gh_api("pulls?state=open&per_page=10")
    if not data or not isinstance(data, list):
        return []
    issues = []
    for pr in data:
        status = ""
        if pr.get("mergeable") == False:
            status = "🔴 CONFLICTOS"
        elif pr.get("mergeable") == None:
            status = "⏳ Verificando..."
        else:
            status = "✅ Mergeable"
        issues.append({
            "num": pr["number"],
            "title": pr["title"],
            "status": status,
            "url": pr["html_url"]
        })
    return issues

def check_ci():
    data = gh_api("actions/runs?per_page=3&status=completed")
    if not data or "workflow_runs" not in data:
        return None
    for run in data["workflow_runs"][:3]:
        if run["conclusion"] == "failure":
            return {
                "name": run["name"],
                "branch": run["head_branch"],
                "conclusion": "🔴 FAILURE",
                "url": run["html_url"]
            }
    return None

def check_commits():
    data = gh_api("commits?per_page=3&sha=main")
    if data and isinstance(data, list):
        return [{
            "sha": c["sha"][:7],
            "msg": c["commit"]["message"].split("\n")[0],
            "author": c["commit"]["author"]["name"]
        } for c in data]
    return []

def main():
    prs = check_prs()
    ci_failure = check_ci()
    commits = check_commits()
    
    last_state = ""
    try:
        with open(STATE_FILE) as f:
            last_state = f.read().strip()
    except:
        pass
    
    lines = [f"📊 Repo Health — {datetime.now().strftime('%H:%M')}"]
    
    conflict_prs = [p for p in prs if "CONFLICTOS" in p["status"]]
    if conflict_prs:
        for p in conflict_prs:
            lines.append(f"  🔴 PR #{p['num']}: {p['title']}")
    
    if ci_failure:
        lines.append(f"  🔴 CI: {ci_failure['name']} en {ci_failure['branch']}")
    
    if commits:
        lines.append(f"  📝 Últimos commits:")
        for c in commits[:2]:
            lines.append(f"    • {c['sha']} — {c['msg']}")
    
    report = "\n".join(lines)
    
    if report != last_state:
        with open(STATE_FILE, "w") as f:
            f.write(report)
        print(report)
    else:
        print("♻️ Sin novedades desde el último check")

if __name__ == "__main__":
    main()
