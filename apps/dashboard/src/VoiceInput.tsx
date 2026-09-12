import { useEffect, useRef, useState } from "react";
import { Mic, Square, Trash2, Upload, LoaderCircle } from "lucide-react";
import { api } from "./api";

/** Audio stays in memory until the user explicitly chooses Transcribe. */
export function VoiceInput({
  onTranscript,
}: {
  onTranscript: (text: string) => void;
}) {
  const [recording, setRecording] = useState(false);
  const [starting, setStarting] = useState(false);
  const [audio, setAudio] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const live = useRef(true);
  const stop = () => {
    if (recorder.current?.state === "recording") recorder.current.stop();
    stream.current?.getTracks().forEach((track) => track.stop());
    stream.current = null;
    if (timer.current) clearTimeout(timer.current);
  };
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      stop();
    };
  }, []);
  useEffect(() => {
    if (!audio) {
      setAudioUrl("");
      return;
    }
    const url = URL.createObjectURL(audio);
    setAudioUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [audio]);
  async function record() {
    if (starting || recording || busy) return;
    setStarting(true);
    setError("");
    setNote("");
    setAudio(null);
    try {
      if (
        !navigator.mediaDevices?.getUserMedia ||
        typeof MediaRecorder === "undefined"
      )
        throw new Error(
          "Microphone recording is unavailable in this browser. You can type your task instead.",
        );
      const media = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!live.current) {
        media.getTracks().forEach((t) => t.stop());
        return;
      }
      stream.current = media;
      const mime = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find(
        (type) => MediaRecorder.isTypeSupported(type),
      );
      const rec = new MediaRecorder(
        media,
        mime ? { mimeType: mime } : undefined,
      );
      const chunks: Blob[] = [];
      recorder.current = rec;
      rec.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      rec.onerror = () => {
        stop();
        if (live.current)
          setError("Recording failed. Please try again or type your task.");
      };
      rec.onstop = () => {
        media.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: rec.mimeType || "audio/webm" });
        chunks.length = 0;
        rec.ondataavailable = null;
        rec.onstop = null;
        rec.onerror = null;
        recorder.current = null;
        if (!live.current) return;
        setRecording(false);
        if (blob.size > 10 * 1024 * 1024)
          setError("Recording is too large. Record a shorter task.");
        else if (blob.size) {
          setAudio(blob);
          setNote("Recording is local. Listen or discard before sending.");
        }
      };
      rec.start();
      setRecording(true);
      timer.current = setTimeout(() => {
        stop();
        if (live.current) setNote("Stopped at the 60-second recording limit.");
      }, 60_000);
    } catch (e) {
      stop();
      setRecording(false);
      setError((e as Error).message);
    } finally {
      if (live.current) setStarting(false);
    }
  }
  async function transcribe() {
    if (!audio) return;
    setBusy(true);
    setError("");
    try {
      const result = await api<{ text: string }>("/audio/transcribe", {
        method: "POST",
        body: audio,
        headers: { "Content-Type": audio.type || "audio/webm" },
      });
      if (!live.current) return;
      onTranscript(result.text);
      setAudio(null);
      setNote(
        "Transcript added to your task. Review and edit it before starting. No task has started.",
      );
    } catch (e) {
      if (live.current) setError((e as Error).message);
    } finally {
      if (live.current) setBusy(false);
    }
  }
  return (
    <div className="voice-input">
      <div className="voice-toolbar">
        <button
          type="button"
          className={"button secondary small " + (recording ? "recording" : "")}
          disabled={busy || starting}
          onClick={() => (recording ? stop() : void record())}
        >
          {recording ? <Square size={14} /> : <Mic size={15} />}
          {starting
            ? "Requesting microphone…"
            : recording
              ? "Stop recording"
              : audio
                ? "Record again"
                : "Record task"}
        </button>
        {recording && (
          <span className="voice-state" role="status">
            Recording locally · up to 60 seconds
          </span>
        )}
        {audio && (
          <>
            <button
              type="button"
              className="button secondary small"
              disabled={busy}
              onClick={() => {
                setAudio(null);
                setNote("Recording discarded.");
              }}
            >
              <Trash2 size={14} />
              Discard
            </button>
            <button
              type="button"
              className="button secondary small"
              disabled={busy}
              onClick={() => void transcribe()}
            >
              {busy ? (
                <LoaderCircle size={14} className="spin" />
              ) : (
                <Upload size={14} />
              )}{" "}
              {busy ? "Transcribing…" : "Transcribe with Whisper"}
            </button>
          </>
        )}
      </div>
      {audioUrl && (
        <audio
          controls
          src={audioUrl}
          preload="metadata"
          aria-label="Listen to local recording"
        />
      )}
      <p className="voice-disclosure">
        Audio is sent unredacted to OpenAI only when you choose Transcribe.
        Whisper uses the separate key in Settings.
      </p>
      {note && (
        <p className="voice-note" role="status">
          {note}
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
