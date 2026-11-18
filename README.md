# reachy-3

Exhibits built around **three Reachy Mini robots** that talk to each other and
to an audience, each with web displays driven from one PC. Every folder is a
self-contained project with its own README, dependencies and deployment files.

| Folder | Exhibit | What the robots do |
|---|---|---|
| [ethics-podcast/](ethics-podcast/) | Ethics Podcast | Hold an autonomous, ongoing conversation about ethics — a confused student and a seasoned professor — with no microphones and no human input (OpenAI Realtime API). |
| [leading-through-ai/](leading-through-ai/) | Leading Through AI | Play an AI C-suite (innovation, people, risk) debating leadership topics, with audience voting. |
| [boma-exhibit/](boma-exhibit/) | BOMA | Perform a fixed welcome sketch, each robot speaking a different language. |

The nine-robot orchestra lives in a separate repository,
[reachy-10-orchestra](https://github.com/n-manato/reachy-10-orchestra).

## How this repository is maintained

Each exhibit is developed and deployed from its own working directory, and its
full commit history is merged in here with `git subtree`. `./sync.sh` pulls the
latest commits from the three working directories and pushes the result.

Secrets (`.env`), licensed background music and generated speech audio are not
part of this repository; each project's README explains how to supply them.
