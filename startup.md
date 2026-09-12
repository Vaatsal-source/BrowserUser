# How to start Dev Privacy Guard

Your project uses Python from its own `.venv` environment. You do not need to activate it manually. The existing Python environment ran successfully in the previous session.

## Start on your Mac

Open Terminal and run:

```bash
cd /Users/aksh-aggarwal/Desktop/Workspace/SIH/Agent
./scripts/start.sh
```

Keep this terminal open. Open the dashboard in your browser:

**http://127.0.0.1:8765**

1. Copy the pairing code printed in Terminal into the dashboard.
2. Create a vault passphrase of at least 12 characters, or unlock your existing vault.
3. Open **Settings** and configure your reasoning API key, model and API base URL. Select **Remote** mode for the Browser Use agent. The screenshot workflow requires a model supporting images and Chat Completions JSON output.
4. Choose **Launch browser** to open the dedicated Chromium window.

Starting the app does not run tests or rebuild the dashboard. If the script reports missing dependencies or a missing dashboard build, run `./scripts/setup.sh` once, then start again. Full installation instructions are in [setup.md](setup.md).

## Open the extension

In the dedicated Chromium window:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select:

   ```text
   /Users/aksh-aggarwal/Desktop/Workspace/SIH/Agent/apps/extension
   ```

4. Pin **Dev Privacy Guard**, open its side panel and enter the terminal pairing code.

If the extension is already installed, just open its side panel and pair. Use this controlled Chromium window for agent tasks.

## Run the portal demo

1. In the dashboard, choose **New task → Prepare portal demo**. It prepares a partial synthetic profile and the starting URL:

   ```text
   http://127.0.0.1:8766/portal.html
   ```

2. Use **Remote** mode and enter this task:

   ```text
   Open this website and complete the application using my selected profile. Fill available details first, ask for missing documents, and stop before final submission.
   ```

3. Keep visual checkpoints and stop-before-submission enabled, then start. The website does not need to be open beforehand.
4. When screenshot approval appears, open dashboard review. Compare the original local image and the redacted outgoing image, add masks if needed, and approve the latest request.
5. When the agent asks for missing information, upload `demo/documents/portal-statement.txt` in **Documents**. Review and confirm the extracted facts.
6. Return to the waiting task, select the newly confirmed records along with the original profile records, and choose **Resume**.
7. Inspect the completed form or review page. Final submission is withheld by default.

The portal is fictional. The original form at `http://127.0.0.1:8766/` also has a deterministic **Demo** mode that needs no API key; that mode does not run the multi-step portal agent.

## Optional voice input

Save a separate OpenAI Whisper key in **Settings → Voice transcription**. Record your instruction, stop, then choose **Transcribe with Whisper**. The original audio is uploaded unredacted to OpenAI. Edit the returned text before starting the task; transcription does not start it automatically. From the extension recording tab, choose **Use transcript** to put it into your task draft.

## Stop and restart

Press **Ctrl+C** in the running terminal to stop the companion. Start it again with the same two commands above. Each restart creates a new pairing code; pair and unlock again. Saved vault records remain in `~/Library/Application Support/Dev Privacy Guard`.

If a port is already in use, stop the previous companion terminal before starting another instance. If Chromium is missing, follow the browser installation step in [setup.md](setup.md).
