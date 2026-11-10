const SAMPLE_RATE = 16000;
const MIN_SIGNAL_RMS = 0.0005;
const MESSAGES = {
  consent_required: "Confirm that this person agrees to enrol their voice.",
  invalid_name: "Enter a display name of up to 40 letters, spaces, periods, apostrophes or hyphens.",
  duplicate_name: "A profile with that name already exists.",
  profile_limit: "The voice profile limit has been reached.",
  sample_unreadable: "This recording could not be read. Make a new take.",
  sample_unusable: "This clip needs more clear speech. Read the whole line and try again.",
  sample_duplicate: "This sounds like a repeated take. Record this sample again.",
  samples_inconsistent: "The takes sound too different. Record the same person speaking alone.",
  duplicate_voice: "These recordings closely match an existing voice profile.",
  engine_busy: "The voice engine is busy. Wait a moment and try saving again.",
  engine_unavailable: "Check that the Sentient Voice Engine app is running, then try saving again.",
  integration_reloaded: "Voice Recognition is reloading. Wait a moment and try saving again.",
  storage_error: "Home Assistant could not save the profile. Try again.",
};

function encodeWav(samples) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, value) => {
    for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i));
  };
  text(0, "RIFF");
  view.setUint32(4, buffer.byteLength - 8, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
  }
  return new Uint8Array(buffer);
}

function encodeBase64(bytes) {
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 8192) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 8192));
  }
  return btoa(binary);
}

function strongestChannel(buffer, maxFrames) {
  let strongest;
  let level = -1;
  for (let channel = 0; channel < buffer.numberOfChannels; channel++) {
    const samples = buffer.getChannelData(channel).subarray(0, maxFrames);
    let sum = 0;
    let squares = 0;
    for (const sample of samples) {
      sum += sample;
      squares += sample * sample;
    }
    const mean = sum / samples.length;
    const rms = Math.sqrt(Math.max(0, squares / samples.length - mean * mean));
    if (rms > level) {
      strongest = samples;
      level = rms;
    }
  }
  return { samples: strongest, level };
}

class SentientVoiceEnrollment extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.takes = [null, null, null];
    this.session = null;
    this.saving = false;
  }

  set hass(value) {
    this.homeAssistant = value;
  }

  set panel(value) {
    this.settings = value?.config;
    this.update();
  }

  connectedCallback() {
    if (!this.shadowRoot.firstChild) {
      this.shadowRoot.innerHTML = `
        <style>
          :host { display: block; min-height: 100%; color: var(--primary-text-color); }
          main { max-width: 650px; margin: auto; padding: 24px 16px 40px; }
          a { color: var(--primary-color); }
          h1 { font-size: 1.65rem; }
          p { line-height: 1.5; }
          form { display: grid; gap: 16px; }
          label { display: grid; gap: 6px; font-weight: 500; }
          label.consent { display: flex; align-items: center; }
          input, button { font: inherit; }
          input[type=text] { padding: 10px; border: 1px solid var(--divider-color);
            border-radius: 8px; color: inherit; background: var(--card-background-color); }
          .sample { padding: 14px; border: 1px solid var(--divider-color); border-radius: 12px;
            background: var(--card-background-color); }
          .sample strong { display: block; }
          .sample p { margin: 7px 0 12px; }
          .controls { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
          audio { width: min(100%, 270px); height: 34px; }
          button { cursor: pointer; padding: 8px 14px; border: 1px solid var(--divider-color);
            border-radius: 9px; color: inherit; background: var(--card-background-color); }
          button:disabled { opacity: .55; cursor: default; }
          button[type=submit] { width: fit-content; background: var(--primary-color);
            color: var(--text-primary-color, white); }
          [data-error] { color: var(--error-color); }
          [hidden] { display: none !important; }
        </style>
        <main>
          <a href="/config/integrations">Back to integrations</a>
          <h1>Record a voice profile</h1>
          <p>Read one line per take in your normal speaking voice. Aim for 4–8 seconds of clear speech.
             You can listen back and retake any clip. Audio is sent to your Home Assistant when you save.</p>
          <p class="availability" hidden></p>
          <form>
            <label>Display name <input name="name" type="text" maxlength="40" required></label>
            <div class="sample" data-index="0"><strong>Sample 1</strong><p></p><div class="controls">
              <button type="button">Record</button><span></span><audio controls hidden></audio>
            </div></div>
            <div class="sample" data-index="1"><strong>Sample 2</strong><p></p><div class="controls">
              <button type="button">Record</button><span></span><audio controls hidden></audio>
            </div></div>
            <div class="sample" data-index="2"><strong>Sample 3</strong><p></p><div class="controls">
              <button type="button">Record</button><span></span><audio controls hidden></audio>
            </div></div>
            <label class="consent"><input name="consent" type="checkbox" required>
              This person agrees to enrol their voice.</label>
            <button type="submit">Save voice profile</button>
            <p role="status" class="status"></p>
          </form>
        </main>`;
      this.form = this.shadowRoot.querySelector("form");
      this.rows = [...this.shadowRoot.querySelectorAll(".sample")];
      this.rows.forEach((row, index) => {
        row.querySelector("button").addEventListener("click", () => {
          if (this.session?.index === index) this.stop();
          else this.record(index);
        });
      });
      this.form.addEventListener("submit", (event) => {
        event.preventDefault();
        this.save();
      });
    }
    this.update();
  }

  disconnectedCallback() {
    if (this.session) this.release(this.session);
    this.clearTakes();
  }

  get unavailable() {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      return "Microphone recording needs Home Assistant over HTTPS. Open your HTTPS address in a browser, or use the WAV upload form.";
    }
    if (!window.MediaRecorder || !window.OfflineAudioContext) {
      return "This browser cannot record here. Open Home Assistant in another browser or use the WAV upload form.";
    }
    return "";
  }

  update() {
    if (!this.form) return;
    const unavailable = this.shadowRoot.querySelector(".availability");
    unavailable.textContent = this.unavailable;
    unavailable.hidden = !this.unavailable;
    this.rows.forEach((row, index) => {
      const active = this.session?.index === index;
      const take = this.takes[index];
      const button = row.querySelector("button");
      const audio = row.querySelector("audio");
      row.querySelector("p").textContent = this.settings?.prompts?.[index] ?? "";
      button.textContent = active ? "Stop" : take ? "Retake" : "Record";
      button.disabled = !!this.unavailable || !this.settings || this.saving
        || (!!this.session && (!active || this.session.phase !== "recording"));
      row.querySelector("span").textContent = active
        ? ({ waiting: "Waiting for microphone…", recording: "Recording…",
             stopping: "Finishing…", processing: "Checking audio…" })[this.session.phase]
        : take ? `${take.seconds.toFixed(1)} s` : "";
      audio.hidden = !take || !!this.session;
      if (take && audio.getAttribute("src") !== take.url) audio.src = take.url;
      if (!take && audio.hasAttribute("src")) audio.removeAttribute("src");
    });
    this.form.querySelector("[type=submit]").disabled =
      this.saving || !!this.session || this.takes.some((take) => !take);
  }

  status(text, error = false) {
    const element = this.shadowRoot.querySelector(".status");
    element.textContent = text;
    element.toggleAttribute("data-error", error);
  }

  async record(index) {
    if (this.session || this.saving || this.unavailable) return;
    this.rows.forEach((row) => row.querySelector("audio").pause());
    this.status("");
    const session = { index, phase: "waiting", chunks: [] };
    this.session = session;
    this.update();
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      if (this.session !== session) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      session.stream = stream;
      const recorder = new MediaRecorder(stream);
      session.recorder = recorder;
      recorder.ondataavailable = ({ data }) => {
        if (data.size) session.chunks.push(data);
      };
      recorder.onstop = () => this.finish(session);
      recorder.onerror = () => this.captureFailed(session);
      recorder.start();
      session.phase = "recording";
      session.timer = setTimeout(() => this.stop(), this.settings.seconds * 1000);
      this.update();
    } catch (error) {
      if (this.session !== session) return;
      this.release(session);
      this.status(error.name === "NotAllowedError"
        ? "Allow microphone access in your browser and try again."
        : "Could not start the microphone. Try your browser’s microphone settings or upload WAV files.", true);
      this.update();
    }
  }

  stop() {
    const session = this.session;
    if (session?.phase !== "recording") return;
    session.phase = "stopping";
    clearTimeout(session.timer);
    session.timer = setTimeout(() => this.captureFailed(session), 2000);
    if (session.recorder.state !== "inactive") session.recorder.stop();
    this.update();
  }

  captureFailed(session) {
    if (this.session !== session) return;
    this.release(session);
    this.status("The microphone recording stopped unexpectedly. Retake the sample or upload a WAV file.", true);
    this.update();
  }

  async finish(session) {
    if (this.session !== session) return;
    const label = `Sample ${session.index + 1}`;
    const blob = new Blob(session.chunks, { type: session.recorder.mimeType });
    this.stopCapture(session);
    session.phase = "processing";
    this.update();
    try {
      if (!blob.size) {
        this.status(`${label} captured no audio. Check microphone access and retake it.`, true);
        return;
      }
      const decoder = new OfflineAudioContext(1, 1, SAMPLE_RATE);
      const decoded = await decoder.decodeAudioData(await blob.arrayBuffer());
      if (this.session !== session) return;
      const { samples, level } = strongestChannel(decoded, this.settings.seconds * SAMPLE_RATE);
      if (!samples || samples.length < 2 * SAMPLE_RATE) {
        this.status(`${label} was too short. Read the whole line before stopping.`, true);
      } else if (level < MIN_SIGNAL_RMS) {
        this.status(`${label} was silent or too quiet. Check your microphone and retake it.`, true);
      } else {
        const bytes = encodeWav(samples);
        const previous = this.takes[session.index];
        if (previous) URL.revokeObjectURL(previous.url);
        this.takes[session.index] = {
          bytes,
          url: URL.createObjectURL(new Blob([bytes], { type: "audio/wav" })),
          seconds: samples.length / SAMPLE_RATE,
        };
        this.status(`${label} recorded. You can listen back before saving.`);
      }
    } catch {
      if (this.session === session) {
        this.status(`Could not read ${label.toLowerCase()}. Try opening Home Assistant in Chrome or upload a WAV file.`, true);
      }
    } finally {
      if (this.session === session) {
        this.session = null;
        this.update();
      }
    }
  }

  stopCapture(session) {
    clearTimeout(session.timer);
    const recorder = session.recorder;
    if (recorder) {
      recorder.ondataavailable = null;
      recorder.onstop = null;
      recorder.onerror = null;
      if (recorder.state !== "inactive") recorder.stop();
      session.recorder = null;
    }
    session.stream?.getTracks().forEach((track) => track.stop());
    session.stream = null;
    session.chunks.length = 0;
  }

  release(session) {
    this.session = null;
    this.stopCapture(session);
  }

  clearTakes() {
    for (const take of this.takes) {
      if (take) URL.revokeObjectURL(take.url);
    }
    this.takes.fill(null);
  }

  async save() {
    if (this.saving || this.session || this.takes.some((take) => !take)) return;
    this.saving = true;
    this.status("Checking recordings and saving the profile…");
    this.update();
    try {
      const result = await this.homeAssistant.callWS({
        type: "sentient_voice_recognition/enroll_recordings",
        name: this.form.elements.name.value.trim(),
        consent: this.form.elements.consent.checked,
        recordings: this.takes.map(({ bytes }) => encodeBase64(bytes)),
      });
      if (result.error) {
        const label = Number.isInteger(result.sample_index) ? `Sample ${result.sample_index + 1}. ` : "";
        this.status(label + (MESSAGES[result.error] || "Could not enrol this profile."), true);
      } else {
        this.clearTakes();
        this.form.reset();
        this.status("Voice profile saved. You can enrol another person or return to the integration.");
      }
    } catch {
      this.status("Could not reach Home Assistant. Check the connection and try saving again.", true);
    } finally {
      this.saving = false;
      this.update();
    }
  }
}

if (!customElements.get("sentient-voice-enrollment")) {
  customElements.define("sentient-voice-enrollment", SentientVoiceEnrollment);
}
