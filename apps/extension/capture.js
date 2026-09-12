"use strict";
const $ = (id) => document.getElementById(id);
let recorder = null,
  media = null,
  recording = null,
  audioUrl = "",
  limit = null,
  busy = false,
  starting = false,
  discardOnStop = false;
const sourceTab =
  Number(new URLSearchParams(location.search).get("source_tab")) || null;
function note(value) {
  $("capture-note").textContent = value;
}
function error(value) {
  $("capture-error").textContent = value;
  $("capture-error").hidden = !value;
}
function refresh() {
  const running = recorder?.state === "recording";
  $("record").hidden = running;
  $("stop-recording").hidden = !running;
  $("record").disabled = busy || starting;
  $("record").textContent = starting
    ? "Requesting microphone…"
    : "Start recording";
  $("discard").hidden = !recording;
  $("discard").disabled = busy;
  $("transcribe").disabled = busy || !recording || running;
  $("transcribe").textContent = busy
    ? "Transcribing…"
    : "Transcribe with Whisper";
  $("recording-playback").hidden = !recording;
}
function clearAudio() {
  recording = null;
  if (audioUrl) URL.revokeObjectURL(audioUrl);
  audioUrl = "";
  $("recording-playback").pause();
  $("recording-playback").removeAttribute("src");
  $("recording-playback").load();
}
function stopRecording() {
  if (recorder?.state === "recording") recorder.stop();
  media?.getTracks().forEach((t) => t.stop());
  media = null;
  if (limit) clearTimeout(limit);
}
$("record").addEventListener("click", async () => {
  if (starting || busy || recorder?.state === "recording") return;
  starting = true;
  refresh();
  error("");
  clearAudio();
  discardOnStop = false;
  try {
    if (
      !navigator.mediaDevices?.getUserMedia ||
      typeof MediaRecorder === "undefined"
    )
      throw new Error(
        "Microphone recording is unavailable. Type your task in the extension instead.",
      );
    media = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(
      (type) => MediaRecorder.isTypeSupported(type),
    );
    const rec = new MediaRecorder(media, mime ? { mimeType: mime } : undefined),
      chunks = [];
    recorder = rec;
    rec.ondataavailable = (event) => {
      if (event.data.size) chunks.push(event.data);
    };
    rec.onstop = () => {
      media?.getTracks().forEach((t) => t.stop());
      media = null;
      if (!discardOnStop) {
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        if (blob.size > 10 * 1024 * 1024)
          error("Recording is too large. Record a shorter task.");
        else if (blob.size) {
          recording = blob;
          audioUrl = URL.createObjectURL(blob);
          $("recording-playback").src = audioUrl;
          note(
            "Recording saved in this tab only. Listen, discard, or explicitly send it for transcription.",
          );
        }
      }
      chunks.length = 0;
      rec.ondataavailable = null;
      rec.onstop = null;
      rec.onerror = null;
      recorder = null;
      refresh();
    };
    rec.onerror = () => {
      discardOnStop = true;
      stopRecording();
      error("Recording failed. Try again or type your task.");
      refresh();
    };
    rec.start();
    note("Recording locally… Stop when you finish. Maximum 60 seconds.");
    refresh();
    limit = setTimeout(stopRecording, 60_000);
  } catch (e) {
    stopRecording();
    error(e.message);
  } finally {
    starting = false;
    refresh();
  }
});
$("stop-recording").addEventListener("click", stopRecording);
$("discard").addEventListener("click", () => {
  clearAudio();
  note("Recording discarded. Nothing was sent.");
  refresh();
});
$("transcribe").addEventListener("click", async () => {
  if (!recording || busy) return;
  error("");
  busy = true;
  refresh();
  try {
    const saved = await chrome.storage.session.get("dpg_token");
    if (!saved.dpg_token)
      throw new Error("Pair the extension with your companion first.");
    const response = await fetch(
      "http://127.0.0.1:8765/api/v1/audio/transcribe",
      {
        method: "POST",
        headers: {
          Authorization: "Bearer " + saved.dpg_token,
          "X-Guard-Origin": chrome.runtime.getURL("").replace(/\/$/, ""),
          "Content-Type": recording.type || "audio/webm",
        },
        body: recording,
      },
    );
    const result = await response.json();
    if (!response.ok)
      throw new Error(
        typeof result.detail === "string"
          ? result.detail
          : "Transcription failed. Check the Whisper key in Settings.",
      );
    $("transcript").value = String(result.text || "");
    $("transcript-section").hidden = false;
    clearAudio();
    note(
      "Whisper returned a transcript. Review it below; no task has started.",
    );
  } catch (e) {
    error(e.message);
  } finally {
    busy = false;
    refresh();
  }
});
$("open-panel").addEventListener("click", async () => {
  try {
    const window = await chrome.windows.getCurrent();
    await chrome.sidePanel.open({ windowId: window.id });
  } catch (e) {
    error(e.message);
  }
});
$("use-transcript").addEventListener("click", async () => {
  error("");
  try {
    const text = $("transcript").value.trim();
    if (!text)
      throw new Error(
        "The transcript is empty. Record again or type your task.",
      );
    if (text.length > 5000)
      throw new Error(
        "Shorten the transcript to 5,000 characters before using it.",
      );
    const result = await chrome.runtime.sendMessage({
      type: "dpg-transcript",
      text,
    });
    if (!result?.received)
      throw new Error(
        result?.error ||
          "Open the task side panel, then choose Use transcript again.",
      );
    note(
      "Transcript placed in the side panel task draft. Review your settings there, then choose Start task when ready.",
    );
    if (Number.isInteger(sourceTab) && sourceTab > 0) {
      try {
        await chrome.tabs.update(sourceTab, { active: true });
      } catch {
        note(
          "Transcript delivered. The original tab is no longer available; select a website in the side panel before starting.",
        );
      }
    }
  } catch (e) {
    error(
      e.message?.includes("Receiving end")
        ? "Open the task side panel, then choose Use transcript again. Your transcript is still here."
        : e.message,
    );
  }
});
$("capture-settings").addEventListener("click", () =>
  chrome.tabs.create({ url: "http://127.0.0.1:8765/#page=settings" }),
);
window.addEventListener("pagehide", () => {
  discardOnStop = true;
  stopRecording();
  clearAudio();
});
refresh();
