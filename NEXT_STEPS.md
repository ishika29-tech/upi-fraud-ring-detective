# NEXT_STEPS.md: what your team does now (step by step)

The project already works end to end. Your job: get it running on your laptops, understand it, make it yours, deploy, record, submit.
Hackathon window: **Oct 15, 12:01 AM IST → Oct 18, 11:59 PM IST**.

## Day 1 (Oct 15): setup (both of you)

1. **Register + team.** Go to https://www.wemakedevs.org/hackathons/falkordb → Sign in → **Register now**.
   One of you creates the team in the panel and invites the other. Pick **Track 01: Agents That Act on Connected Data**.
2. **Gemini key.** Go to https://aistudio.google.com/apikey → "Create API key" → copy it. Each of you can make your own (the free daily limits are per key/project).
3. **Install:** Python 3.11 or 3.12 (python.org; on Windows tick "Add Python to PATH"), Git, and Docker Desktop.
   No Docker? Use a free FalkorDB Cloud instance instead (https://app.falkordb.cloud): copy host/port/username/password into `.env`.
4. **GitHub repo.** One of you: github.com → New repository `upi-fraud-ring-detective` (public) → then in the project folder:
   ```bash
   git init
   git add .
   git commit -m "UPI Fraud Ring Detective - initial version"
   git branch -M main
   git remote add origin https://github.com/<you>/upi-fraud-ring-detective.git
   git push -u origin main
   ```
   Add your teammate as a collaborator (repo → Settings → Collaborators). `.env` is in `.gitignore`, so your key is never pushed.
5. **Run it** (README "Quick start"): `docker compose up -d` → `pip install -r requirements.txt` → copy `.env.example` to `.env` and paste the key →
   `python generate_data.py` → `streamlit run app.py`. Click Investigate on each flagged transaction.
6. **Read `LEARN.md` together** (30 min), then run `python agent.py TXN502640` in the terminal and match each printed step to the code in `tools.py`.

## Day 2 (Oct 16): make it yours (split the work)

Each task below is small, so you learn one idea per task. Do them on separate git branches and merge.

**Person A: graph and tools**
- [ ] Add a new fraud pattern in `generate_data.py`, e.g. a **circular flow** (A → B → C → A within an hour). Re-run and see if the agent finds it.
- [ ] Add a new tool in `tools.py`, e.g. `find_circular_flows(account_id)` using `MATCH p=(a:Account {id:$id})-[:PAID*2..5]->(a)`.
      Add it to the `TOOLS` list and to `mcp_server.py`. Watch the agent start using it.
- [ ] Try another FalkorDB algorithm as a tool: `algo.labelPropagation` (community detection) or `algo.betweenness` (who is the bridge between groups).
- [ ] Run `python evaluate.py` after every change (it should still catch the rings with 0 false alarms).

**Person B: agent and UI**
- [ ] Change the `SYSTEM_PROMPT` in `agent.py` (for example, ask for a Hindi summary for the customer) and see how the behaviour changes.
- [ ] UI: add a "Network overview" button that draws the entire fraud ring map, or a filter that shows only PAID edges.
- [ ] UI: add an **analyst approval** step: show the proposed action and only call `take_action` after a "Confirm" button (human-in-the-loop, which judges like).
- [ ] Try the MCP server: `mcp dev mcp_server.py`, then connect it to Claude Desktop (README section). Screenshot it for the submission.

## Day 3 (Oct 17): deploy + demo video

7. **Deploy:**
   - FalkorDB Cloud free instance → put its details in `.env` → `python generate_data.py` (loads data into the cloud).
   - https://share.streamlit.io → New app → your repo → `app.py` → Advanced settings → Secrets (copy from README "Deploy").
   - Open the public URL and run one investigation to check it works.
8. **Demo video (2–3 min):** follow `DEMO_SCRIPT.md`. Record with OBS, Loom or the Windows Game Bar (Win+Alt+R), and upload to YouTube (unlisted is fine).
   Tip: do one practice run first. Use `offline` if your Gemini quota runs low that day.

## Day 4 (Oct 18): blog + submit (before 11:59 PM IST)

9. **Blog post (AirPods side prize):** start from `BLOG_DRAFT.md`, add your own screenshots and what you learned, then publish on Hashnode / dev.to / Medium.
10. **Submit** in the WeMakeDevs panel: GitHub repo link, live Streamlit URL, demo video link, blog link, track = Track 01. Make sure the README screenshot shows.
11. Be ready to explain: use the "Judge Q&A cheat sheet" in `LEARN.md`.

## If something breaks

- `Cannot reach FalkorDB` → Docker Desktop is not running, or `docker compose up -d` was not run. Check http://localhost:3000.
- `429 RESOURCE_EXHAUSTED` → free Gemini quota used up. The app already tries the fallback models. Otherwise use the other teammate's key, or `offline`.
- `ModuleNotFoundError` → activate the venv first (`source .venv/bin/activate` / `.venv\Scripts\activate`), then `pip install -r requirements.txt`.
- Empty dropdown → run `python generate_data.py` again.
