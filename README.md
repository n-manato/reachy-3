# reachy-3

Exhibits built around **three Reachy Mini robots** that talk to each other and
to an audience, each with web displays driven from one PC. Every folder is a
self-contained project with its own README, dependencies and deployment files.

| Folder | Exhibit | What the robots do |
|---|---|---|
| [ethics-podcast/](ethics-podcast/) | Ethics Podcast | Hold an autonomous, ongoing conversation about ethics — a confused student and a seasoned professor — with no microphones and no human input (OpenAI Realtime API). |
| [leading-through-ai/](leading-through-ai/) | Leading Through AI | Play an AI C-suite (innovation, people, risk) debating leadership topics, with audience voting. |
| [boma-exhibit/](boma-exhibit/) | BOMA | Perform a fixed welcome sketch, each robot speaking a different language. |

Also here, although it is a tool rather than an exhibit:

| Folder | What it is |
|---|---|
| [remote-connect/](remote-connect/) | Starter kit that lets students write and run their own Reachy Mini app from a Windows PC over the university VPN: one-step setup, SSH tunnel for motion, camera, microphone and speaker, and a browser check page. |

The nine-robot orchestra lives in a separate repository,
[reachy-10](https://github.com/n-manato/reachy-10).

## Layout

One repository, one folder per exhibit. Each folder is run in place: it holds
its own virtual environment and `.env`, and the systemd units under each
`deploy/` directory point at it.

`ethics-podcast/` is also published, with identical history, to the lab's
exhibit repository; `./push-ethics-podcast-to-org.sh` sends new commits there.

Secrets (`.env`), licensed background music and generated speech audio are not
part of this repository; each project's README explains how to supply them.
