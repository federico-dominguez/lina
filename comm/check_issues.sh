#!/bin/bash
cd /home/fede/lina
echo "=== Issues OPEN ==="
gh issue list -L 10 --json number,title,labels,state --jq '.[] | "#(.number) - (.title) [(.labels | map(.name) | join(","))]"'
echo ""
echo "=== PRs OPEN ==="
gh pr list -L 5 --json number,title,headRefName,state --jq '.[] | "#(.number) - (.title) ((.headRefName)) [(.state)]"'