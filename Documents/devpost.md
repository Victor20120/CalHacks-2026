# ExpandRG: Devpost write-up

## Inspiration
Research groups that write their own simulation code lose knowledge constantly. How to build the code, why a setting was changed, which bug was already fixed: it lives in people's heads and leaves when they graduate. New members spend weeks catching up, and the same problems get solved twice.

## What it does
ExpandRG gives each researcher a private AI agent and the group a shared knowledge base, the **Super Brain**.
- Your agent helps you understand the code, set up and run simulations, and diagnose problems.
- Your work stays private until you approve a finding, then it's shared with the group.
- New members get auto-generated **lessons** that connect papers to the code, with equations and diagrams.
- Runs are monitored for problems and compared on interactive graphs.

## How we built it
A web dashboard and a VS Code extension, backed by a server that keeps private and shared data apart. A small local service reads only the folders a researcher allows, so code stays on their computer. CREAO powers the AI agents: one per researcher, plus the group's Super Brain (agent IDs in the [README](../README.md#creao-agents)).

## Challenges we ran into
- Keeping AI responses fast despite a slow hosted agent API.
- Real research code is messy: unusual build files, unlabeled outputs.
- Making sure AI explanations are accurate, by checking them against the code and the papers.
- Balancing sharing with privacy.

## Accomplishments that we're proud of
- It works on our lab's real research code, not a toy example.
- Sharing is always the researcher's choice.
- One-click install, with over 600 automated tests.

## What we learned
In research the bottleneck is knowledge, not compute. AI help is only useful when it's verified and when people trust where their data goes.

## What's next for ExpandRG
Support for computing clusters, smarter onboarding, and piloting it with more research groups.

## Built with
python, fastapi, uvicorn, pydantic, sqlite, react, vite, javascript, typescript, vscode-extension, node.js, creao, plotly, katex, crossref-api, pypdf, pytest, fortran
